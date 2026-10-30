"""Connector engagement and collision are measured from the physical socket mouth."""
from pathlib import Path

import numpy as np
import pytest

from cad_helpers import geometry_for
from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_onshape.fit import verify_fit

PROJECT = Path(__file__).parent / "fixtures/connector_stack"


def _stack(socket_shift: float = 0.0):
    project = Project.load(PROJECT)
    project.spec.components["socket"].pose.xyz[2] += socket_shift
    interface = project.spec.mechanical.interfaces[0]
    interface.axis_point[2] += socket_shift
    model, materials = geometry_for(project)
    result = StageResult(stage="cad")
    report = verify_fit(project, model, materials, result, scoped=True)
    return project, model, report, result


def test_spaced_stack_has_real_engagement_and_no_interference():
    project, model, report, result = _stack()
    assert not result.errors, [d.message for d in result.errors]
    fit = report.interfaces["contact"]
    assert fit["ok"] and fit["pins_inside_cavity"]
    # Header top 8 mm minus socket mouth at 2.2 mm.
    assert fit["insertion_mm"] == pytest.approx(5.8, abs=.001)
    assert fit["lateral_offset_mm"] == pytest.approx(0, abs=.001)
    assert (model["header"].manifold() ^ model["socket"].manifold()).volume() == pytest.approx(0, abs=1e-12)
    np.testing.assert_allclose(project.spec.mechanical.interfaces[0].axis_point,
                               project.spec.components["socket"].pose.xyz, atol=1e-12)


def test_omitting_spacing_really_collides_with_header_plastic():
    _, model, _, result = _stack(socket_shift=-.0003)
    # A 0.1 mm slice of the socket rim enters the header base.
    expected_mm3 = (.056 * .010 - .0504 * .0054) * .0001 * 1e9
    assert (model["header"].manifold() ^ model["socket"].manifold()).volume() * 1e9 == pytest.approx(expected_mm3, abs=.02)
    assert any(d.code in {"connector_misaligned", "cad_interference"} for d in result.errors)
