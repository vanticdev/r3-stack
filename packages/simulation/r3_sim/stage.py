"""Pipeline stage ``sim``: run every scenario in ``simulation.scenarios``."""

from __future__ import annotations

import json
import time
from typing import Callable

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .scenario import ScenarioOutcome, run_scenario

STAGE = "sim"


def run(project: Project, scenarios: list[str] | None = None, software_cmd: list[str] | None = None, render: bool = True,
        progress: Callable[[str], None] | None = None) -> StageResult:
    """Run the scenarios (all by default) and aggregate diagnostics, metrics and artifacts."""
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    started = time.monotonic()
    selected = [s for s in project.spec.simulation.scenarios if scenarios is None or s.id in scenarios]
    if not selected:
        result.add(Diagnostic.warning("no_scenarios", "robot.yaml defines no matching simulation scenarios",
                                      data={"requested": len(scenarios or [])}))
    seen_diagnostics: set[tuple[str, tuple[str, ...]]] = set()
    seen_assumptions: set[tuple[str, str]] = set()
    outcomes: list[ScenarioOutcome] = []
    for scenario in selected:
        if progress:
            progress(f"scenario {scenario.id} ({scenario.duration:.0f} s)")
        outcome = run_scenario(project, scenario.id, software_cmd, render=render, progress=progress)
        outcomes.append(outcome)
        for diagnostic in outcome.diagnostics:
            key = (diagnostic.code, tuple(diagnostic.subjects))
            if key not in seen_diagnostics:
                seen_diagnostics.add(key)
                result.add(diagnostic)
        for domain, statement in outcome.assumptions:
            if (domain, statement) not in seen_assumptions:
                seen_assumptions.add((domain, statement))
                result.assume(domain, statement)
        for path, kind, description in outcome.artifacts:
            result.artifact(path, kind, f"{scenario.id}: {description}")
        result.metrics[scenario.id] = {"passed": outcome.passed, **{k: v for k, v in outcome.summary["metrics"].items()
                                                                     if not isinstance(v, dict)}}
    result.metrics["scenarios_passed"] = sum(o.passed for o in outcomes)
    result.metrics["scenarios_total"] = len(outcomes)
    result.ok = result.ok and all(o.passed for o in outcomes)
    result.duration_s = time.monotonic() - started
    out = project.build_dir(STAGE)
    if outcomes:
        from .plots import plot_overview

        overview = plot_overview(outcomes, out / "overview.png")
        result.artifact(str(overview.relative_to(project.root / "build")), "plot_png", "pass/fail overview of all scenarios")
    result.write(out / "result.json")
    (out / "scenarios.json").write_text(json.dumps({o.scenario: {"passed": o.passed, "checks": o.summary["checks"]}
                                                    for o in outcomes}, indent=2, default=float))
    return result


__all__ = ["run", "STAGE"]
