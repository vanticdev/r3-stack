"""Release gate: the requirement evidence matrix.

For every requirement in ``robot.yaml`` (``requirements:``), every scenario
expectation (``simulation.scenarios[*].expect``) and every pipeline stage, the
gate finds the latest evidence under ``build/`` and classifies it:

* ``verified``  a stage checked it against the limit, it passed, and the
  inputs were measured, datasheet or simulated values;
* ``estimated`` it passed, but the evidence rests on estimates (catalog
  envelope masses, fallback solvers, estimated geometry or parameters);
* ``failed``    fresh evidence violates the limit;
* ``stale``     the evidence was produced from other inputs than the current
  ones (``robot.yaml`` digest or a pinned ``*_revision`` differs);
* ``not_run``   no stage has produced evidence yet.

``fabrication_ready`` is true only when every item is ``verified``. The same
payload backs ``r3 status --release``, the MCP tool ``r3_release_status`` and
``GET /api/projects/{pid}`` (``release``).

Staleness compares ``robot_yaml_sha256`` and the ``*_revision`` keys of
``Project.fingerprint()``. The git commit is deliberately ignored: committing
an unrelated file does not invalidate a simulation.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from r3_model.diagnostics import StageResult
from r3_model.project import Project

from . import stages as stage_runner

SCHEMA = "r3/release@1"
STATUSES = ("verified", "estimated", "failed", "stale", "not_run")


@dataclass
class Evidence:
    """One check backing a requirement. ``value`` and ``limit`` are SI numbers."""

    stage: str
    check: str
    passed: bool | None
    value: Any = None
    limit: Any = None
    artifact: str | None = None
    inputs: dict[str, str] = field(default_factory=dict)
    estimated: bool = False
    stale: bool = False
    detail: str = ""


@dataclass
class Item:
    id: str
    kind: str  # requirement | scenario_expectation | stage
    subject: str
    target: Any
    status: str = "not_run"
    evidence: list[Evidence] = field(default_factory=list)
    message: str = ""


# ------------------------------------------------------------------ helpers
def is_stale(inputs: dict[str, str] | None, current: dict[str, str]) -> bool:
    if not inputs:
        return True
    keys = ["robot_yaml_sha256", *(k for k in current if k.endswith("_revision"))]
    return any(inputs.get(key) != current.get(key) for key in keys if key in current)


def _status(evidence: list[Evidence]) -> str:
    if not evidence:
        return "not_run"
    fresh = [e for e in evidence if not e.stale]
    if any(e.passed is False for e in fresh):
        return "failed"
    if len(fresh) < len(evidence):
        return "stale"
    if any(e.passed is None for e in fresh):
        return "not_run"
    if any(e.estimated for e in fresh):
        return "estimated"
    return "verified"


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


class _Context:
    """Latest results and artifacts of one project, read once."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.build = project.root / "build"
        self.current = project.fingerprint()
        self.results: dict[str, StageResult] = stage_runner.load_results(project)
        self.scenarios: dict[str, dict[str, Any]] = {}
        for path in sorted(self.build.glob("sim/*/summary.json")):
            data = _read_json(path)
            if isinstance(data, dict):
                self.scenarios[str(data.get("scenario") or path.parent.name)] = {**data, "_path": str(path.relative_to(self.build))}

    def result(self, stage: str) -> StageResult | None:
        return self.results.get(stage)

    def stale(self, inputs: dict[str, str] | None) -> bool:
        return is_stale(inputs, self.current)

    def codes(self, stage: str) -> set[str]:
        result = self.result(stage)
        return {d.code for d in result.diagnostics} if result else set()

    def stage_evidence(self, stage: str, check: str, passed: bool | None, value: Any, limit: Any, artifact: str | None,
                       estimated: bool = False, detail: str = "") -> Evidence:
        result = self.result(stage)
        inputs = result.inputs if result else {}
        return Evidence(stage, check, passed, value, limit, artifact, dict(inputs), estimated, self.stale(inputs), detail)

    def scenario_checks(self, key: str) -> list[Evidence]:
        """Evidence from every scenario that evaluated expectation ``key``."""
        out = []
        for scenario_id, summary in self.scenarios.items():
            check = _find_check(summary, key)
            if check is not None:
                out.append(self.scenario_evidence(scenario_id, summary, key, check))
        return out

    def scenario_evidence(self, scenario_id: str, summary: dict[str, Any], key: str, check: dict[str, Any]) -> Evidence:
        inputs = summary.get("inputs") or {}
        fallbacks = summary.get("fallbacks") or {}
        estimated = [name for name, used in fallbacks.items() if used]
        passed = check.get("pass", check.get("passed"))
        return Evidence("sim", f"{scenario_id}.{key}", None if passed is None else bool(passed),
                        check.get("measured", check.get("value")), check.get("expected", check.get("limit")), summary["_path"],
                        dict(inputs), bool(estimated), self.stale(inputs),
                        f"fallback models: {', '.join(estimated)}" if estimated else str(check.get("detail", "")))


def _find_check(summary: dict[str, Any], key: str) -> dict[str, Any] | None:
    checks = summary.get("checks")
    if isinstance(checks, list):
        return next((c for c in checks if isinstance(c, dict) and c.get("key") == key), None)
    if isinstance(checks, dict) and isinstance(checks.get(key), dict):
        return checks[key]
    return None


# ------------------------------------------------------------- requirements
def _max_mass(ctx: _Context, limit: float) -> list[Evidence]:
    model = ctx.result("model")
    if model is None or "total_mass" not in model.metrics:
        return []
    graph = _read_json(ctx.build / "model" / "graph.json") or {}
    estimated_fraction = float((graph.get("mass") or {}).get("estimated_fraction") or 0.0)
    total = float(model.metrics["total_mass"])
    return [ctx.stage_evidence("model", "total_mass", total <= limit, total, limit, "model/graph.json", estimated_fraction > 0,
                               f"{estimated_fraction:.0%} of the mass is estimated" if estimated_fraction else "")]


def _top_speed(ctx: _Context, limit: float) -> list[Evidence]:
    evidence = ctx.scenario_checks("min_top_speed_mps")
    if evidence:
        return evidence
    out = []
    for scenario_id, summary in ctx.scenarios.items():
        speed = _finite((summary.get("metrics") or {}).get("top_speed_mps"))
        if speed is not None:
            check = {"pass": speed >= limit, "measured": speed, "expected": limit}
            out.append(ctx.scenario_evidence(scenario_id, summary, "top_speed_mps", check))
    best = max(out, key=lambda e: e.value, default=None)
    return [best] if best else []


def _min_runtime(ctx: _Context, limit: float) -> list[Evidence]:
    electrical = ctx.result("electrical")
    runtime = _finite(electrical.metrics.get("runtime")) if electrical else None
    if runtime is None:
        return []
    points = _read_json(ctx.build / "electrical" / "operating_points.json") or {}
    vehicle = points.get("vehicle") or {}
    estimated = vehicle.get("mass_source") == "estimate" or "battery" in (points.get("estimated_parameters") or {})
    return [ctx.stage_evidence("electrical", "runtime", runtime >= limit, runtime, limit, "electrical/operating_points.json", estimated,
                               "duty-cycle runtime with estimated vehicle mass or battery parameters" if estimated else "")]


def _radio_range(ctx: _Context, limit: float) -> list[Evidence]:
    return ctx.scenario_checks("link_lost_beyond_m")


STOP_DISTANCE_CHECKS = ("stop_within_m_of_link_loss", "stop_within_m_of_fault")


def _stop_checks(ctx: _Context, limit: float) -> list[Evidence]:
    """Stop-distance checks of the scenarios that test this requirement: those whose expected stop distance
    equals it (a scenario at top speed tests ``failsafe_stop_distance_at_top_speed``, one at the autonomous
    cruise speed ``failsafe_stop_distance``)."""
    out = []
    for key in STOP_DISTANCE_CHECKS:
        for evidence in ctx.scenario_checks(key):
            limit_value = _finite(evidence.limit)
            if limit_value is not None and abs(limit_value - limit) <= 1e-6:
                out.append(evidence)
    return out


def _failsafe_stop(ctx: _Context, limit: float) -> list[Evidence]:
    return _stop_checks(ctx, limit)


def _failsafe_stop_top_speed(ctx: _Context, limit: float) -> list[Evidence]:
    return _stop_checks(ctx, limit)


def _voltage_drop(ctx: _Context, limit: float) -> list[Evidence]:
    electrical = ctx.result("electrical")
    if electrical is None or stage_runner.status_of(electrical) == "unavailable":
        return []
    drops = [d for d in electrical.diagnostics if d.code == "voltage_drop_exceeded"]
    worst = max((_finite(d.data.get("fraction")) or 0.0 for d in drops), default=None)
    points = _read_json(ctx.build / "electrical" / "operating_points.json") or {}
    estimated_lengths = [w for w, info in (points.get("wire_lengths") or {}).items() if (info or {}).get("source") != "routed"]
    return [ctx.stage_evidence("electrical", "voltage_drop_fraction", not drops, worst, limit, "electrical/operating_points.json",
                               bool(estimated_lengths),
                               f"{len(drops)} harness(es) exceed the limit" if drops
                               else (f"{len(estimated_lengths)} wire length(s) estimated" if estimated_lengths else "no harness exceeds the limit"))]


def _wire_clearance(ctx: _Context, limit: float) -> list[Evidence]:
    out = []
    wiring = ctx.result("wiring")
    clearances = (wiring.metrics.get("wire_min_clearance_moving") or {}) if wiring else {}
    values = [v for v in (_finite(x) for x in clearances.values()) if v is not None]
    if values:
        worst = min(values)
        out.append(ctx.stage_evidence("wiring", "wire_min_clearance_moving", worst >= limit, worst, limit, "wiring/routes.json",
                                      "cad_geometry_estimated" in ctx.codes("wiring"),
                                      "clearances use catalog envelopes, not CAD meshes" if "cad_geometry_estimated" in ctx.codes("wiring") else ""))
    out.extend(ctx.scenario_checks("min_wire_clearance_m"))
    return out


def _temperature(ctx: _Context, limit: float) -> list[Evidence]:
    points = _read_json(ctx.build / "electrical" / "operating_points.json")
    if not points or ctx.result("electrical") is None:
        return []
    temperatures = {}
    for component, info in (points.get("thermal") or {}).items():
        if isinstance(info, dict):
            value = _finite(info.get("temperature_c", info.get("steady_temperature_c")))
            if value is not None:
                temperatures[component] = value
    if not temperatures:
        return []
    hottest = max(temperatures, key=temperatures.get)
    estimated = bool(points.get("estimated_parameters", {}).get(hottest))
    return [ctx.stage_evidence("electrical", f"temperature_c.{hottest}", temperatures[hottest] <= limit, temperatures[hottest], limit,
                               "electrical/operating_points.json", estimated,
                               f"hottest component {hottest}" + (" (estimated thermal parameters)" if estimated else ""))]


REQUIREMENT_CHECKS: dict[str, Callable[[_Context, float], list[Evidence]]] = {
    "max_mass": _max_mass,
    "top_speed": _top_speed,
    "min_runtime": _min_runtime,
    "radio_range": _radio_range,
    "failsafe_stop_distance": _failsafe_stop,
    "failsafe_stop_distance_at_top_speed": _failsafe_stop_top_speed,
    "max_wire_voltage_drop_fraction": _voltage_drop,
    "min_wire_clearance_moving": _wire_clearance,
    "max_component_temperature_c": _temperature,
}


# --------------------------------------------------------------------- gate
def _requirement_items(ctx: _Context) -> list[Item]:
    requirements = ctx.project.spec.requirements
    items = []
    for name, value in requirements.model_dump(mode="json").items():
        if name == "extra" or value is None:
            continue
        check = REQUIREMENT_CHECKS.get(name)
        item = Item(f"requirement.{name}", "requirement", name, value)
        item.evidence = check(ctx, float(value)) if check else []
        if check is None:
            item.message = "no stage checks this requirement yet"
        items.append(item)
    for name, value in (requirements.extra or {}).items():
        items.append(Item(f"requirement.extra.{name}", "requirement", name, value,
                          message="custom requirement: no stage checks it automatically"))
    return items


def _scenario_items(ctx: _Context) -> list[Item]:
    items = []
    for scenario in ctx.project.spec.simulation.scenarios:
        summary = ctx.scenarios.get(scenario.id)
        for key, expected in (scenario.expect or {}).items():
            item = Item(f"scenario.{scenario.id}.{key}", "scenario_expectation", scenario.id, expected)
            check = _find_check(summary, key) if summary else None
            if check is not None:
                item.evidence = [ctx.scenario_evidence(scenario.id, summary, key, check)]
            elif summary is not None:
                item.message = f"scenario {scenario.id} ran but did not evaluate {key}"
            items.append(item)
    return items


def _stage_items(ctx: _Context) -> list[Item]:
    items = []
    for name in stage_runner.stage_names():
        result = ctx.result(name)
        item = Item(f"stage.{name}", "stage", name, "no errors")
        if result is None:
            item.message = "stage has not run"
        elif stage_runner.status_of(result) == "unavailable":
            item.message = "stage is not installed here"
            item.evidence = [ctx.stage_evidence(name, "stage_unavailable", None, None, None, f"{name}/result.json")]
        else:
            counts = stage_runner.counts(result.diagnostics)
            estimates = sorted({d.code for d in result.diagnostics if d.code.endswith("_estimated")})
            item.evidence = [ctx.stage_evidence(name, "errors", result.ok, counts["error"], 0, f"{name}/result.json", bool(estimates),
                                                f"estimates: {', '.join(estimates)}" if estimates else "")]
        items.append(item)
    return items


def evaluate(project: Project) -> dict[str, Any]:
    """The requirement evidence matrix and ``fabrication_ready`` for ``project``."""
    ctx = _Context(project)
    items = [*_requirement_items(ctx), *_scenario_items(ctx), *_stage_items(ctx)]
    for item in items:
        item.status = _status(item.evidence)
        if not item.message and item.evidence:
            item.message = "; ".join(e.detail for e in item.evidence if e.detail)
    counts = {status: sum(1 for i in items if i.status == status) for status in STATUSES}
    return {
        "schema": SCHEMA,
        "ok": True,
        "project": str(project.root),
        "inputs": ctx.current,
        "fabrication_ready": bool(items) and counts["verified"] == len(items),
        "counts": counts,
        "blocking": [i.id for i in items if i.status != "verified"],
        "items": [asdict(i) for i in items],
    }


def print_release(payload: dict[str, Any]) -> None:
    marks = {"verified": "ok ", "estimated": "est", "failed": "ERR", "stale": "old", "not_run": "-- "}
    for item in payload["items"]:
        evidence = item["evidence"][0] if item["evidence"] else None
        value = "" if evidence is None or evidence["value"] is None else f" = {evidence['value']:.4g}" \
            if isinstance(evidence["value"], (int, float)) and not isinstance(evidence["value"], bool) else f" = {evidence['value']}"
        print(f"  {marks[item['status']]} {item['id']:<52} target {item['target']}{value}")
    c = payload["counts"]
    print(f"{'FABRICATION READY' if payload['fabrication_ready'] else 'NOT READY'}: "
          + ", ".join(f"{c[s]} {s}" for s in STATUSES))
