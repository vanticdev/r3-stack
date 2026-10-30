"""Downstream native CAD mass/bounds and standalone analytical steering-table interpolation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_sim.electrical.stage import vehicle_model
from r3_wiring.bodies import Kinematics

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"


def _steering_cad() -> dict:
    """A linkage solution with Ackermann-like asymmetry: the inner kingpin turns 1.2x the outer one."""
    servo = np.linspace(-0.5, 0.5, 11)
    inner, outer = 0.7 * servo * 1.2, 0.7 * servo  # inside the +-0.45 rad joint stops
    rows = [{"servo_rad": float(s), "left": float(i if s >= 0 else o), "right": float(o if s >= 0 else i)}
            for s, i, o in zip(servo, inner, outer)]
    sides = {j: {"status": "ok", "wheel_angle_range_rad": [min(r[j] for r in rows), max(r[j] for r in rows)]}
             for j in ("left", "right")}
    return {"kinematics": {"steering": {"transmission": "steering", "sides": sides, "table": rows}}}


@pytest.fixture()
def bare_project(tmp_path: Path) -> Project:
    root = tmp_path / "bench"
    shutil.copytree(BENCH, root, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return Project.load(root)


def test_wiring_kinematics_follow_cad_linkage(bare_project):
    from r3_model.spec import RobotSpec
    data = bare_project.spec.model_dump(mode="json")
    data["components"].update({name: {"type": "test-arm", "mount": "base", "pose": {"xyz": [0, sign * .08, .25]}}
                               for name, sign in (("left", 1), ("right", -1))})
    data["mechanical"] = {
        "joints": [{"id": name, "type": "revolute", "parent": "base", "child": name,
                    "origin": [0, sign * .08, .25], "axis": [0, 0, 1], "limits": [-.6, .6]}
                   for name, sign in (("left", 1), ("right", -1))],
        "transmissions": [{"id": "steering", "type": "ackermann_steering", "actuator": "servo",
                           "joints": ["left", "right"], "ratio": .5,
                           "params": {"wheelbase": .4, "kingpin_track": .16, "max_steer": .6}}]}
    spec = RobotSpec.model_validate(data)
    ideal = Kinematics(spec)
    cad = _steering_cad()
    kin = Kinematics(spec, cad)
    rows = cad["kinematics"]["steering"]["table"]
    # Centre angle of a table row maps back to that row's kingpin angles.
    for row in rows:
        q = kin.ackermann(0.5 * (row["left"] + row["right"]))
        assert q["left"] == pytest.approx(row["left"]) and q["right"] == pytest.approx(row["right"])
    # The sweep limit is the largest centre angle the linkage reaches on both sides.
    assert kin.steering_limit() == pytest.approx(0.5 * (rows[-1]["left"] + rows[-1]["right"]))
    assert kin.steering_limit() < ideal.steering_limit()
    # Kingpins sweep only their reachable travel (inside the joint stops).
    for joint in ("left", "right"):
        lo, hi = cad["kinematics"]["steering"]["sides"][joint]["wheel_angle_range_rad"]
        assert kin.joints[joint].limits == pytest.approx((lo, hi))
    # A failed linkage side is not trusted: ideal Ackermann is used.
    cad["kinematics"]["steering"]["sides"]["right"]["status"] = "locked"
    assert Kinematics(spec, cad).ackermann(0.2) == ideal.ackermann(0.2)


def test_electrical_vehicle_uses_only_native_mass_and_bounds(tmp_path):
    import yaml
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from pulled_fixture import write_pull
    from r3_model.pulled import PullError

    root = tmp_path / "native-mass"
    root.mkdir()
    (root / "robot.yaml").write_text(yaml.safe_dump({"robot": {"name": "Mass coupons", "mobility": "stationary"},
        "components": {"base": {"type": "test-plate"}, "payload": {"type": "test-box"}}}))
    folder = root / "catalog/components"
    folder.mkdir(parents=True)
    (folder / "coupons.yaml").write_text(yaml.safe_dump({"components": [
        {"id": type_id, "kind": "structure", "name": "Synthetic mass coupon",
         "mechanical": {"mass": 99, "size": [9, 9, 9]}} for type_id in ("test-plate", "test-box")]}))
    project = Project.load(root)
    with pytest.raises(PullError, match="No pulled CAD"):
        vehicle_model(project, StageResult(stage="electrical"))
    pose = np.eye(4); pose[:3, 3] = [0, -.12, 0]
    write_pull(root, {"base": BRepPrimAPI_MakeBox(300, 200, 200).Shape(),
                      "payload": BRepPrimAPI_MakeBox(10, 10, 10).Shape()},
               poses={"base": np.eye(4), "payload": pose}, mass={"base": .2, "payload": .001})
    # An old generated payload cannot override the pinned mass or dimensions.
    (project.build_dir("cad") / "cad_model.json").write_text(json.dumps({"parts": {"base": {"mass": 99}}}))
    result = StageResult(stage="electrical")
    vehicle = vehicle_model(Project.load(root), result)
    assert vehicle["mass"] == pytest.approx(.201)
    assert vehicle["mass_source"] == "pulled"
    assert vehicle["frontal_area_source"] == "pulled"
    assert vehicle["frontal_area"] == pytest.approx(.32 * .2)
