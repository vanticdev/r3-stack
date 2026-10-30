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
from cad_helpers import geometry_for
from r3_onshape.organized import plan_assembly
from r3_onshape.parts import build_part_catalog

ROOT = Path(__file__).resolve().parents[1]
COUPON = ROOT / "tests/fixtures/mechanism"
PARTS = {"base", "support", "sleeve", "rotor", "fastener:support_mount_1", "fastener:support_mount_2"}


@pytest.fixture(scope="module")
def model(project):
    return geometry_for(project)[0]


@pytest.fixture(scope="module")
def project():
    return Project.load(COUPON)


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


def test_generated_coupon_verifies(project, model):
    assert PARTS == set(model.parts)
    report, result = _run(project, model)
    assert _codes(result) == set()
    assert report.verified_fasteners == {"support_mount_1", "support_mount_2"}
    assert report.verified_interfaces == {"sleeve_press", "rotor_pivot"}
    assert report.retained == PARTS
    assert report.interfaces["rotor_pivot"]["radial_clearance_mm"] == pytest.approx(.1, abs=1e-4)
    assert report.interfaces["rotor_pivot"]["engagement_mm"] == pytest.approx(8, abs=1e-3)
    press = report.interfaces["sleeve_press"]
    assert press["radial_clearance_mm"] == pytest.approx(-.01, abs=1e-4)
    assert press["overlap_mm3"] == pytest.approx(press["expected_overlap_mm3"], rel=1e-3)
    for fastener in report.fasteners.values():
        assert fastener["engagement_mm"] == pytest.approx(8, abs=.02)
        assert fastener["head_contact_mm2"] > 10


def test_contact_alone_does_not_retain(project, model):
    _, result = _run(project, model, PARTS - {"fastener:support_mount_1", "fastener:support_mount_2"})
    errors = [d for d in result.errors if d.code == "part_not_retained"]
    assert errors and {"support", "sleeve", "rotor"} <= set(errors[0].subjects)


def test_fit_limits_map_to_diagnostics(project, model, tmp_path):
    mutated = copy.deepcopy(project)
    interface = next(i for i in mutated.spec.mechanical.interfaces if i.id == "rotor_pivot")
    interface.params["clearance"] = [.00001, .00005]
    mutated.spec.mechanical.fasteners[0].thread_into = "rotor"
    _, result = _run(mutated, model)
    assert {"interface_fit_out_of_range", "thread_geometry_missing"} <= _codes(result)
    failing = next(d for d in result.errors if d.code == "interface_fit_out_of_range")
    assert failing.data["value"] == pytest.approx(1e-4, abs=1e-7)
    assert failing.data["limits"] == pytest.approx([1e-5, 5e-5])
