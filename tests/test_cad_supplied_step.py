"""A source-only purchased part must not become its bounding-box proxy."""
import numpy as np
import pytest

from r3_connectors.frames import Frame
from r3_onshape.geometry import PartPlan
from r3_onshape.parts import LocalPart, world_manifold
from r3_onshape.step_geometry import read_step


def test_supplied_plate_keeps_real_thickness_hole_and_seat_faces(tmp_path, monkeypatch):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from r3_catalog.schema import ModelRef
    from r3_connectors import models

    blank = BRepPrimAPI_MakeBox(10, 8, 1).Shape()
    drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(3, 4, -1), gp_Dir(0, 0, 1)), 1.5, 3).Shape()
    shape = BRepAlgoAPI_Cut(blank, drill).Shape()
    path = tmp_path / "sensor.step"
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    assert writer.Write(str(path)) == IFSelect_RetDone
    frame = Frame.from_axes([.02, .03, .04], [0, 1, 0], [0, 0, 1])
    part = read_step("synthetic sensor", path, frame)
    assert part.manifold().volume() * 1e9 == pytest.approx(80 - np.pi * 1.5**2, abs=.02)
    np.testing.assert_allclose(part.hi - part.lo, [.008, .010, .001], atol=1e-9)
    holes = [f for f in part.faces_of("CYLINDER") if f.convex is False]
    assert len(holes) == 1 and holes[0].radius == pytest.approx(.0015)
    assert any(f.origin[2] == pytest.approx(.041) and f.direction[2] > .99
               for f in part.faces_of("PLANE"))

    # An empty purchased fallback plan must still produce the real supplied solid.
    monkeypatch.setattr(models, "resolve_step", lambda ref: path)
    ref = ModelRef(source="vendor", placement={"origin": frame.origin.tolist(), "x": frame.x.tolist(), "z": frame.z.tolist()})
    local = LocalPart("fixture", "fixture", "fixture", "sensor",
                     PartPlan("fixture", "purchased", "board", 0), source_model=ref)
    solid = world_manifold(local, np.eye(4))
    assert solid.volume() == pytest.approx(part.manifold().volume(), rel=1e-6)


def test_supplied_sphere_keeps_center_and_radius_after_placement(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.gp import gp_Pnt
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    path = tmp_path / 'ball.step'
    writer = STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeSphere(gp_Pnt(2, 3, 4), 5).Shape(), STEPControl_AsIs)
    writer.Write(str(path))
    frame = Frame.from_axes([.02, .03, .04], [0, 1, 0], [1, 0, 0])
    part = read_step('synthetic ball', path, frame)
    spheres = part.faces_of('SPHERE')
    assert spheres
    for face in spheres:
        assert face.radius == pytest.approx(.005)
        np.testing.assert_allclose(face.origin, frame.point([.002, .003, .004]))
    assert part.manifold().volume() == pytest.approx(4 / 3 * np.pi * .005**3, rel=.01)
