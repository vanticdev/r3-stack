"""Fit verification on geometry recorded from a real Onshape build (bodydetails
+ tessellation fixture of the front-left corner): fastener math, interface
probes, retention graph and the probe -> diagnostic mapping. No Onshape access."""

import copy
from pathlib import Path

import pytest
import yaml

from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_onshape.fit import verify_fit
from r3_onshape.model import load_fixture
from r3_onshape.plan import build_plans

ROOT = Path(__file__).resolve().parents[1]
RC_CAR = ROOT / "examples" / "robots" / "rc-car"
FIXTURE = ROOT / "tests" / "fixtures" / "cad" / "front_left_corner.json.gz"
PARTS = ["chassis", "arm_fl", "knuckle_fl", "stub_fl", "wheel_fl", "fastener:kp_fl", "fastener:arm_l1", "fastener:arm_l2",
         "fastener:nut_fl", "fastener:bs_kl"]


@pytest.fixture(scope="module")
def model():
    return load_fixture(FIXTURE)


@pytest.fixture(scope="module")
def project():
    return Project.load(RC_CAR)


def _run(project, model, names=None):
    plans = build_plans(project)
    if names is not None:
        model = copy.copy(model)
        model.parts = {n: p for n, p in model.parts.items() if n in names}
    result = StageResult(stage="cad")
    report = verify_fit(project, model, {n: p.material for n, p in plans.items()}, result, scoped=True)
    return report, result


def _codes(result):
    return {d.code for d in result.diagnostics if d.severity.value == "error"}


def test_recorded_corner_verifies(project, model):
    assert set(PARTS) <= set(model.parts)
    report, result = _run(project, model)
    assert _codes(result) == set()
    assert {"kp_fl", "arm_l1", "arm_l2", "nut_fl"} <= report.verified_fasteners
    assert {"kingpin_fl", "stub_press_fl", "hub_fl"} <= report.verified_interfaces
    assert report.retained >= set(PARTS) - {"fastener:bs_kl"}  # the stud is retained by the knuckle too
    kingpin = report.interfaces["kingpin_fl"]
    assert kingpin["radial_clearance_mm"] == pytest.approx(0.1, abs=1e-4)
    assert kingpin["engagement_mm"] == pytest.approx(12.0, abs=1e-3)
    assert report.interfaces["hub_fl"]["radial_clearance_mm"] == pytest.approx(0.025, abs=1e-4)
    assert report.interfaces["stub_press_fl"]["radial_clearance_mm"] == pytest.approx(-0.01, abs=1e-4)
    # Kingpin screw: 20 mm screw seated 0.15 mm above the knuckle, 7.85 mm of thread in the PA12 arm (>= 2 d).
    assert report.fasteners["kp_fl"]["engagement_mm"] == pytest.approx(7.85, abs=0.02)
    assert report.fasteners["arm_l1"]["engagement_mm"] == pytest.approx(7.0, abs=0.02)
    assert report.fasteners["arm_l1"]["head_contact_mm2"] > 10.0


def test_contact_alone_does_not_retain(project, model):
    """Without its two screws the arm only touches the chassis: it (and everything on it) is not retained."""
    _, result = _run(project, model, set(PARTS) - {"fastener:arm_l1", "fastener:arm_l2"})
    errors = [d for d in result.diagnostics if d.code == "part_not_retained"]
    assert errors and {"arm_fl", "knuckle_fl", "wheel_fl"} <= set(errors[0].subjects)


def test_fit_limits_map_to_diagnostics(project, model, tmp_path):
    spec = yaml.safe_load((RC_CAR / "robot.yaml").read_text())
    kingpin = next(i for i in spec["mechanical"]["interfaces"] if i["id"] == "kingpin_fl")
    kingpin["params"]["clearance"] = ["0.01 mm", "0.05 mm"]  # the 3.2 mm bore on a 3 mm shank is a 0.1 mm clearance
    arm = next(f for f in spec["mechanical"]["fasteners"] if f["id"] == "arm_l1")
    arm["thread_into"] = "knuckle_fl"  # the screw does not reach the knuckle: no coaxial tapped bore there
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    _, result = _run(Project.load(tmp_path), model)
    codes = _codes(result)
    assert "interface_fit_out_of_range" in codes
    assert "thread_geometry_missing" in codes
    failing = next(d for d in result.diagnostics if d.code == "interface_fit_out_of_range")
    assert failing.data["value"] == pytest.approx(1e-4, abs=1e-7) and failing.data["limits"] == pytest.approx([1e-5, 5e-5])
