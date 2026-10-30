"""Fit verification on geometry recorded from the phase B Onshape build (each
part's Part Studio bodydetails + tessellation, placed by the assembly's solved
transforms) of the front-left corner: fastener math, interface probes, retention
graph and the probe -> diagnostic mapping. No Onshape access."""

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_onshape.fit import verify_fit
from r3_onshape.model import load_local_fixture
from r3_onshape.organized import plan_assembly
from r3_onshape.parts import build_part_catalog

ROOT = Path(__file__).resolve().parents[1]
RECORDED_PROJECT = ROOT / "tests" / "fixtures" / "cad" / "front_left_corner_phaseb"
FIXTURE = ROOT / "tests" / "fixtures" / "cad" / "front_left_corner_phaseb.json.gz"
# The kingpin screw, wheel nut and ball stud are joined objects; the arm screws are plain fasteners.
PARTS = ["chassis", "arm_fl", "kp_fl", "knuckle_fl", "stub_fl", "wheel_fl", "nut_fl", "bs_kl", "fastener:arm_fl_mount_1",
         "fastener:arm_fl_mount_2"]


@pytest.fixture(scope="module")
def model():
    return load_local_fixture(FIXTURE)


@pytest.fixture(scope="module")
def project():
    return Project.load(RECORDED_PROJECT)


def _run(project, model, names=None):
    parts, instance_part = build_part_catalog(project)
    plan = plan_assembly(project, parts, instance_part)
    materials = {iid: parts[placement.key].plan.material for iid, placement in plan.placements.items()}
    if names is not None:
        model = copy.copy(model)
        model.parts = {n: p for n, p in model.parts.items() if n in names}
    result = StageResult(stage="cad")
    report = verify_fit(project, model, materials, result, scoped=True)
    return report, result


def _codes(result):
    return {d.code for d in result.diagnostics if d.severity.value == "error"}


def test_recorded_corner_verifies(project, model):
    assert set(PARTS) <= set(model.parts)
    report, result = _run(project, model)
    assert _codes(result) == set()
    assert {"kingpin_thread_fl", "arm_fl_mount_1", "arm_fl_mount_2", "wheel_nut_fl", "bs_kl"} <= report.verified_fasteners
    assert {"kingpin_fl", "stub_press_fl", "hub_fl"} <= report.verified_interfaces
    assert report.retained >= set(PARTS)
    kingpin = report.interfaces["kingpin_fl"]
    assert kingpin["radial_clearance_mm"] == pytest.approx(0.1, abs=1e-4)
    assert kingpin["engagement_mm"] == pytest.approx(12.0, abs=1e-3)
    assert report.interfaces["hub_fl"]["radial_clearance_mm"] == pytest.approx(0.025, abs=1e-4)
    press = report.interfaces["stub_press_fl"]
    assert press["radial_clearance_mm"] == pytest.approx(-0.025, abs=1e-4)
    assert press["overlap_mm3"] == pytest.approx(press["expected_overlap_mm3"], rel=1e-3)
    # Kingpin screw: 20 mm screw seated 0.15 mm above the knuckle, 7.85 mm of thread in the PA12 arm (>= 2 d).
    assert report.fasteners["kingpin_thread_fl"]["engagement_mm"] == pytest.approx(7.85, abs=0.02)
    assert report.fasteners["arm_fl_mount_1"]["engagement_mm"] == pytest.approx(7.0, abs=0.02)
    assert report.fasteners["arm_fl_mount_1"]["head_contact_mm2"] > 10.0
    assert report.fasteners["wheel_nut_fl"]["engagement_mm"] == pytest.approx(6.0, abs=0.02)


def test_contact_alone_does_not_retain(project, model):
    """Without its two screws the arm only touches the chassis: it (and everything on it) is not retained."""
    _, result = _run(project, model, set(PARTS) - {"fastener:arm_fl_mount_1", "fastener:arm_fl_mount_2"})
    errors = [d for d in result.diagnostics if d.code == "part_not_retained"]
    assert errors and {"arm_fl", "knuckle_fl", "wheel_fl"} <= set(errors[0].subjects)


def test_fit_limits_map_to_diagnostics(project, model, tmp_path):
    spec = yaml.safe_load((RECORDED_PROJECT / "robot.yaml").read_text())
    kingpin = next(j for j in spec["assembly"]["joins"] if j["id"] == "kingpin_fl")
    kingpin["verify"]["clearance"] = ["0.01 mm", "0.05 mm"]  # the 3.2 mm bore on a 3 mm shank is a 0.1 mm clearance
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    shutil.copytree(RECORDED_PROJECT / "catalog", tmp_path / "catalog")
    mutated = Project.load(tmp_path)
    arm = next(f for f in mutated.spec.mechanical.fasteners if f.id == "arm_fl_mount_1")
    arm.thread_into = "knuckle_fl"  # the screw does not reach the knuckle: no coaxial tapped bore there
    _, result = _run(mutated, model)
    codes = _codes(result)
    assert "interface_fit_out_of_range" in codes
    assert "thread_geometry_missing" in codes
    failing = next(d for d in result.diagnostics if d.code == "interface_fit_out_of_range")
    assert failing.data["value"] == pytest.approx(1e-4, abs=1e-7) and failing.data["limits"] == pytest.approx([1e-5, 5e-5])
