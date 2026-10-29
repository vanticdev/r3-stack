"""Stage registry and runner.

Stages are imported lazily so that a missing optional package (or one still
being written by another team) degrades to a ``stage_unavailable`` warning
instead of breaking ``r3 validate``. Each result is persisted to
``build/<stage>/result.json`` so later commands (report, diagnose, status, the
MCP server, CI) read the same state.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import json
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from r3_model.diagnostics import Diagnostic, Severity, StageResult
from r3_model.project import Project

RESULT_FILE = "result.json"


@dataclass(frozen=True)
class StageSpec:
    name: str
    module: str
    description: str


STAGES: tuple[StageSpec, ...] = (
    StageSpec("assembly", "r3_orchestrator.assembly_stage", "Object/interface assembly: derived poses, fits, hole patterns, loop closure"),
    StageSpec("model", "r3_orchestrator.model_stage", "Spec, catalog and graph checks (bindings, pins, voltages, I2C, interference, mass)"),
    StageSpec("cad", "r3_onshape.stage", "Onshape CAD sync, mass properties and meshes"),
    StageSpec("pcb", "r3_kicad.stage", "KiCad schematic/PCB generation, ERC/DRC"),
    StageSpec("wiring", "r3_wiring.stage", "3D cable routing, harness checks and slack"),
    StageSpec("electrical", "r3_sim.electrical.stage", "Static worst-case electrical operating points"),
    StageSpec("sim", "r3_sim.stage", "Multi-domain digital twin scenarios"),
)
REPORT = StageSpec("report", "r3_orchestrator.report_stage", "HTML report and summary.json")
ALL_STAGES: dict[str, StageSpec] = {s.name: s for s in (*STAGES, REPORT)}


def stage_names() -> list[str]:
    return [s.name for s in STAGES]


def _unavailable(stage: StageSpec, project: Project, error: ModuleNotFoundError) -> StageResult:
    result = StageResult(stage=stage.name, inputs=project.fingerprint())
    missing = error.name or str(error)
    own = stage.module == missing or stage.module.startswith(f"{missing}.")
    result.add(Diagnostic.warning(
        "stage_unavailable",
        f"stage {stage.name}: module {stage.module} is not installed" if own
        else f"stage {stage.name}: {stage.module} needs missing dependency {missing}",
        subjects=[stage.name], data={"module": stage.module, "missing": missing},
        hint="Install the package that provides this stage (see its README) or select other stages with --stage."))
    return result


@contextlib.contextmanager
def _env(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _call(run: Any, project: Project, options: dict[str, Any]) -> StageResult:
    """Pass only the options a stage's ``run`` accepts. Scenario selection is
    also exported as ``R3_SCENARIOS`` for stages with a plain ``run(project)``."""
    parameters = inspect.signature(run).parameters
    accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    kwargs = {k: v for k, v in options.items() if v is not None and (accepts_any or k in parameters)}
    scenarios = options.get("scenarios")
    if scenarios and "scenarios" not in kwargs and "scenario" in parameters and len(scenarios) == 1:
        kwargs["scenario"] = scenarios[0]
    env = {"R3_SCENARIOS": ",".join(scenarios)} if scenarios else {}
    with _env(env):
        return run(project, **kwargs)


def run_stage(project: Project, name: str, **options: Any) -> StageResult:
    """Run one stage and persist its result. Never raises for stage failures."""
    stage = ALL_STAGES.get(name)
    if stage is None:
        raise KeyError(f"unknown stage {name!r}; known: {', '.join(ALL_STAGES)}")
    started = time.time()
    try:
        module = importlib.import_module(stage.module)
    except ModuleNotFoundError as error:
        result = _unavailable(stage, project, error)
    else:
        try:
            result = _call(module.run, project, options)
            if not isinstance(result, StageResult):
                result = StageResult.model_validate(result)
        except Exception as error:  # a crashing stage must not take down the pipeline
            result = StageResult(stage=name, inputs=project.fingerprint())
            result.add(Diagnostic.error(
                "stage_crashed", f"stage {name} raised {type(error).__name__}: {error}", subjects=[name],
                data={"exception": type(error).__name__}, hint=traceback.format_exc(limit=6)))
    result.stage = result.stage or name
    for diagnostic in result.diagnostics:
        diagnostic.stage = diagnostic.stage or result.stage
    result.ok = not result.errors
    if not result.inputs:
        result.inputs = project.fingerprint()
    if not result.duration_s:
        result.duration_s = time.time() - started
    result.write(result_path(project, name))
    return result


def result_path(project: Project, name: str) -> Path:
    return project.root / "build" / name / RESULT_FILE


def load_results(project: Project) -> dict[str, StageResult]:
    results = {}
    for name in ALL_STAGES:
        path = result_path(project, name)
        if path.exists():
            try:
                results[name] = StageResult.model_validate(json.loads(path.read_text()))
            except (ValueError, json.JSONDecodeError):
                continue
    return results


#: ``listener(stage, result)`` is called with ``result=None`` before a stage runs and with its result after.
StageListener = Callable[[str, "StageResult | None"], None]


def plan(stages: list[str] | None = None, report: bool = True) -> list[str]:
    """Stage names ``validate`` would run, in pipeline order."""
    wanted = stages or stage_names()
    unknown = [s for s in wanted if s not in ALL_STAGES]
    if unknown:
        raise KeyError(f"unknown stage(s): {', '.join(unknown)}; known: {', '.join(ALL_STAGES)}")
    return [s.name for s in STAGES if s.name in wanted] + (["report"] if report or "report" in wanted else [])


def validate(project: Project, stages: list[str] | None = None, report: bool = True, listener: StageListener | None = None,
             **options: Any) -> list[StageResult]:
    """Run the requested stages (default: all) in pipeline order, then the report."""
    results = []
    for name in plan(stages, report):
        if listener:
            listener(name, None)
        result = run_stage(project, name, **(options if name != "report" else {}))
        if listener:
            listener(name, result)
        results.append(result)
    return results


def counts(diagnostics: list[Diagnostic]) -> dict[str, int]:
    tally = {severity.value: 0 for severity in Severity}
    for diagnostic in diagnostics:
        tally[diagnostic.severity.value] += 1
    return tally


def status_of(result: StageResult | None) -> str:
    if result is None:
        return "missing"
    if any(d.code == "stage_unavailable" for d in result.diagnostics):
        return "unavailable"
    return "ok" if result.ok else "failed"
