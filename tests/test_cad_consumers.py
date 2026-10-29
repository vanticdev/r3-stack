"""Downstream stages read the direct-CAD hand-off (``build/cad/cad_model.json``) consistently:
steering linkage table, CAD-only hardware mass and exact bounding boxes."""

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

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


def _steering_cad() -> dict:
    """A linkage solution with Ackermann-like asymmetry: the inner kingpin turns 1.2x the outer one."""
    servo = np.linspace(-0.5, 0.5, 11)
    inner, outer = 0.7 * servo * 1.2, 0.7 * servo  # inside the +-0.45 rad joint stops
    rows = [{"servo_rad": float(s), "steer_fl": float(i if s >= 0 else o), "steer_fr": float(o if s >= 0 else i)}
            for s, i, o in zip(servo, inner, outer)]
    sides = {j: {"status": "ok", "wheel_angle_range_rad": [min(r[j] for r in rows), max(r[j] for r in rows)]}
             for j in ("steer_fl", "steer_fr")}
    return {"kinematics": {"steering": {"transmission": "steering", "sides": sides, "table": rows}}}


@pytest.fixture()
def bare_project(tmp_path: Path) -> Project:
    root = tmp_path / "rc-car"
    shutil.copytree(RC_CAR, root, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return Project.load(root)


def test_wiring_kinematics_follow_cad_linkage(bare_project):
    spec = bare_project.spec
    ideal = Kinematics(spec)
    cad = _steering_cad()
    kin = Kinematics(spec, cad)
    rows = cad["kinematics"]["steering"]["table"]
    # Centre angle of a table row maps back to that row's kingpin angles.
    for row in rows:
        q = kin.ackermann(0.5 * (row["steer_fl"] + row["steer_fr"]))
        assert q["steer_fl"] == pytest.approx(row["steer_fl"]) and q["steer_fr"] == pytest.approx(row["steer_fr"])
    # The sweep limit is the largest centre angle the linkage reaches on both sides.
    assert kin.steering_limit() == pytest.approx(0.5 * (rows[-1]["steer_fl"] + rows[-1]["steer_fr"]))
    assert kin.steering_limit() < ideal.steering_limit()
    # Kingpins sweep only their reachable travel (inside the joint stops).
    for joint in ("steer_fl", "steer_fr"):
        lo, hi = cad["kinematics"]["steering"]["sides"][joint]["wheel_angle_range_rad"]
        assert kin.joints[joint].limits == pytest.approx((lo, hi))
    # A failed linkage side is not trusted: ideal Ackermann is used.
    cad["kinematics"]["steering"]["sides"]["steer_fr"]["status"] = "locked"
    assert Kinematics(spec, cad).ackermann(0.2) == ideal.ackermann(0.2)


def test_electrical_vehicle_uses_cad_mass_and_boxes(bare_project):
    project = bare_project
    spec, catalog = project.spec, project.catalog
    no_cad = vehicle_model(project, StageResult(stage="electrical"))
    assert no_cad["mass_source"] == "estimate" and no_cad["frontal_area_source"] == "catalog"
    # A stale CAD model without the stub axles, plus one CAD-only screw.
    parts = {cid: {"mass": 0.01, "bbox_min": [0.0, -0.1, 0.0], "bbox_max": [0.3, 0.1, 0.2]}
             for cid in spec.components if not cid.startswith("stub_")}
    parts["fastener:gb_1"] = {"mass": 0.001, "owner": "gearbox", "bbox_min": [0.0, -0.12, 0.0], "bbox_max": [0.01, -0.11, 0.01]}
    (project.root / "build" / "cad").mkdir(parents=True)
    (project.root / "build" / "cad" / "cad_model.json").write_text(json.dumps({"parts": parts}))
    project = Project.load(project.root)
    result = StageResult(stage="electrical")
    vehicle = vehicle_model(project, result)
    stubs = [cid for cid in spec.components if cid.startswith("stub_")]
    expected = 0.01 * (len(spec.components) - len(stubs)) + 0.001 + sum(
        catalog.component(spec.components[cid].type).mechanical.mass for cid in stubs)
    assert vehicle["mass"] == pytest.approx(expected) and vehicle["mass_source"] == "cad+catalog"
    assert any("stub_fl" in a.statement for a in result.assumptions)
    # Frontal area from the exact boxes: width 0.22 m (screw at y = -0.12) x height 0.2 m x fill.
    assert vehicle["frontal_area_source"] == "cad"
    assert vehicle["frontal_area"] == pytest.approx(0.6 * 0.22 * 0.2)
