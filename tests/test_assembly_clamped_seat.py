"""A declared spacer must physically reach both clamp seats in pulled STEP."""
from types import SimpleNamespace
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from r3_catalog import default_catalog
from r3_model.diagnostics import StageResult
from r3_model.pulled import load_pulled
from r3_model.spec import Fastener
from r3_onshape.fit import FitChecker
from r3_onshape.step_geometry import geometry_from_pulled
from pulled_fixture import write_pull


@pytest.mark.parametrize('declared,height,x,expected', [
    (True, 5, 0, False), (True, 4, 0, True), (True, 5, 30, True), (False, 5, 0, True),
])
def test_only_a_contacting_declared_spacer_explains_seat_offset(tmp_path, declared, height, x, expected):
    def box(z, depth, shift=0):
        block = BRepPrimAPI_MakeBox(gp_Pnt(shift-10, -10, z), 20, 20, depth).Shape()
        hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -5), gp_Dir(0, 0, 1)), 1.7, 30).Shape()
        return BRepAlgoAPI_Cut(block, hole).Shape()
    head = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 9), gp_Dir(0, 0, 1)), 3, 3).Shape()
    shank = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 9), gp_Dir(0, 0, -1)), 1.5, 20).Shape()
    shapes = {'base': box(0, 2), 'cap': box(7, 2), 'spacer': box(2, height, x),
              'fastener:clamp': BRepAlgoAPI_Fuse(head, shank).Shape()}
    write_pull(tmp_path, shapes)
    geometry = geometry_from_pulled(load_pulled(tmp_path))
    project = SimpleNamespace(spec=SimpleNamespace(components={}), catalog=default_catalog())
    result = StageResult(stage='assembly')
    checker = FitChecker(project, geometry, {}, result)
    fastener = Fastener(id='clamp', type='shcs-m3', length=.02, position=[0, 0, .009], axis=[0, 0, -1],
                        clamps=['cap', *(['spacer'] if declared else []), 'base'])
    checker.check_fastener(fastener)
    assert any(d.code == 'fastener_joint_gap' for d in result.diagnostics) is expected
