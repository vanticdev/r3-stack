"""Worker entry for the ``sim`` stage (delegates to :func:`r3_sim.stage.run`)."""

from __future__ import annotations

from r3_model.diagnostics import StageResult
from r3_model.project import Project


def run(project: Project, scenarios: list[str] | None = None, software_cmd: list[str] | None = None,
        render: bool = True) -> StageResult:
    from r3_sim.stage import run as run_sim

    return run_sim(project, scenarios=scenarios, software_cmd=software_cmd, render=render, progress=None)
