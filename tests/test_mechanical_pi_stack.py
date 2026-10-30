"""GPIO insertion is measured from the socket mouth, including the seating gap."""
from pathlib import Path

import numpy as np
import pytest

from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_onshape.fit import verify_fit
from r3_onshape.model import GeometryModel
from r3_onshape.organized import plan_assembly, transform_part
from r3_onshape.parts import build_part_catalog
from r3_onshape.recompose import part_from_plan

PROJECT = Path(__file__).resolve().parents[1] / "examples/robots/rc-car"


def _stack(hat_shift: float = 0.0):
    project = Project.load(PROJECT)
    parts, instances = build_part_catalog(project)
    plan = plan_assembly(project, parts, instances)
    geometry = {}
    materials = {}
    for name in ("pi", "hat"):
        placement = plan.placements[name]
        part = parts[placement.key]
        pose = placement.pose.copy()
        if name == "hat":
            pose[2, 3] += hat_shift
        geometry[name] = transform_part(part_from_plan(name, part.plan), pose, name)
        materials[name] = part.plan.material
    result = StageResult(stage="cad")
    model = GeometryModel(geometry, source="local test plans")
    report = verify_fit(project, model, materials, result, scoped=True)
    return project, model, report, result


def test_shimmed_pi_stack_has_real_gpio_engagement_and_no_interference():
    project, model, report, result = _stack()
    assert not project.assembly.errors
    assert not result.errors, [d.message for d in result.errors]
    fit = report.interfaces["gpio_header"]
    assert fit["ok"] and fit["pins_inside_cavity"]
    # The legacy offline header plan rounds the measured STEP datum by 4 um.
    assert fit["insertion_mm"] == pytest.approx(5.774, abs=0.001)
    # Harwin M20-783 drawing: contact point 3.41 +/- 0.25 mm from the mouth.
    measured_insertion = 6.0 - 0.230
    assert measured_insertion - 3.41 == pytest.approx(2.360, abs=0.001)
    assert measured_insertion - (3.41 + 0.25) > 2.1
    assert fit["lateral_offset_mm"] == pytest.approx(0, abs=0.001)
    assert (model["pi"].manifold() ^ model["hat"].manifold()).volume() == pytest.approx(0, abs=1e-12)
    header = project.graph.endpoints["pi.J8"].position
    socket = project.graph.endpoints["hat.J_GPIO"].position
    assert (socket - header) == pytest.approx([0, 0, 0.000230], abs=1e-9)
    interface = next(i for i in project.spec.mechanical.interfaces if i.id == "gpio_header")
    np.testing.assert_allclose(interface.axis_point, socket, atol=1e-12)


def test_omitting_shims_really_collides_with_header_plastic():
    # Simulate assembling the same parts without the three-tenths shim stack.
    _, model, _, result = _stack(hat_shift=-0.0003)
    assert (model["pi"].manifold() ^ model["hat"].manifold()).volume() * 1e9 == pytest.approx(0.677, abs=0.02)
    assert any(d.code in {"connector_misaligned", "component_interference"} for d in result.errors)
