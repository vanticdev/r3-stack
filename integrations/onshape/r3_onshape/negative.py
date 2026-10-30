"""Negative tests: deliberately broken designs must produce the expected diagnostics.

Each case mutates ``robot.yaml`` (in a temporary project copy). ``onshape``
cases build only the affected parts in a scratch Onshape document, run the
scoped fit/motion checks on the imported geometry and delete the document;
``recorded`` cases re-run the checks on the geometry recorded from the clean
build (``build/cad/fixtures/onshape_geometry.json.gz``) with the mutated spec.
Results go to ``build/cad/negative_tests.json``.

    python -m r3_onshape.negative examples/robots/robot [case ...]
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from r3_model.diagnostics import StageResult
from r3_model.project import Project


@dataclass
class Case:
    id: str
    description: str
    mutate: Callable[[dict[str, Any]], None]
    expect: list[str]
    parts: list[str] = field(default_factory=list)  # onshape cases: parts to build
    mode: str = "onshape"  # onshape | recorded
    motion: bool = False


def _fastener(spec: dict[str, Any], fid: str) -> dict[str, Any]:
    return next(f for f in spec["mechanical"]["fasteners"] if f["id"] == fid)


def _interface(spec: dict[str, Any], iid: str) -> dict[str, Any]:
    return next(i for i in spec["mechanical"]["interfaces"] if i["id"] == iid)


def _shift(item: dict[str, Any], key: str, index: int, delta: str) -> None:
    from r3_model.units import to_si

    values = item[key] if key != "xyz" else item["pose"]["xyz"]
    values[index] = to_si(values[index]) + to_si(delta)


def cases_for_project(project: Project) -> list[Case]:
    """Generate applicable negative probes from the project's declared mechanics."""
    cases: list[Case] = []
    for fastener in project.spec.mechanical.fasteners:
        if fastener.length and fastener.thread_into and not fastener.body:
            fid = fastener.id
            cases.append(Case("short_thread", "Reduce a declared screw's engagement",
                lambda spec, fid=fid: _fastener(spec, fid).update(length="1 mm"),
                ["thread_engagement_short"], project.spec.link_parts(fid) or []))
            break
    for interface in project.spec.mechanical.interfaces:
        if interface.kind in ("revolute_fit", "press_fit"):
            iid = interface.id
            key = "interference" if interface.kind == "press_fit" else "clearance"
            cases.append(Case("fit_limit", "Require an intentionally incompatible shaft/bore fit",
                lambda spec, iid=iid, key=key: _interface(spec, iid)["params"].update({key: ["1 m", "2 m"]}),
                ["interface_fit_out_of_range"], list(interface.parts), mode="recorded"))
            break
    if project.spec.mechanical.joints:
        joint = project.spec.mechanical.joints[0]
        jid = joint.id
        # Pick a Cartesian axis perpendicular to the largest declared component.
        index = min(range(3), key=lambda index: abs(joint.axis[index]))
        axis = [float(index == k) for k in range(3)]
        cases.append(Case("joint_axis", "Change a declared joint axis away from its physical interface",
            lambda spec, jid=jid, axis=axis: next(j for j in spec["mechanical"]["joints"] if j["id"] == jid).update(axis=axis),
            ["joint_axis_mismatch"], mode="recorded"))
    return cases


def _mutated_project(root: Path, case: Case, workdir: Path) -> Project:
    spec = Project.load(root).spec.model_dump(mode="json")
    spec.pop("assembly", None)
    case.mutate(spec)
    target = workdir / case.id
    target.mkdir(parents=True, exist_ok=True)
    (target / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    if (root / "catalog").exists():
        shutil.copytree(root / "catalog", target / "catalog", dirs_exist_ok=True)
    return Project.load(target)


def run_case(root: Path, case: Case, api: Any | None, workdir: Path) -> dict[str, Any]:
    from .fit import verify_fit
    from .model import fetch_geometry, load_fixture
    from .motion import run_motion
    from .plan import build_plans

    project = _mutated_project(root, case, workdir)
    result = StageResult(stage="cad-negative")
    record: dict[str, Any] = {"id": case.id, "description": case.description, "mode": case.mode, "expected": case.expect}
    started = time.time()
    plans = build_plans(project)
    if case.mode == "recorded":
        fixture = root / "build" / "cad" / "fixtures" / "onshape_geometry.json.gz"
        model = load_fixture(fixture)
        record["geometry"] = f"recorded clean build (microversion {model.microversion})"
        fit = verify_fit(project, model, {n: p.material for n, p in plans.items()}, result)
    else:
        from .direct import build_robot
        from .sync import create_document, delete_document

        subset = {name: plans[name] for name in case.parts}
        doc = create_document(api, f"R3 negative test {case.id} (auto-deleted)", project.spec.cad.folder)
        record["document"] = doc.url()
        try:
            meta = build_robot(api, doc, project, result, subset)
            model = fetch_geometry(api, doc.did, doc.wid, doc.part_studio, {n: m["part_id"] for n, m in meta.items()})
            record["microversion"] = model.microversion
            fit = verify_fit(project, model, {n: p.material for n, p in subset.items()}, result, scoped=True)
        finally:
            delete_document(api, doc.did)
            record["document_deleted"] = True
    if case.motion:
        run_motion(project, model, fit, result)
    observed = sorted({d.code for d in result.diagnostics if d.severity.value in ("error", "warning")})
    record.update({
        "observed_codes": observed,
        "passed": all(code in observed for code in case.expect),
        "diagnostics": [{"severity": d.severity.value, "code": d.code, "message": d.message, "subjects": d.subjects, "data": d.data}
                        for d in result.diagnostics if d.severity.value in ("error", "warning")
                        and d.code != "fastener_tool_access_blocked"],
        "seconds": round(time.time() - started, 1),
    })
    return record


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv.pop(0) if argv else ".").resolve()
    candidates = cases_for_project(Project.load(root))
    wanted = set(argv)
    from .client import default_transport

    api = default_transport()
    out_path = root / "build" / "cad" / "negative_tests.json"
    previous = {c["id"]: c for c in json.loads(out_path.read_text())["cases"]} if out_path.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        for case in candidates:
            if wanted and case.id not in wanted:
                continue
            if case.mode == "onshape" and api is None:
                previous[case.id] = {"id": case.id, "skipped": "no Onshape transport"}
                continue
            record = run_case(root, case, api, Path(tmp))
            previous[case.id] = record
            print(f"{'PASS' if record['passed'] else 'FAIL'} {case.id}: expected {case.expect}, observed {record['observed_codes']}")
    cases = [previous[c.id] for c in candidates if c.id in previous]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"cases": cases, "all_passed": all(c.get("passed") for c in cases)}, indent=1, default=str))
    return 0 if all(c.get("passed") for c in cases) else 1


if __name__ == "__main__":
    sys.exit(main())
