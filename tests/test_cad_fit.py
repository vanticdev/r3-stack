"""Unchanged fit limits checked against explicit synthetic STEP solids."""
from types import SimpleNamespace
import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from r3_model.diagnostics import StageResult
from r3_model.spec import Interface
from r3_model.pulled import load_pulled
from r3_onshape.fit import FitChecker, VOLUME_TOL, plane_contacts
from r3_onshape.step_geometry import geometry_from_pulled
from pulled_fixture import write_pull


def cylinder(radius, height, z=0):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0,0,z),gp_Dir(0,0,1)),radius,height).Shape()


def checker(tmp_path, radius=3.1, length=8):
    shapes = {'shaft': cylinder(3, 8), 'hub': BRepAlgoAPI_Cut(cylinder(6, length), cylinder(radius, length+2,-1)).Shape()}
    write_pull(tmp_path, shapes)
    model = geometry_from_pulled(load_pulled(tmp_path))
    project = SimpleNamespace(spec=SimpleNamespace(components={}, assembly=None), catalog=SimpleNamespace())
    result = StageResult(stage='cad')
    return FitChecker(project,model,{},result), Interface(id='axis',kind='revolute_fit',parts=['shaft','hub'],
        axis_point=[0,0,0],axis_dir=[0,0,1],params={'clearance':[.00005,.00015],'min_engagement':.008})


def test_step_running_fit_verifies_and_reports_measured_geometry(tmp_path):
    check, interface = checker(tmp_path)
    check.check_interface(interface)
    check.check_interference()
    assert not check.result.errors
    assert check.report.verified_interfaces == {'axis'}
    assert check.report.interfaces['axis']['radial_clearance_mm'] == pytest.approx(.1)
    assert check.report.interfaces['axis']['engagement_mm'] == pytest.approx(8)
    assert check.report.interface_axes['axis']['axis'] == pytest.approx([0,0,1])


def test_fit_limits_map_to_diagnostics(tmp_path):
    check, interface = checker(tmp_path)
    interface.params['clearance'] = [.00001,.00005]
    check.check_interface(interface)
    error = next(d for d in check.result.errors if d.code == 'interface_fit_out_of_range')
    assert error.data['value'] == pytest.approx(.0001)
    assert error.data['limits'] == pytest.approx([.00001,.00005])


def test_short_axial_engagement_fails(tmp_path):
    check, interface = checker(tmp_path,length=4)
    check.check_interface(interface)
    assert 'interface_engagement_short' in {d.code for d in check.result.errors}


def test_offset_bore_does_not_verify(tmp_path):
    check, interface = checker(tmp_path)
    interface.axis_point = [.001,0,0]
    check.check_interface(interface)
    assert 'interface_geometry_missing' in {d.code for d in check.result.errors}


def test_running_interference_is_not_relaxed(tmp_path):
    check, interface = checker(tmp_path,radius=2.99)
    check.check_interface(interface)
    check.check_interference()
    assert check.intersection('shaft','hub') > VOLUME_TOL
    assert {'interface_fit_out_of_range','cad_interference'} <= {d.code for d in check.result.errors}


def test_contact_alone_does_not_retain(tmp_path):
    check, _ = checker(tmp_path)
    check.check_retention(root='shaft')
    assert check.report.retained == {'shaft'}
    assert next(d for d in check.result.errors if d.code == 'part_not_retained').subjects == ['hub']


def test_press_fit_uses_measured_intersection(tmp_path):
    check, interface = checker(tmp_path,radius=2.99)
    interface.kind='press_fit'
    interface.params={'interference':[.000005,.000015],'min_engagement':.008}
    check.check_interface(interface)
    assert not check.result.errors
    assert check.report.interfaces['axis']['radial_clearance_mm'] == pytest.approx(-.01)
    assert check.report.interfaces['axis']['overlap_mm3'] == pytest.approx(check.report.interfaces['axis']['expected_overlap_mm3'],rel=.01)


def test_step_plane_contact_area(tmp_path):
    poses = {'a':np.eye(4),'b':np.eye(4)};poses['b'][2,3]=.01
    write_pull(tmp_path,{n:BRepPrimAPI_MakeBox(10,20,10).Shape() for n in poses},poses=poses)
    model=geometry_from_pulled(load_pulled(tmp_path))
    contacts=plane_contacts(model['a'],model['b'])
    assert len(contacts)==1
    assert contacts[0].area==pytest.approx(.0002,rel=1e-5)


def test_thread_geometry_check_still_runs_and_missing_definition_blocks(tmp_path):
    check, _ = checker(tmp_path,radius=2.5)
    fastener=SimpleNamespace(id='thread',part='shaft')
    kind=SimpleNamespace(d=.006,tap_drill=.005,pitch=.001)
    info={}
    check.check_thread(fastener,kind,'shaft','hub',np.zeros(3),np.array([0,0,1]),False,info)
    assert info['geometric_engagement_mm']==pytest.approx(8)
    assert info['engagement_mm']==pytest.approx(8,rel=.01)
    assert {'thread_definition_unavailable','thread_engagement_short'} <= {d.code for d in check.result.errors}
