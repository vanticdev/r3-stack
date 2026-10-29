"""Negative tests: deliberately broken designs must produce the expected diagnostics.

Each case mutates ``robot.yaml`` (in a temporary project copy). ``onshape``
cases build only the affected parts in a scratch Onshape document, run the
scoped fit/motion checks on the imported geometry and delete the document;
``recorded`` cases re-run the checks on the geometry recorded from the clean
build (``build/cad/fixtures/onshape_geometry.json.gz``) with the mutated spec.
Results go to ``build/cad/negative_tests.json``.

    python -m r3_onshape.negative examples/robots/rc-car [case ...]
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


def _remove_strap(spec: dict[str, Any]) -> None:
    spec["components"].pop("battery_strap")
    spec["mechanical"]["interfaces"] = [i for i in spec["mechanical"]["interfaces"] if i["id"] != "battery_strap_loop"]


def _joint(spec: dict[str, Any], jid: str) -> dict[str, Any]:
    return next(j for j in spec["mechanical"]["joints"] if j["id"] == jid)


PI_STACK = ["deck", "pi", "hat", *[f"pi_spacer_{i}" for i in range(1, 5)], *[f"fastener:pi_d{i}" for i in range(1, 5)],
            *[f"fastener:pi_s{i}" for i in range(1, 5)], *[f"fastener:hat_{i}" for i in range(1, 5)]]

CASES = [
    Case("hat_offset_2_5mm", "HAT at its pre-redesign pose (2.5 mm toward +x): its catalog holes no longer meet the Pi standoffs",
         lambda s: _shift(s["components"]["hat"], "xyz", 0, "2.5 mm"),
         ["cad_interference", "fastener_hole_missing", "connector_misaligned"], PI_STACK),
    Case("arm_screw_too_short", "Front-arm screw shortened from M3x10 to M3x6: 3 mm of thread in PA12 (needs 2 d = 6 mm)",
         lambda s: _fastener(s, "arm_l1").update(length="6 mm"),
         ["thread_engagement_short"], ["chassis", "arm_fl", "fastener:arm_l1", "fastener:arm_l2"]),
    Case("motor_screw_misplaced", "Motor screw moved 1.5 mm off its tapped hole (spec position error)",
         lambda s: _shift(_fastener(s, "mot_1"), "position", 0, "1.5 mm"),
         ["cad_interference"], ["gearbox", "motor", "fastener:mot_1", "fastener:mot_2"]),
    Case("battery_strap_removed", "Battery strap deleted: the battery only rests on the plate (contact is not attachment)",
         _remove_strap, ["part_not_retained"], ["chassis", "battery"]),
    Case("encoder_gap_too_large", "Encoder board 2 mm lower: magnet air gap 3 mm and the screws miss the board's holes",
         lambda s: _shift(s["components"]["encoder"], "xyz", 2, "-2 mm"),
         ["magnet_gap_out_of_range", "cad_interference"],
         ["gearbox", "encoder", "fastener:enc_1", "fastener:enc_2", "axle_rl", "enc_ring", *[f"enc_magnet_{i}" for i in range(1, 5)]]),
    Case("servo_travel_51deg", "Original +-51 deg servo end points (1122/1878 us): the inner knuckle is driven past its 0.45 rad stop",
         lambda s: s["software"]["config"].update(steering_pulse_us={"left": 1878, "center": 1500, "right": 1122}),
         ["steering_range_unreachable"], mode="recorded", motion=True),
    Case("joint_axis_wrong", "steer_fl declared about +y instead of the verified kingpin axis",
         lambda s: _joint(s, "steer_fl").update(axis=[0, 1, 0]), ["joint_axis_mismatch"], mode="recorded"),
    Case("drive_path_broken", "Left wheel drive flat omitted from the drivetrain path",
         lambda s: next(t for t in s["mechanical"]["transmissions"] if t["id"] == "drivetrain").update(
             path=["motor_pinion", "output_l", "output_r", "wheel_drive_rr"]),
         ["transmission_path_broken"], mode="recorded"),
    Case("ball_socket_too_tight", "Tie-rod ball joint fit limit tightened to 0.03-0.05 mm (socket has 0.025 mm)",
         lambda s: _interface(s, "bj_hl")["params"].update(clearance=["0.03 mm", "0.05 mm"]),
         ["interface_fit_out_of_range"], mode="recorded"),
]


def _mutated_project(root: Path, case: Case, workdir: Path) -> Project:
    spec = yaml.safe_load((root / "robot.yaml").read_text())
    spec = copy.deepcopy(spec)
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
    root = Path(argv.pop(0) if argv else "examples/robots/rc-car").resolve()
    wanted = set(argv)
    from .client import default_transport

    api = default_transport()
    out_path = root / "build" / "cad" / "negative_tests.json"
    previous = {c["id"]: c for c in json.loads(out_path.read_text())["cases"]} if out_path.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        for case in CASES:
            if wanted and case.id not in wanted:
                continue
            if case.mode == "onshape" and api is None:
                previous[case.id] = {"id": case.id, "skipped": "no Onshape transport"}
                continue
            record = run_case(root, case, api, Path(tmp))
            previous[case.id] = record
            print(f"{'PASS' if record['passed'] else 'FAIL'} {case.id}: expected {case.expect}, observed {record['observed_codes']}")
    cases = [previous[c.id] for c in CASES if c.id in previous]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"cases": cases, "all_passed": all(c.get("passed") for c in cases)}, indent=1, default=str))
    return 0 if all(c.get("passed") for c in cases) else 1


if __name__ == "__main__":
    sys.exit(main())
