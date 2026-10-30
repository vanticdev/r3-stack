"""Phase B: the RC car as joined objects, local-frame CAD parts and the organized
Onshape document plan. No Onshape access: the organization check runs against a
fake REST transport."""

from __future__ import annotations

import copy
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from r3_model.assembly import FLIP, rot_x, rot_z, trans_z
from r3_model.diagnostics import StageResult
from r3_model.project import Project

ROOT = Path(__file__).resolve().parents[1]
RC_CAR = ROOT / "examples" / "robots" / "rc-car"


@pytest.fixture(scope="module")
def project():
    return Project.load(RC_CAR)


@pytest.fixture(scope="module")
def catalog_parts(project):
    from r3_onshape.parts import build_part_catalog

    return build_part_catalog(project)


def _errors(diagnostics):
    return [d for d in diagnostics if d.severity.value == "error"]


# ------------------------------------------------------------------ compile
def test_rc_car_compiles_with_every_object_joined(project):
    result = project.assembly
    assert _errors(result.diagnostics) == []
    assert set(result.poses) == set(project.spec.components)
    assert all(loop["ok"] for loop in result.loops)
    assert max(loop["position_error"] for loop in result.loops) < 1e-9
    authored = [cid for cid, c in yaml.safe_load((RC_CAR / "robot.yaml").read_text())["components"].items() if "pose" in c]
    assert authored == ["chassis"]  # only the root keeps an authored pose
    warnings = {d.code for d in result.diagnostics if d.severity.value == "warning"}
    assert warnings <= {"loop_mobility_low", "mount_pattern_estimated"}
    provisional = [d for d in result.diagnostics if d.code == "mount_pattern_estimated"]
    assert len(provisional) == 1 and "front_camera" in provisional[0].subjects
    assert "30 × 30 mm" in provisional[0].message  # owned UC-717 measurements are pending


def test_exact_interference_and_seats_are_clean(project):
    from r3_orchestrator.assembly_stage import run

    stage = run(Project.load(RC_CAR))
    codes = {d.code for d in stage.diagnostics}
    assert "object_interference" not in codes and "seat_not_in_contact" not in codes
    assert stage.metrics["interferences"] == 0 and stage.metrics["exact_solids"] >= 130


def test_derived_mechanics_cover_fasteners_interfaces_and_joints(project):
    mech = project.spec.mechanical
    fasteners = {f.id: f for f in mech.fasteners}
    assert len(fasteners) >= 60 and all(f.join for f in fasteners.values())
    kingpin = fasteners["kingpin_thread_fl"]
    assert kingpin.body == "kp_fl" and kingpin.thread_into == "arm_fl" and not kingpin.preloaded
    assert kingpin.part == "kp_fl" and kingpin.length == pytest.approx(7.85e-3)
    nut = fasteners["wheel_nut_rl"]
    assert nut.body == "nut_rl" and nut.thread_into == "axle_rl" and nut.clamps == ["wheel_rl", "spacer_rl", "bearing_rl"]
    mast = fasteners["mast_mount_1"]
    assert mast.nut == "nut-m3-nyloc" and mast.clamps == ["antenna_mast", "deck"] and mast.grip == pytest.approx(6e-3)
    screw = fasteners["gb_mount_1"]
    assert screw.type == "shcs-m3" and screw.length == pytest.approx(0.010) and screw.thread_into == "gearbox"
    assert screw.axis == pytest.approx([0, 0, 1]) and screw.position[2] == pytest.approx(0.025)  # head under the plate
    kinds = {i.id: i.kind for i in mech.interfaces}
    assert kinds["kingpin_fl"] == "revolute_fit" and kinds["horn_spline"] == "spline" and kinds["bj_kl"] == "ball_joint"
    assert kinds["output_l"] == "drive_flat" and kinds["stub_press_fl"] == "press_fit" and kinds["gpio_header"] == "connector_retention"
    assert kinds["enc_magnet_1_bond"] == "adhesive" and kinds["battery_strap_loop"] == "strap" and kinds["enc_target"] == "magnetic_target"
    joints = {j.id: j for j in mech.joints}
    assert set(joints) == {"spin_rl", "spin_rr", "steer_fl", "steer_fr", "spin_fl", "spin_fr", "steer_servo"}
    assert joints["steer_fl"].interface == "kingpin_fl" and joints["steer_fl"].damping == pytest.approx(0.02)
    assert joints["steer_fl"].limits == pytest.approx([-0.45, 0.45]) and joints["spin_fl"].type == "continuous"


def test_mirrored_parts_place_symmetrically(project):
    poses = project.assembly.poses
    for left, right in (("knuckle_fl", "knuckle_fr"), ("arm_fl", "arm_fr"), ("hub_rl", "hub_rr"), ("tie_rod", "tie_rod_r")):
        assert poses[right][:3, 3] == pytest.approx(poses[left][:3, 3] * np.array([1, -1, 1]), abs=1e-12)
    joints = {j.id: j for j in project.spec.mechanical.joints}
    assert joints["steer_fr"].origin == pytest.approx(np.array(joints["steer_fl"].origin) * [1, -1, 1], abs=1e-12)


def test_hat_follows_board_json_hole_offset(project):
    """board.json puts the 58 x 49 pattern 0.25 mm toward the GPIO edge: the HAT center sits 0.25 mm off the Pi pattern."""
    pi, hat = project.assembly.poses["pi"], project.assembly.poses["hat"]
    assert hat[1, 3] - pi[1, 3] == pytest.approx(-0.25e-3, abs=1e-12)


def test_printed_bores_use_the_print_fit_table(project, tmp_path):
    from r3_model.assembly import compile_assembly

    spec = copy.deepcopy(project.spec)
    catalog = copy.deepcopy(project.catalog)
    knuckle = catalog.components["steering-knuckle"]
    knuckle.interfaces["kingpin"].params["d"] = "3.5 mm"  # 0.5 mm on a 3 mm shank: beyond the SLS sliding range
    result = compile_assembly(spec, catalog)
    fits = [d for d in result.diagnostics if d.code == "fit_out_of_range" and "kingpin_fl" in d.subjects]
    assert fits and fits[0].data["print_process"] == "sls" and fits[0].data["limit_max"] == pytest.approx(0.3e-3)


def test_caps_follow_the_print_clearance(project):
    for type_id, head in (("screw-cap-m2.5-petg", "shcs-m2.5"), ("screw-cap-m3-petg", "shcs-m3")):
        seat = project.catalog.component(type_id).interfaces["seat"].params
        fit = project.catalog.print_fits["fdm"]
        from r3_model.units import to_si

        assert to_si(seat["inner_d"]) == pytest.approx(project.catalog.fastener(head).head_diameter + 2 * to_si(fit.cap_clearance))


# ------------------------------------------------------------------ CAD parts and assembly plan
def test_parts_are_local_and_exactly_disjoint(project, catalog_parts):
    from r3_onshape.parts import instance_poses, world_manifold

    parts, instances = catalog_parts
    assert len(instances) == len(project.spec.components) + sum(1 + bool(f.nut) for f in project.spec.mechanical.fasteners if not f.body)
    assert {p.name for p in parts.values()} >= {"Chassis plate", "Steering knuckle L", "Steering knuckle R", "SHCS M3x10"}
    mirrored = [p for p in parts.values() if p.mirror_of]
    assert {p.name for p in mirrored} == {"Steering knuckle R", "Front arm R", "Front stub axle R", "Rear hub carrier R", "Tie rod R"}
    assert all(not p.library for p in parts.values() if p.plan.kind == "fabricated")


def test_mates_reproduce_the_compiled_poses(project, catalog_parts):
    from r3_onshape.organized import _connector_world, plan_assembly

    parts, instances = catalog_parts
    plan = plan_assembly(project, parts, instances)
    assert {m.kind for m in plan.mates} == {"FASTENED", "REVOLUTE", "BALL"}
    mated = {x for m in plan.mates for x in (m.a[0], m.b[0])}
    assert mated == set(plan.placements)
    for mate in plan.mates:
        wa = _connector_world(parts, plan.placements[mate.a[0]], mate.a[1])
        wb = _connector_world(parts, plan.placements[mate.b[0]], mate.b[1])
        flip = np.eye(4) if mate.aligned else FLIP  # nuts continue outward from the far face (aligned axes)
        predicted = wa @ rot_z(mate.clock) @ trans_z(mate.offset) @ flip  # Onshape mate (primary axis flipped unless aligned)
        if mate.kind == "FASTENED":
            assert np.abs(predicted - wb).max() < 1e-9, mate.name
        else:
            assert np.linalg.norm(predicted[:3, 3] - wb[:3, 3]) < 1e-9, mate.name
    names = [m.name for m in plan.mates]
    assert len(names) == len(set(names))


def test_connector_transform_round_trips_every_axis_aligned_frame():
    from r3_onshape.studio import SECONDARY, connector_transform

    rng = np.random.default_rng(3)
    axes = [np.eye(3)[i] * s for i in range(3) for s in (1, -1)]
    count = 0
    for z, x in itertools.product(axes, axes):
        if abs(z @ x) > 0.5:
            continue
        frame = np.eye(4)
        frame[:3, :3] = np.column_stack([x, np.cross(z, x), z])
        frame[:3, 3] = rng.normal(size=3) * 0.05
        t = connector_transform(frame)
        base = (np.diag([1.0, -1, -1, 1]) if t["flip"] else np.eye(4)) @ rot_z(SECONDARY[t["secondary"]])
        move = np.eye(4)
        move[:3, 3] = t["translation"]
        turn = {"ABOUT_Z": rot_z, "ABOUT_X": rot_x,
                "ABOUT_Y": lambda a: np.array([[math.cos(a), 0, math.sin(a), 0], [0, 1, 0, 0], [-math.sin(a), 0, math.cos(a), 0],
                                               [0, 0, 0, 1]])}[t["axis"]](t["rotation"])
        assert np.abs(base @ move @ turn - frame).max() < 1e-12
        count += 1
    assert count == 24


DOF = {"line": 4, "arc": 5, "circle": 3, "point": 2}


def _dof(writer) -> int:
    """Free degrees of freedom: entity DOF minus constraint equations (coincident point-point 2,
    point-origin 2, point-on-line 1, everything else 1)."""
    free = 0
    for entity in writer.entities:
        if entity["btType"] == "BTMSketchPoint-158":
            free += DOF["point"]
        elif entity["btType"] == "BTMSketchCurve-4":
            free += DOF["circle"]
        else:
            free += DOF["arc"] if entity["geometry"]["btType"] == "BTCurveGeometryCircle-115" else DOF["line"]
    for constraint in writer.constraints:
        kind = constraint["constraintType"]
        params = {p["parameterId"]: p for p in constraint["parameters"]}
        if kind == "COINCIDENT":
            second = params.get("localSecond", {}).get("value", "")
            free -= 2 if ("externalSecond" in params or "." in second) else 1
        else:
            free -= 1
    return free


def test_sketches_are_fully_constrained():
    from r3_onshape.geometry import Loop
    from r3_onshape.studio import SketchWriter

    shapes = [Loop.polygon([(0.0, 0.0), (0.02, 0.0), (0.03, 0.01), (0.0, 0.02)]), Loop.rectangle((-0.01, -0.005), (0.02, 0.005)),
              Loop.circle((0.0, 0.0), 0.003), Loop.circle((0.01, -0.02), 0.002), Loop.d_shape((0.0, 0.0), 0.003, 0.0025, math.pi / 2),
              Loop.half_disc((0.0, 0.0121), 0.0029)]
    for loop in shapes:
        writer = SketchWriter()
        writer.loop(loop)
        assert _dof(writer) == 0, loop
    points = SketchWriter()
    for p in ((0.0, 0.0), (0.012, 0.0), (0.0, -0.004), (0.007, 0.006)):
        points.point(p)
    assert _dof(points) == 0


# ------------------------------------------------------------------ organization check (fake Onshape)
class FakeOnshape:
    """Just enough REST for :func:`organization_check` on one tiny document."""

    name = "fake"

    def __init__(self, features, sketches, parts, contents, mates):
        self.features, self.sketches, self.parts, self.contents, self.mates = features, sketches, parts, contents, mates

    def json(self, method, path, query=None, body=None):
        if path.endswith("/features") and "/assemblies/" in path:
            return {"features": [{"name": n, "featureId": f"m{i}"} for i, n in enumerate(self.mates)]}
        if path.endswith("/features"):
            return {"features": self.features, "featureStates": {}}
        if path.endswith("/sketches"):
            return {"sketches": self.sketches}
        if path.startswith("/parts/"):
            return [{"name": n, "partId": f"J{i}"} for i, n in enumerate(self.parts)]
        if path.endswith("/contents"):
            return self.contents
        raise AssertionError(path)


def _fake_document(default_name=False, loose_tab=False, loose_sketch=False):
    from r3_onshape.organized import DocumentInfo, StudioRef
    from r3_onshape.parts import ConnectorSpec, LocalPart
    from r3_onshape.geometry import PartPlan

    part = LocalPart(key="post", name="Servo post", studio="Servo post", type="servo-post-al6061",
                     plan=PartPlan(name="Servo post", kind="fabricated", material="Al", density=2700),
                     connectors=[ConnectorSpec("mc_base", np.eye(4), "base"), ConnectorSpec("mc_top", np.eye(4), "top")])
    features = [{"name": "Extrude 1" if default_name else "envelope extrude 27.5 mm", "featureType": "extrude", "featureId": "F1"},
                {"name": "envelope sketch", "featureType": "newSketch", "featureId": "F0"},
                {"name": "mc_base", "featureType": "mateConnector", "featureId": "F2"},
                {"name": "mc_top", "featureType": "mateConnector", "featureId": "F3"}]
    sketches = [{"name": "envelope sketch", "sketchSolveStatus": "UNDER_DEFINED" if loose_sketch else "WELL_DEFINED"}]
    groups = [{"btType": "BTElementGroup-1458", "groupName": "02 Fabricated parts", "groups": [
        {"btType": "BTDocumentElementReference-2484", "elementId": "e1"}]}]
    elements = [{"id": "e1", "name": "Servo post", "elementType": "PARTSTUDIO"}]
    if loose_tab:
        groups.append({"btType": "BTDocumentElementReference-2484", "elementId": "e2"})
        elements.append({"id": "e2", "name": "Part Studio 2", "elementType": "PARTSTUDIO"})
    api = FakeOnshape(features, sketches, ["Servo post"], {"elements": elements, "folders": {"groups": groups}}, ["Fasten servo to post"])
    robot = DocumentInfo(did="d" * 24, wid="w" * 24, name="robot", studios={"Servo post": StudioRef("Servo post", "e1", {"post": "J0"}, {}, {})},
                         assemblies={"Top-level assembly": "a1"})
    library = DocumentInfo(did="l" * 24, wid="v" * 24, name="library")
    return api, robot, library, {"post": part}


@pytest.mark.parametrize("flags, code", [({"default_name": True}, "cad_default_feature_name"),
                                         ({"loose_tab": True}, "cad_tab_not_in_folder"),
                                         ({"loose_sketch": True}, "cad_unconstrained_sketch")])
def test_organization_check_flags_bad_documents(flags, code):
    from r3_onshape.organized import organization_check

    api, robot, library, parts = _fake_document(**flags)
    result = StageResult(stage="cad")
    organization_check(api, None, robot, library, parts, {}, result)
    assert code in {d.code for d in _errors(result.diagnostics)}
    api, robot, library, parts = _fake_document()
    clean = StageResult(stage="cad")
    report = organization_check(api, None, robot, library, parts, {}, clean)
    assert _errors(clean.diagnostics) == []
    assert "cad_feature_not_in_folder" in report["unchecked"]  # REST cannot see feature folders: reported, not passed


def test_misaligned_hole_pattern_is_a_compile_error(tmp_path):
    """Negative test: move one gearbox mounting hole in the chassis by 0.5 mm."""
    import shutil

    shutil.copy(RC_CAR / "robot.yaml", tmp_path / "robot.yaml")
    catalog = tmp_path / "catalog" / "components"
    catalog.mkdir(parents=True)
    data = yaml.safe_load((ROOT / "packages/component-catalog/r3_catalog/data/components/rc_car.yaml").read_text())
    plate = copy.deepcopy(next(c for c in data["components"] if c["id"] == "chassis-plate-al6061"))
    plate["interfaces"]["gearbox"]["params"]["holes"][0][0] = "18.5 mm"
    (catalog / "override.yaml").write_text(yaml.safe_dump({"components": [plate]}))
    mismatch = [d for d in Project.load(tmp_path).assembly.diagnostics if d.code == "hole_pattern_mismatch"]
    assert mismatch and "gb_mount" in mismatch[0].subjects and mismatch[0].data["max_offset"] == pytest.approx(5e-4, abs=1e-9)


def test_sim_encoder_reads_the_differential_carrier(project):
    from r3_sim.vehicle.mjcf import build_vehicle_model

    vm = build_vehicle_model(project)
    assert {w.component for w in vm.wheels.values()} == {"wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"}
    assert {w.steered_by for w in vm.wheels.values()} == {"steer_fl", "steer_fr", None}
    assert vm.steering[0].horn_joint == "steer_servo"
    params = project.catalog.component("hall-encoder-a1120").behavior.params
    assert params["target"] == "differential_carrier"
    assert project.spec.software.config["encoder_pulses_per_rev"] == pytest.approx(4 * params["target_ratio"], abs=1e-3)
    assert json.loads(json.dumps(project.spec.software.config["traction"]))["gear_ratio"] == pytest.approx(9.066)
