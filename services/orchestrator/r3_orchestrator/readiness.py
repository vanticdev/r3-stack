"""Stage readiness: what a stage needs in ``robot.yaml`` before it can check anything.

A robot grows component by component from a blank project, so most stages
first meet a partial spec. Instead of crashing or reporting misleading
errors, the runner asks :func:`precheck` first and records one of two info
diagnostics:

* ``not_configured``: the section the stage checks does not exist yet (no
  ``assembly:`` for CAD, no harness for wiring, no scenario for the twin). The
  release gate counts the stage as ``not_run``: nothing was verified.
* ``not_applicable``: the robot legitimately has nothing for this stage (no
  custom PCB board). The stage passes.

Each message says what to add next, so an agent can follow the hints.
"""

from __future__ import annotations

from typing import Callable

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

Check = Callable[[Project], tuple[str, str, str] | None]  # -> (code, message, hint) or None when ready


def _behavior_models(project: Project) -> list[str]:
    models = []
    for comp in project.spec.components.values():
        ctype = project.catalog.components.get(comp.type)
        if ctype is not None and ctype.behavior is not None:
            models.append(ctype.behavior.model)
    return models


def _unresolved(project: Project) -> list[str]:
    """Components whose catalog type is unknown (an unpinned `source:` slot or a typo)."""
    return [cid for cid, comp in project.spec.components.items() if comp.type not in project.catalog.components]


def _components(project: Project) -> tuple[str, str, str] | None:
    if not project.spec.components:
        return ("not_configured", "robot.yaml has no components yet",
                "Add components from the catalog (r3_list_catalog) with r3_patch_spec, e.g. "
                "{components: {pi: {type: raspberry-pi-5-8gb}}}.")
    missing = _unresolved(project)
    if missing:
        return ("not_configured", f"components without a resolved catalog type: {', '.join(missing)}",
                "Pin each `source:` slot with r3_source_search + r3_source_choose (or set `type`), then re-run.")
    return None


def _cad(project: Project) -> tuple[str, str, str] | None:
    # A populated description must check its pull, even before an assembly
    # root is selected. Missing CAD is an error, never a generation preflight.
    return _components(project) if not project.spec.components else None


def _pcb(project: Project) -> tuple[str, str, str] | None:
    if not project.spec.pcb.boards:
        return ("not_applicable", "robot.yaml declares no custom PCB boards (pcb.boards); nothing to build",
                "Robots built from off-the-shelf modules need no PCB. Add pcb.boards only for a custom KiCad board.")
    return None


def _wiring(project: Project) -> tuple[str, str, str] | None:
    if (found := _components(project)) is not None:
        return found
    if not any(h.wires for h in project.spec.electrical.harnesses):
        return ("not_configured", "robot.yaml has no harness wires yet (electrical.harnesses)",
                "Add a harness whose connectors mate component connectors and whose wires join their pins "
                "(electrical.harnesses[].connectors/wires); plug-in modules mate with electrical.direct_mates.")
    return None


def _electrical(project: Project) -> tuple[str, str, str] | None:
    if (found := _components(project)) is not None:
        return found
    if not any(model.startswith("battery") for model in _behavior_models(project)):
        return ("not_configured", "no power source: no component has a battery behaviour model",
                "Add a battery (e.g. r3_list_catalog kind=battery) and wire it to the loads.")
    if not project.spec.electrical.harnesses and not project.spec.electrical.direct_mates:
        return ("not_configured", "nothing is connected yet (no harnesses or direct mates)",
                "Connect the battery to the loads with electrical.harnesses (wires) or electrical.direct_mates.")
    return None


def _connectors(project: Project) -> tuple[str, str, str] | None:
    return _components(project)


def _sim(project: Project) -> tuple[str, str, str] | None:
    if (found := _components(project)) is not None:
        return found
    # Mechanical load cases apply to every robot, including passive assemblies
    # with no motor, wheels, software or authored driving mission.
    return None


def _assembly(project: Project) -> tuple[str, str, str] | None:
    return _components(project) if not project.spec.components else None


def _model(project: Project) -> tuple[str, str, str] | None:
    found = _components(project)
    return found if found is not None and not project.spec.components else None


CHECKS: dict[str, Check] = {"assembly": _assembly, "model": _model, "cad": _cad, "pcb": _pcb, "wiring": _wiring, "connectors": _connectors,
                            "electrical": _electrical, "sim": _sim}


def precheck(project: Project, stage: str) -> StageResult | None:
    """A finished ``StageResult`` when ``stage`` cannot check anything yet, else None (run it)."""
    check = CHECKS.get(stage)
    found = check(project) if check else None
    if found is None:
        return None
    code, message, hint = found
    result = StageResult(stage=stage, inputs=project.fingerprint())
    result.add(Diagnostic.info(code, message, subjects=[stage], data={"ready": 0}, hint=hint))
    return result
