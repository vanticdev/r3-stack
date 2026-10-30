"""Phase B: the bench as joined objects, local-frame CAD parts and the organized
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
BENCH = ROOT / "tests" / "fixtures" / "mechanism"


@pytest.fixture(scope="module")
def project():
    return Project.load(BENCH)


@pytest.fixture(scope="module")
def catalog_parts(project):
    from r3_onshape.parts import build_part_catalog

    return build_part_catalog(project)


def _errors(diagnostics):
    return [d for d in diagnostics if d.severity.value == "error"]


# ------------------------------------------------------------------ compile
def test_bench_compiles_with_every_object_joined(project):
    result = project.assembly
    assert _errors(result.diagnostics) == []
    assert set(result.poses) == set(project.spec.components)
    authored = [cid for cid, c in yaml.safe_load((BENCH / "robot.yaml").read_text())["components"].items() if "pose" in c]
    assert authored == ["base"]
    loops = Project.load(ROOT / "tests/fixtures/assembly_bench").assembly.loops
    assert loops and all(loop["ok"] for loop in loops)
    assert max(loop["position_error"] for loop in loops) < 1e-9


def test_exact_interference_and_seats_are_clean(project):
    from r3_orchestrator.assembly_stage import run
    stage = run(project)
    codes = {d.code for d in stage.diagnostics}
    assert "object_interference" not in codes and "seat_not_in_contact" not in codes
    assert stage.metrics["interferences"] == 0
    assert stage.metrics["exact_solids"] == len(project.spec.components) + len(project.spec.mechanical.fasteners)


def test_derived_mechanics_cover_fasteners_interfaces_and_joints(project):
    mech = project.spec.mechanical
    assert len(mech.fasteners) == 2 and all(f.join == "support_mount" for f in mech.fasteners)
    assert all(f.type == "shcs-m3" and f.length == pytest.approx(.012) and f.thread_into == "support" for f in mech.fasteners)
    assert all(f.axis == pytest.approx([0, 0, 1]) and f.position[2] == pytest.approx(.02) for f in mech.fasteners)
    assert {i.id: i.kind for i in mech.interfaces} == {"sleeve_press": "press_fit", "rotor_pivot": "revolute_fit"}
    (joint,) = mech.joints
    assert joint.id == "pivot" and joint.interface == "rotor_pivot" and joint.damping == pytest.approx(.02)


def test_mirrored_parts_place_symmetrically(project):
    from r3_onshape.parts import build_part_catalog
    from r3_model.spec import ComponentInstance
    variant = Project(root=project.root, spec=copy.deepcopy(project.spec), lock={})
    variant.spec.components["mirror"] = ComponentInstance(type="coupon-support", params={"mirror": [1, -1, 1]})
    parts, instances = build_part_catalog(variant)
    original, mirrored = parts[instances["support"]], parts[instances["mirror"]]
    assert mirrored.mirror_of == original.key and mirrored.mirror == [1, -1, 1]




def test_printed_bores_use_the_print_fit_table(project, tmp_path):
    from r3_model.assembly import compile_assembly
    catalog = copy.deepcopy(project.catalog)
    catalog.components["coupon-rotor"].interfaces["inner"].params["d"] = .017
    result = compile_assembly(copy.deepcopy(project.spec), catalog)
    fits = [d for d in result.diagnostics if d.code == "fit_out_of_range" and "rotor_pivot" in d.subjects]
    assert fits and fits[0].data["print_process"] == "fdm"
    assert fits[0].data["clearance_min"] > fits[0].data["limit_max"]




# ------------------------------------------------------------------ CAD parts and assembly plan
def test_parts_are_local_and_exactly_disjoint(project, catalog_parts):
    parts, instances = catalog_parts
    assert len(instances) == len(project.spec.components) + len(project.spec.mechanical.fasteners)
    assert {p.name for p in parts.values()} == {"Coupon base", "Coupon support", "Coupon sleeve", "Coupon rotor", "SHCS M3x12"}
    assert all(not p.library for p in parts.values() if p.plan.kind == "fabricated")


def test_mates_reproduce_the_compiled_poses(project, catalog_parts):
    from r3_onshape.organized import _connector_world, plan_assembly

    parts, instances = catalog_parts
    plan = plan_assembly(project, parts, instances)
    assert {m.kind for m in plan.mates} == {"FASTENED", "REVOLUTE"}
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

    part = LocalPart(key="post", name="Servo post", studio="Servo post", type="coupon-support",
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
    import shutil
    shutil.copytree(BENCH, tmp_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns("build", "__pycache__"))
    path = tmp_path / "catalog/components/coupons.yaml"
    data = yaml.safe_load(path.read_text())
    data["components"][0]["interfaces"]["mount"]["params"]["holes"][0][0] += .0005
    path.write_text(yaml.safe_dump(data))
    mismatch = [d for d in Project.load(tmp_path).assembly.diagnostics if d.code == "hole_pattern_mismatch"]
    assert mismatch and "support_mount" in mismatch[0].subjects
    assert mismatch[0].data["max_offset"] == pytest.approx(5e-4, abs=1e-9)
