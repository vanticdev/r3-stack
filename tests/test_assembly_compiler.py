"""Assembly compiler on the bench fixture: positive build, every negative case
of the brief with its exact diagnostic code and numbers, back-compat and the
``assembly`` stage artifacts."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest
import yaml

from r3_model.assembly import rotation_about_axis
from r3_model.project import Project
from r3_orchestrator import stages

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "assembly_bench"
CATALOG = FIXTURE / "catalog" / "components" / "bench.yaml"
MM = 1e-3


def bench(tmp_path: Path, spec: Callable[[dict], None] | None = None, catalog: Callable[[dict[str, dict]], None] | None = None) -> Project:
    """Copy the bench fixture (optionally mutating robot.yaml and the catalog) and load it."""
    data = yaml.safe_load((FIXTURE / "robot.yaml").read_text())
    types = {entry["id"]: entry for entry in yaml.safe_load(CATALOG.read_text())["components"]}
    if spec:
        spec(data)
    if catalog:
        catalog(types)
    root = tmp_path / "bench"
    (root / "catalog" / "components").mkdir(parents=True)
    (root / "robot.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    (root / "catalog" / "components" / "bench.yaml").write_text(yaml.safe_dump({"components": list(types.values())}, sort_keys=False))
    return Project.load(root)


def join(data: dict, join_id: str) -> dict:
    return next(j for j in data["assembly"]["joins"] if j.get("id", f"{j['a']}~{j['b']}") == join_id)


def only(project: Project, code: str) -> Any:
    found = [d for d in project.assembly.diagnostics if d.code == code]
    assert len(found) == 1, f"expected one {code}, got {[(d.code, d.message) for d in project.assembly.diagnostics]}"
    return found[0]


def run_assembly(project: Project):
    return stages.run_stage(project, "assembly")


# --------------------------------------------------------------------------- positive


def test_bench_compiles_with_exact_poses(tmp_path):
    project = bench(tmp_path)
    result = project.assembly
    assert result.diagnostics == []
    xyz = {cid: (np.asarray(comp.pose.xyz) / MM).round(9).tolist() for cid, comp in project.spec.components.items()}
    # Hand-derived stack: plate top 4, 30 mm standoffs, 3 mm deck, 6 mm Pi standoffs, 1.6 mm Pi, 11 mm HAT stack.
    assert xyz["deck"] == [0, 0, 34]
    assert xyz["bracket"] == [75, 0, 4]
    assert xyz["motor"] == [103, 0, 34]  # 25 mm half-can beyond the bracket face at x = 78
    assert xyz["wheel"] == [-50, 0, 6]
    assert xyz["pi"] == [-20, 0, 43]
    assert xyz["hat"] == [-30, 0, 55.6]
    assert xyz["hat_so_4"] == [-1, -24.5, 44.6]
    # The motor faces the bracket: its +x axis points along robot -x (yaw 180 deg).
    assert np.allclose(project.assembly.poses["motor"][:3, 0], [-1, 0, 0], atol=1e-15)
    # Every object is placed from joins; mount is the tree parent.
    assert len(result.poses) == len(project.spec.components) == 20
    assert project.spec.components["deck"].mount == "s_fl" and project.spec.components["hat"].mount == "pi"


def test_bench_loops_close_and_header_clock_is_inferred(tmp_path):
    result = bench(tmp_path).assembly
    assert len(result.tree) == 19 and len(result.loops) == 10
    assert max(loop["position_error"] for loop in result.loops) < 1e-15
    assert {loop["join"] for loop in result.loops} >= {"s_fr.top~deck.h_fr", "pi_so_4.top~pi.b4", "hat_so_1.top~hat.h1"}
    # 2x20 grid is 180-degree symmetric; pin 1 picks clock 0 without an authored clock.
    assert result.joins["pi.gpio~hat.socket"]["clock_deg"] == pytest.approx(0.0)


def test_wheel_joint_is_derived_and_moves(tmp_path):
    project = bench(tmp_path)
    (joint,) = [j for j in project.spec.mechanical.joints if j.id == "wheel_spin"]
    assert (joint.type, joint.parent, joint.child) == ("continuous", "axle", "wheel")
    assert joint.origin == pytest.approx([-0.05, 0, 0.006]) and joint.axis == pytest.approx([0, 0, 1])
    q = 1.234
    moved = project.assembly.poses_at({"wheel_spin": q})
    assert np.allclose(moved["wheel"], rotation_about_axis(joint.axis, joint.origin, q) @ project.assembly.poses["wheel"], atol=1e-15)
    rim = moved["wheel"] @ np.array([0.03, 0, 0.01, 1])
    assert rim[:3] == pytest.approx([-0.05 + 0.03 * math.cos(q), 0.03 * math.sin(q), 0.016])
    assert all(np.array_equal(moved[o], project.assembly.poses[o]) for o in moved if o != "wheel")


def test_assembly_stage_writes_artifacts(tmp_path):
    project = bench(tmp_path)
    result = run_assembly(project)
    assert result.ok and result.diagnostics == []
    payload = json.loads((project.root / "build" / "model" / "assembly.json").read_text())
    assert payload["root"] == "plate" and len(payload["poses"]) == 20 and len(payload["loops"]) == 10
    assert payload["poses"]["hat"]["xyz"] == pytest.approx([-0.03, 0, 0.0556])
    assert payload["joints"][0]["id"] == "wheel_spin"
    # The axle inside the wheel hub is an allowed insertion, not interference.
    assert [(i["a"], i["b"], i["insertion"]) for i in payload["interference"]] == [("axle", "wheel", "axle.shaft~wheel.hub")]
    png = project.root / "build" / "model" / "assembly_graph.png"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result.metrics["loop_closures"] == 10 and result.metrics["interferences"] == 0


def test_assembly_stage_runs_first():
    assert stages.stage_names()[0] == "assembly" and stages.stage_names()[1] == "model"


def test_model_stage_sees_compiled_poses(tmp_path):
    project = bench(tmp_path)
    result = stages.run_stage(project, "model")
    graph = json.loads((project.root / "build" / "model" / "graph.json").read_text())
    assert graph["components"]["hat"]["aabb_min"][2] == pytest.approx(0.0556)
    assert "stage_crashed" not in {d.code for d in result.diagnostics}


# --------------------------------------------------------------------------- negative cases of the brief


def test_hole_pattern_offset_half_mm_fails(tmp_path):
    def shift(types):
        types["motor-540-bench"]["interfaces"]["face"]["params"]["holes"] = [
            [12.5e-3, 0.5e-3, 3e-3, "tapped", 4e-3], [-12.5e-3, 0.5e-3, 3e-3, "tapped", 4e-3]]
    diag = only(bench(tmp_path, catalog=shift), "hole_pattern_mismatch")
    assert diag.subjects == ["bracket", "motor", "bracket.motor_face~motor.face"]
    assert diag.data["max_offset"] == pytest.approx(0.5e-3, abs=1e-12)
    assert diag.data["allowance"] == pytest.approx(0.1e-3, abs=1e-12)  # (3.2 - 3.0) / 2, tapped side 0
    assert diag.data["matched"] == 2 and len(diag.data["over_limit"]) == 2


def test_hole_pattern_within_allowance_passes(tmp_path):
    def shift(types):
        types["motor-540-bench"]["interfaces"]["face"]["params"]["holes"] = [
            [12.5e-3, 0.09e-3, 3e-3, "tapped", 4e-3], [-12.5e-3, 0.09e-3, 3e-3, "tapped", 4e-3]]
    assert "hole_pattern_mismatch" not in {d.code for d in bench(tmp_path, catalog=shift).assembly.diagnostics}


def test_standoff_one_mm_short_is_a_loop_misfit(tmp_path):
    def short(types):
        entry = copy.deepcopy(types["standoff-m3-30"])
        entry["id"] = "standoff-m3-29"
        entry["mechanical"]["size"] = ["5.5 mm", "29 mm"]
        entry["interfaces"]["top"]["origin"] = [0, 0, "29 mm"]
        types[entry["id"]] = entry

    def use(data):
        data["components"]["s_rr"]["type"] = "standoff-m3-29"
    diag = only(bench(tmp_path, spec=use, catalog=short), "assembly_misfit")
    assert diag.data["join"] == "s_rr.top~deck.h_rr"
    assert diag.data["position_error"] == pytest.approx(1e-3, abs=1e-12)
    assert diag.data["error_vector"] == pytest.approx([0, 0, -1e-3], abs=1e-12)
    assert diag.data["angle_error"] == pytest.approx(0.0, abs=1e-12)
    assert diag.data["expected"] == pytest.approx([-0.06, -0.04, 0.033]) and diag.data["actual"] == pytest.approx([-0.06, -0.04, 0.034])
    assert diag.data["position_tolerance"] == pytest.approx(5e-5)
    assert set(diag.data["cycle"]) == {"plate.so_fl~s_fl.bottom", "s_fl.top~deck.h_fl", "plate.so_rr~s_rr.bottom", "s_rr.top~deck.h_rr"}


def test_motor_on_a_seat_inside_the_plate_is_interference(tmp_path):
    def seat(types):
        # Authoring error: a seat on the plate's bottom face with its normal pointing up, into the plate.
        types["bench-plate"]["interfaces"]["motor_pad"] = {
            "kind": "hole_pattern", "origin": ["-100 mm", 0, 0],
            "params": {"holes": [["12.5 mm", 0, "3.2 mm", "clearance", "4 mm"], ["-12.5 mm", 0, "3.2 mm", "clearance", "4 mm"]]}}

    def rejoin(data):
        moved = join(data, "bracket.motor_face~motor.face")
        moved["a"], moved["fasteners"]["type"] = "plate.motor_pad", "M3x8"  # 4 mm plate grip
    project = bench(tmp_path, spec=rejoin, catalog=seat)
    assert project.assembly.errors == []  # the holes line up; only the volumes collide
    result = run_assembly(project)
    (diag,) = [d for d in result.diagnostics if d.code == "object_interference"]
    assert diag.subjects == ["motor", "plate", "plate.motor_pad~motor.face"]
    # 48-gon prism of the 36 mm can inside the 4 mm plate: 24 r^2 sin(7.5 deg) x 4 mm.
    assert diag.data["overlap_volume"] == pytest.approx(24 * 0.018 ** 2 * math.sin(math.radians(7.5)) * 0.004, rel=1e-6)
    assert diag.data["depth"] == pytest.approx(0.004) and diag.data["joined"] is True


def test_missing_join_is_object_not_joined(tmp_path):
    def drop(data):
        data["assembly"]["joins"] = [j for j in data["assembly"]["joins"] if "hat_so_4" not in j["a"] + j["b"]]
    diag = only(bench(tmp_path, spec=drop), "object_not_joined")
    assert diag.severity.value == "error" and diag.subjects == ["hat_so_4"] and diag.data == {"root": "plate", "joins": 0}


def test_island_not_reaching_root_is_object_not_joined(tmp_path):
    def drop(data):
        data["assembly"]["joins"] = [j for j in data["assembly"]["joins"] if j["a"] != "plate.axle_hole"]
    project = bench(tmp_path, spec=drop)
    found = {d.subjects[0]: d for d in project.assembly.diagnostics if d.code == "object_not_joined"}
    assert set(found) == {"axle", "wheel"} and found["wheel"].data["island"] == ["axle", "wheel"]


def test_shaft_larger_than_bore_is_fit_out_of_range(tmp_path):
    def shrink(types):
        types["wheel-60x20"]["interfaces"]["hub"]["params"]["d"] = "5.9 mm"
    diag = only(bench(tmp_path, catalog=shrink), "fit_out_of_range")
    # 6 g6 = 5.988..5.996, 5.9 H7 = 5.900..5.912 (ISO 286-2, 3-6 mm row).
    assert diag.data["clearance_min"] == pytest.approx(-0.096e-3, abs=1e-12)
    assert diag.data["clearance_max"] == pytest.approx(-0.076e-3, abs=1e-12)
    assert diag.data["actual_fit"] == "interference" and diag.data["required_fit"] == "clearance"
    assert (diag.data["limit_min"], diag.data["limit_max"]) == pytest.approx((0.0, 220e-6))


def test_too_loose_running_fit_is_fit_out_of_range(tmp_path):
    def grow(types):
        types["wheel-60x20"]["interfaces"]["hub"]["params"]["d"] = "6.3 mm"
    diag = only(bench(tmp_path, catalog=grow), "fit_out_of_range")
    assert diag.data["clearance_max"] == pytest.approx(0.3e-3 + 15e-6 + 12e-6, abs=1e-12)  # 6.3 H7 is in the 6-10 row


# --------------------------------------------------------------------------- other compatibility diagnostics


def test_symmetric_two_hole_pattern_needs_explicit_clock(tmp_path):
    def unclock(data):
        join(data, "plate.bracket_seat~bracket.foot").pop("clock")
    diag = only(bench(tmp_path, spec=unclock), "clock_ambiguous")
    assert diag.data["candidates_deg"] == pytest.approx([0.0, 180.0])


def test_wrong_explicit_clock_reports_matching_clocks(tmp_path):
    def turn(data):
        join(data, "bracket.motor_face~motor.face")["clock"] = "90 deg"
    diag = only(bench(tmp_path, spec=turn), "hole_pattern_mismatch")
    assert diag.data["clock_deg"] == pytest.approx(90.0) and diag.data["matching_clocks_deg"] == pytest.approx([0.0, 180.0])
    assert diag.data["matched"] == 0 and len(diag.data["unmatched_a"]) == 2


def test_header_pin1_reversed_is_header_mismatch(tmp_path):
    def flip(types):
        types["hat-bench"]["interfaces"]["socket"]["params"]["pin1"] = ["-24.13 mm", "-1.27 mm"]
    diag = only(bench(tmp_path, catalog=flip), "header_mismatch")
    assert diag.data["pin1_offset"] == pytest.approx(2.54e-3) and diag.data["max_pin_offset"] == pytest.approx(0.0, abs=1e-15)


def test_header_pitch_mismatch(tmp_path):
    def pitch(types):
        types["hat-bench"]["interfaces"]["socket"]["params"]["pitch"] = "2.0 mm"
    diag = only(bench(tmp_path, catalog=pitch), "header_mismatch")
    assert (diag.data["header_pitch"], diag.data["socket_pitch"]) == pytest.approx((2.54e-3, 2.0e-3))
    assert diag.data["max_pin_offset"] > 0.5e-3


def test_clearance_hole_too_small_is_hole_size_mismatch(tmp_path):
    def tight(types):
        types["bench-deck"]["interfaces"]["h_fl"]["params"]["holes"] = [[0, 0, "3.0 mm", "clearance", "3 mm"]]
    diag = only(bench(tmp_path, catalog=tight), "hole_size_mismatch")
    assert diag.data["hole_d"] == pytest.approx(3.0e-3) and diag.data["iso273_min"] == pytest.approx(3.2e-3)
    assert diag.data["iso273_max"] == pytest.approx(3.6e-3)


def test_tapped_to_tapped_is_hole_size_mismatch(tmp_path):
    def tapped(types):
        types["bench-plate"]["interfaces"]["axle_hole"]["params"]["holes"] = [[0, 0, "3 mm", "tapped", "4 mm"]]
    diag = only(bench(tmp_path, catalog=tapped), "hole_size_mismatch")
    assert (diag.data["a_kind"], diag.data["b_kind"]) == ("tapped", "tapped")


@pytest.mark.parametrize("fastener, min_mm, max_mm", [("M3x8", 9.0, 10.0), ("M3x12", 9.0, 10.0)])
def test_fastener_length_invalid(tmp_path, fastener, min_mm, max_mm):
    def screw(data):
        join(data, "bracket.motor_face~motor.face")["fasteners"]["type"] = fastener
    diag = only(bench(tmp_path, spec=screw), "fastener_length_invalid")
    # Grip = 6 mm bracket; >= 1.0 x 3 mm engagement; <= 4 mm thread depth in the motor face.
    assert diag.data["grip"] == pytest.approx(6e-3)
    assert (diag.data["min_length"], diag.data["max_length"]) == pytest.approx((min_mm * MM, max_mm * MM))
    assert len(diag.data["pairs"]) == 2


def test_kind_mismatch_and_join_kind_invalid(tmp_path):
    def socket(types):
        types["wheel-60x20"]["interfaces"]["hub"] = {"kind": "socket", "z": [0, 0, -1],
                                                     "params": {"pitch": "2.54 mm", "rows": 1, "cols": 1}}
    project = bench(tmp_path, catalog=socket)
    diag = only(project, "interface_kind_mismatch")
    assert (diag.data["a_kind"], diag.data["b_kind"]) == ("shaft", "socket") and diag.data["a_partners"] == ["bore"]

    def revolute(data):
        join(data, "plate.axle_hole~axle.base")["kind"] = "revolute"
    diag = only(bench(tmp_path / "2", spec=revolute), "join_kind_invalid")
    assert diag.data == {"join_kind": "revolute", "allowed": ["fasten"]}


def test_unknown_interface(tmp_path):
    def typo(data):
        join(data, "pi.gpio~hat.socket")["b"] = "hat.sockett"
    diag = only(bench(tmp_path, spec=typo), "interface_unknown")
    assert diag.data["interface"] == "sockett" and "socket" in diag.data["available"]


def test_interface_used_twice_is_occupied(tmp_path):
    def duplicate(data):
        data["assembly"]["joins"].append({"id": "dup", "a": "plate.so_fl", "b": "s_fl.bottom"})
    project = bench(tmp_path, spec=duplicate)
    found = {d.data["interface"]: d for d in project.assembly.diagnostics if d.code == "interface_occupied"}
    assert set(found) == {"plate.so_fl", "s_fl.bottom"} and found["plate.so_fl"].data["uses"] == 2


def test_rigid_join_locking_a_revolute_is_overconstrained(tmp_path):
    def collar(types):
        types["axle-stub-6"]["interfaces"]["collar"] = {"kind": "hole_pattern", "origin": [0, 0, "22 mm"], "z": [0, 0, -1],
                                                        "params": {"holes": [[0, 0, "6.4 mm", "clearance", "2 mm"]]}}
        types["wheel-60x20"]["interfaces"]["top"] = {"kind": "hole_pattern", "origin": [0, 0, "20 mm"],
                                                     "params": {"holes": [[0, 0, "6.4 mm", "clearance", "20 mm"]]}}

    def lock(data):
        data["assembly"]["joins"].append({"id": "lock", "a": "axle.collar", "b": "wheel.top"})
    project = bench(tmp_path, spec=lock, catalog=collar)
    diag = only(project, "object_overconstrained_conflict")
    assert diag.data["locked_join"] == "axle.shaft~wheel.hub" and diag.data["mobility"] == -5
    assert next(loop for loop in project.assembly.loops if loop["join"] == "lock")["position_error"] < 1e-15


# --------------------------------------------------------------------------- back-compat


def test_authored_pose_matching_is_ignored_and_conflict_is_error(tmp_path):
    def same(data):
        data["components"]["deck"]["pose"] = {"xyz": [0, 0, "34 mm"]}
    diag = only(bench(tmp_path, spec=same), "authored_pose_ignored")
    assert diag.severity.value == "warning" and diag.data["position_error"] < 1e-15

    def off(data):
        data["components"]["deck"]["pose"] = {"xyz": [0, 0, "35 mm"]}
    project = bench(tmp_path / "2", spec=off)
    diag = only(project, "pose_conflict")
    assert diag.data["position_error"] == pytest.approx(1e-3) and diag.data["compiled_xyz"] == pytest.approx([0, 0, 0.034])
    assert project.spec.components["deck"].pose.xyz == pytest.approx([0, 0, 0.034])  # compiled pose wins


def test_unjoined_object_with_authored_pose_is_kept_with_warning(tmp_path):
    def legacy(data):
        data["components"]["gps"] = {"type": "hat-bench", "pose": {"xyz": [0.1, 0, 0.1]}}
    project = bench(tmp_path, spec=legacy)
    diag = only(project, "object_not_joined")
    assert diag.severity.value == "warning" and project.spec.components["gps"].pose.xyz == [0.1, 0, 0.1]


def test_specs_without_assembly_are_untouched():
    project = Project.load(ROOT / "examples" / "robots" / "rc-car")
    raw = yaml.safe_load((ROOT / "examples" / "robots" / "rc-car" / "robot.yaml").read_text())
    assert project.spec.assembly is None and project.assembly is None
    assert not hasattr(project, "_catalog")  # loading does not touch the catalog
    reference = Project.load(ROOT / "examples" / "robots" / "rc-car")
    for cid, comp in project.spec.components.items():
        assert comp.pose == reference.spec.components[cid].pose
        assert comp.mount == raw["components"][cid].get("mount", "world")


def test_assembly_stage_without_assembly_is_informational(tmp_path):
    from orchestrator_helpers import make_project

    result = run_assembly(Project.load(make_project(tmp_path)))
    assert result.ok and [d.code for d in result.diagnostics] == ["assembly_not_declared"]
