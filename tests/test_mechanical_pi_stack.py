"""Connector engagement and collision are measured from the physical socket mouth."""
from pathlib import Path

import numpy as np
import pytest

from cad_helpers import geometry_for
from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_onshape.fit import verify_fit

PROJECT = Path(__file__).parent / "fixtures/connector_stack"


def _stack(tmp_path, socket_shift: float = 0.0):
    import shutil
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut
    from OCP.gp import gp_Pnt
    from pulled_fixture import write_pull
    root = tmp_path / "stack"
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns("build", "cad"))
    project = Project.load(root)
    project.spec.components["socket"].pose.xyz[2] += socket_shift
    interface = project.spec.mechanical.interfaces[0]
    interface.axis_point[2] += socket_shift
    header = BRepAlgoAPI_Fuse(BRepPrimAPI_MakeBox(gp_Pnt(-28,-5,0),56,10,2).Shape(),
                             BRepPrimAPI_MakeBox(gp_Pnt(-25,-2.5,2),50,5,6).Shape()).Shape()
    socket = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(gp_Pnt(-28,-5,0),56,10,8).Shape(),
                            BRepPrimAPI_MakeBox(gp_Pnt(-25.2,-2.7,-1),50.4,5.4,8).Shape()).Shape()
    poses = {name:np.eye(4) for name in ("header","socket")}
    poses['socket'][2,3] = .0022+socket_shift
    write_pull(root, {'header':header,'socket':socket}, poses=poses)
    model, materials = geometry_for(project)
    result = StageResult(stage="cad")
    report = verify_fit(project, model, materials, result, scoped=True)
    return project, model, report, result


def test_spaced_stack_has_real_engagement_and_no_interference(tmp_path):
    project, model, report, result = _stack(tmp_path)
    assert not result.errors, [d.message for d in result.errors]
    fit = report.interfaces["contact"]
    assert fit["ok"] and fit["pins_inside_cavity"]
    # Header top 8 mm minus socket mouth at 2.2 mm.
    assert fit["insertion_mm"] == pytest.approx(5.8, abs=.001)
    assert fit["lateral_offset_mm"] == pytest.approx(0, abs=.001)
    assert (model["header"].manifold() ^ model["socket"].manifold()).volume() == pytest.approx(0, abs=1e-12)
    np.testing.assert_allclose(project.spec.mechanical.interfaces[0].axis_point,
                               project.spec.components["socket"].pose.xyz, atol=1e-12)


def test_omitting_spacing_really_collides_with_header_plastic(tmp_path):
    _, model, _, result = _stack(tmp_path, socket_shift=-.0003)
    # A 0.1 mm slice of the socket rim enters the header base.
    expected_mm3 = (.056 * .010 - .0504 * .0054) * .0001 * 1e9
    assert (model["header"].manifold() ^ model["socket"].manifold()).volume() * 1e9 == pytest.approx(expected_mm3, abs=.02)
    assert any(d.code in {"connector_misaligned", "cad_interference"} for d in result.errors)
