"""Sourcing searches for the HTTP API, run as background jobs streamed over SSE.

``POST /api/projects/{pid}/sourcing/{component}/search`` starts a job in a
thread (provider calls are network-bound; the twin checks are the model
stage's in-process checks). One search per slot at a time. Events:

``job_started``  ``{job_id, project, slot}``
``search_started``  ``{slot, requires, components}``
``provider_started`` / ``provider_skipped`` / ``provider_finished`` / ``provider_failed``  provider report
``ranking_started``  ``{candidates, template}``
``candidate``  ``{rank, tier, score, id, name, provider, unit_price, currency, twin_errors, twin_warnings}``
``search_finished``  ``{slot, counts, best}``
``job_finished``  ``{status: succeeded|failed, counts?, error?}``
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .errors import ApiError
from .events import EventLog


@dataclass
class SourcingJob:
    id: str
    project_id: str
    root: Path
    slot: str
    providers: list[str] | None
    status: str = "running"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: EventLog = field(default_factory=EventLog)

    def to_json(self) -> dict[str, Any]:
        return {"job_id": self.id, "project": self.project_id, "slot": self.slot, "providers": self.providers, "status": self.status,
                "created_at": self.created_at, "finished_at": self.finished_at, "error": self.error, "events": len(self.events),
                "counts": (self.result or {}).get("counts")}


class SourcingJobs:
    def __init__(self, http_factory: Callable[[], Any] | None = None) -> None:
        #: Builds the HttpClient for each search (tests pass an offline client with fixtures).
        self.http_factory = http_factory
        self._jobs: dict[str, SourcingJob] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> SourcingJob | None:
        return self._jobs.get(job_id)

    def list(self, project_id: str | None = None) -> list[SourcingJob]:
        return sorted((j for j in self._jobs.values() if project_id in (None, j.project_id)), key=lambda j: j.created_at)

    def start(self, project_id: str, root: Path, component: str, providers: list[str] | None = None,
              offline: bool = False) -> SourcingJob:
        from r3_model.project import Project

        from .. import sourcing

        try:
            slot = sourcing.requirement_for(Project.load(root), component).slot
        except sourcing.SourcingError as error:
            raise ApiError("sourcing_slot_not_found", str(error).strip("'\""), {"component": component}, status=404) from error
        with self._lock:
            busy = next((j for j in self._jobs.values() if j.project_id == project_id and j.slot == slot and j.status == "running"), None)
            if busy is not None:
                raise ApiError("sourcing_busy", f"search {busy.id} for {slot} is still running", {"job_id": busy.id}, status=409)
            job = SourcingJob(uuid.uuid4().hex[:12], project_id, Path(root), slot, providers or None)
            self._jobs[job.id] = job
        job.events.emit("job_started", job_id=job.id, project=project_id, slot=slot, providers=job.providers)
        threading.Thread(target=self._run, args=(job, offline), daemon=True, name=f"r3-source-{job.id}").start()
        return job

    def _run(self, job: SourcingJob, offline: bool) -> None:
        from r3_catalog.sourcing import HttpClient
        from r3_model.project import Project

        from .. import sourcing

        def progress(event: str, payload: dict[str, Any]) -> None:
            job.events.emit(event, job_id=job.id, **payload)

        try:
            http = self.http_factory() if self.http_factory else HttpClient(offline=True) if offline else None
            payload = sourcing.search_slot(Project.load(job.root), job.slot, provider_ids=job.providers, http=http, progress=progress)
            job.result = payload
            job.status = "succeeded"
            job.events.emit("job_finished", job_id=job.id, status="succeeded", counts=payload["counts"])
        except Exception as error:  # noqa: BLE001 - surface every failure as a job error event
            job.status, job.error = "failed", f"{type(error).__name__}: {error}"
            job.events.emit("job_finished", job_id=job.id, status="failed", error=job.error, traceback=traceback.format_exc(limit=6))
        finally:
            job.finished_at = time.time()
            job.events.close()
