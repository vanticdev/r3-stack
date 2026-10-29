"""Pipeline runs for the HTTP API: worker processes, one at a time per project.

``POST /api/projects/{pid}/runs`` queues a run. A per-project single-thread
executor starts each run in a fresh (``spawn``) worker process, so a crashing
or hanging stage, MuJoCo's GL state or ngspice's shared library never touch
the server process, and runs of one project never overlap (they write the
same ``build/``). Runs of different projects proceed in parallel.

The worker reports through a multiprocessing queue; the parent turns each
message into an event on the run's ``EventLog``:

``run_started``  ``{run_id, project, stages, scenarios}``
``stage_started``  ``{stage, index, total}``
``log``  ``{stage, stream: stdout|stderr|logging, line}``
``progress``  ``{stage, message}`` (stages that accept a ``progress`` callback, e.g. sim)
``diagnostic``  ``{stage, diagnostic: {code, severity, message, subjects, data, hint}}``
``stage_finished``  ``{stage, status, ok, stale, counts, duration_s, metrics, artifacts}``
``run_finished``  ``{status: succeeded|failed|cancelled|error, ok, counts, stages, release, error?}``
"""

from __future__ import annotations

import io
import logging
import multiprocessing
import queue as queue_module
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import EventLog

TERMINAL = ("succeeded", "failed", "cancelled", "error")
_DONE = "__done__"


# ------------------------------------------------------------ worker process
class _QueueWriter(io.TextIOBase):
    """File-like object turning written text into ``log`` messages, line by line."""

    def __init__(self, send: Any, stream: str, state: dict[str, Any]) -> None:
        self._send, self._stream, self._state, self._buffer = send, stream, state, ""

    def write(self, text: str) -> int:
        self._buffer += text
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            if line.strip():
                self._send("log", stage=self._state.get("stage"), stream=self._stream, line=line[:4000])
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._send("log", stage=self._state.get("stage"), stream=self._stream, line=self._buffer[:4000])
        self._buffer = ""


class _QueueLogHandler(logging.Handler):
    def __init__(self, send: Any, state: dict[str, Any]) -> None:
        super().__init__(logging.INFO)
        self._send, self._state = send, state

    def emit(self, record: logging.LogRecord) -> None:
        self._send("log", stage=self._state.get("stage"), stream="logging", line=self.format(record)[:4000], level=record.levelname)


def _apply_stage_overrides(overrides: dict[str, str]) -> None:
    """Point stages at alternative modules (tests, experimental stage implementations)."""
    import dataclasses

    from .. import stages as stage_runner

    if not overrides:
        return
    stage_runner.STAGES = tuple(dataclasses.replace(s, module=overrides.get(s.name, s.module)) for s in stage_runner.STAGES)
    report = dataclasses.replace(stage_runner.REPORT, module=overrides.get("report", stage_runner.REPORT.module))
    stage_runner.REPORT = report
    stage_runner.ALL_STAGES = {s.name: s for s in (*stage_runner.STAGES, report)}


def run_worker(root: str, stages: list[str] | None, scenarios: list[str] | None, overrides: dict[str, str], out: Any) -> None:
    """Worker-process entry point: run the pipeline and report through ``out``."""

    def send(event_type: str, **payload: Any) -> None:
        out.put((event_type, payload))

    state: dict[str, Any] = {"stage": None}
    sys.stdout = _QueueWriter(send, "stdout", state)
    sys.stderr = _QueueWriter(send, "stderr", state)
    handler = _QueueLogHandler(send, state)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    try:
        from .. import api, release
        from .. import stages as stage_runner
        from ..cli import configure_headless

        configure_headless()
        _apply_stage_overrides(overrides)
        project = api.load_project(root)
        names = stage_runner.plan(stages)
        fingerprint = project.fingerprint()

        def listener(name: str, result: Any) -> None:
            if result is None:
                state["stage"] = name
                send("stage_started", stage=name, index=names.index(name), total=len(names))
                return
            sys.stdout.flush()
            sys.stderr.flush()
            for diagnostic in result.diagnostics:
                send("diagnostic", stage=name, diagnostic=api._diag(diagnostic))
            send("stage_finished", **api._stage_summary(name, result, fingerprint))
            state["stage"] = None

        def progress(message: str) -> None:
            send("progress", stage=state.get("stage"), message=str(message)[:2000])

        results = stage_runner.validate(project, stages, listener=listener, scenarios=scenarios, progress=progress)
        payload = api._results_payload(project, results)
        gate = release.evaluate(project)
        send(_DONE, status="succeeded" if payload["ok"] else "failed", ok=payload["ok"], counts=payload["counts"],
             stages=payload["stages"], release={k: gate[k] for k in ("fabrication_ready", "counts", "blocking")})
    except BaseException as error:  # report everything, including KeyboardInterrupt from terminate()
        sys.stdout.flush()
        sys.stderr.flush()
        send(_DONE, status="error", ok=False, error={"type": type(error).__name__, "message": str(error),
                                                     "traceback": traceback.format_exc(limit=8)})
    finally:
        sys.stdout.flush()
        sys.stderr.flush()


# --------------------------------------------------------------- run manager
@dataclass
class Run:
    id: str
    project_id: str
    root: Path
    stages: list[str] | None
    scenarios: list[str] | None
    kind: str = "validate"
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    events: EventLog = field(default_factory=EventLog)
    cancel_requested: bool = False
    _process: Any = None
    _current_stage: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"run_id": self.id, "project": self.project_id, "kind": self.kind, "status": self.status,
                "stages": self.stages, "scenarios": self.scenarios, "current_stage": self._current_stage,
                "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at,
                "events": len(self.events), "result": self.result}


class RunManager:
    def __init__(self, stage_overrides: dict[str, str] | None = None, start_method: str = "spawn") -> None:
        self.stage_overrides = dict(stage_overrides or {})
        self._context = multiprocessing.get_context(start_method)
        self._runs: dict[str, Run] = {}
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._lock = threading.Lock()

    def submit(self, project_id: str, root: Path, stages: list[str] | None = None, scenarios: list[str] | None = None,
               kind: str = "validate") -> Run:
        from .. import stages as stage_runner

        stage_runner.plan(stages)  # KeyError for unknown stages before anything is queued
        run = Run(uuid.uuid4().hex[:12], project_id, Path(root), stages or None, scenarios or None, kind)
        with self._lock:
            self._runs[run.id] = run
            executor = self._executors.setdefault(project_id, ThreadPoolExecutor(1, thread_name_prefix=f"r3-run-{project_id}"))
        run.events.emit("run_queued", run_id=run.id, project=project_id, kind=kind, stages=run.stages, scenarios=run.scenarios)
        executor.submit(self._execute, run)
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self, project_id: str | None = None) -> list[Run]:
        return sorted((r for r in self._runs.values() if project_id in (None, r.project_id)), key=lambda r: r.created_at)

    def active(self, project_id: str) -> Run | None:
        return next((r for r in self.list(project_id) if r.status in ("queued", "running")), None)

    def latest(self, project_id: str) -> Run | None:
        runs = self.list(project_id)
        return runs[-1] if runs else None

    def cancel(self, run_id: str) -> Run | None:
        run = self._runs.get(run_id)
        if run is None or run.status in TERMINAL:
            return run
        run.cancel_requested = True
        process = run._process
        if process is not None and process.is_alive():
            process.terminate()
        return run

    def shutdown(self) -> None:
        for run in list(self._runs.values()):
            self.cancel(run.id)
        for executor in self._executors.values():
            executor.shutdown(wait=False, cancel_futures=True)

    # -------------------------------------------------------------- internals
    def _finish(self, run: Run, status: str, **payload: Any) -> None:
        run.status = status
        run.finished_at = time.time()
        run._current_stage = None
        run.result = {"status": status, **payload}
        run.events.emit("run_finished", run_id=run.id, status=status, **payload)
        run.events.close()

    def _execute(self, run: Run) -> None:
        if run.cancel_requested:
            self._finish(run, "cancelled", ok=False)
            return
        out = self._context.Queue()
        process = self._context.Process(target=run_worker, name=f"r3-run-{run.id}", daemon=True,
                                        args=(str(run.root), run.stages, run.scenarios, self.stage_overrides, out))
        run.status, run.started_at = "running", time.time()
        run.events.emit("run_started", run_id=run.id, project=run.project_id, stages=run.stages, scenarios=run.scenarios)
        try:
            process.start()
        except Exception as error:  # noqa: BLE001 - surface spawn failures as a run error
            self._finish(run, "error", ok=False, error={"type": type(error).__name__, "message": str(error)})
            return
        run._process = process
        final: dict[str, Any] | None = None
        while final is None:
            try:
                event_type, payload = out.get(timeout=0.2)
            except queue_module.Empty:
                if not process.is_alive():
                    # Drain anything flushed just before exit, then give up.
                    try:
                        event_type, payload = out.get(timeout=1.0)
                    except queue_module.Empty:
                        break
                else:
                    continue
            if event_type == _DONE:
                final = payload
                break
            if event_type == "stage_started":
                run._current_stage = payload.get("stage")
            run.events.emit(event_type, run_id=run.id, **payload)
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
        if run.cancel_requested:
            self._finish(run, "cancelled", ok=False)
        elif final is None:
            self._finish(run, "error", ok=False, error={"type": "WorkerExited", "message": f"worker exited with code {process.exitcode}"})
        else:
            status = final.pop("status")
            self._finish(run, status, **final)
