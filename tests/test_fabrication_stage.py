"""Fabrication reads synthetic native STEP/STL, never catalog shape programs."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pytest
import trimesh
import yaml
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from r3_fab import stage
from r3_model.project import Project
from pulled_fixture import write_pull


def make_project(root: Path, shapes: dict, fabrication=None):
    folder = root / 'catalog/components'
    folder.mkdir(parents=True)
    # Deliberately unrelated catalog envelope/material: they cannot become CAD.
    (folder / 'coupon.yaml').write_text(yaml.safe_dump({'components': [{'id': 'coupon', 'name': 'Synthetic coupon',
        'kind': 'structure', 'mechanical': {'size': [9,9,9], 'mass': 99, 'material': 'steel', 'fabricated': True}}]}))
    (root / 'robot.yaml').write_text(yaml.safe_dump({'robot': {'name': 'fabrication fixture', 'mobility': 'stationary'},
        'components': {name: {'type': 'coupon'} for name in shapes}, 'fabrication': fabrication or {}}))
    if shapes:
        write_pull(root, shapes)
    return Project.load(root)


def codes(result, code, subject=None):
    return [d for d in result.diagnostics if d.code == code and (subject is None or subject in d.subjects)]


def plate():
    shape = BRepPrimAPI_MakeBox(gp_Pnt(-60,-40,0),120,80,4).Shape()
    for x in (-40,40):
        drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x,0,-1),gp_Dir(0,0,1)),1.7,6).Shape()
        shape = BRepAlgoAPI_Cut(shape, drill).Shape()
    return shape


@pytest.fixture(scope='module')
def coupon(tmp_path_factory):
    project = make_project(tmp_path_factory.mktemp('native-fabrication'), {'coupon': plate()})
    return project, stage.run(project)


def test_material_and_geometry_come_from_pull(coupon):
    project, result = coupon
    plan = json.loads((project.root / 'build/fabrication/plan.json').read_text())
    entry = plan['parts']['coupon']
    assert entry['material'] == 'pla' and entry['geometry_source'] == 'pulled_stl'
    assert not codes(result, 'print_geometry_estimated')
    assert not codes(result, 'print_bed_exceeded')


def test_plate_prints_flat(coupon):
    project, result = coupon
    entry = json.loads((project.root / 'build/fabrication/plan.json').read_text())['parts']['coupon']
    assert abs(abs(entry['orientation']['build_direction'][2])-1) < 1e-9
    assert entry['height_mm'] == pytest.approx(4, abs=1e-6)
    assert entry['support_area_mm2'] == pytest.approx(0)
    assert not codes(result, 'print_overhang', 'coupon')


def test_missing_native_load_face_association_blocks_strength(coupon):
    _, result = coupon
    assert codes(result, 'print_strength_geometry_unavailable', 'coupon')
    assert not result.ok
    assert not codes(result, 'print_strength_not_applicable')


def test_bore_nominal_association_is_required(coupon):
    _, result = coupon
    finding = codes(result, 'printed_hole_nominal_missing', 'coupon')
    assert finding and finding[0].data['measured_bores'] == 2


def test_print_files_preserve_measured_holes_and_bed_placement(coupon):
    project, result = coupon
    folder = project.root / 'build/fabrication/coupon'
    for suffix in ('stl','3mf'):
        mesh = trimesh.load(folder / f'coupon.{suffix}', force='mesh')
        assert isinstance(mesh,trimesh.Trimesh) and len(mesh.faces)>0
        assert mesh.bounds[0,2] == pytest.approx(0, abs=1e-6)
        assert sorted(mesh.extents) == pytest.approx([4,80,120], abs=1e-3)
        assert mesh.volume == pytest.approx(120*80*4-2*np.pi*1.7**2*4, rel=2e-3)
    assert {a.kind for a in result.artifacts} >= {'stl','3mf','json','plot_png'}


def test_bed_overhang_and_thin_wall_checks_use_pulled_meshes(tmp_path):
    tube = BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(10,30).Shape(),BRepPrimAPI_MakeCylinder(9.5,30).Shape()).Shape()
    project = make_project(tmp_path, {'big':BRepPrimAPI_MakeBox(300,280,3).Shape(),
        'ball':BRepPrimAPI_MakeSphere(10).Shape(), 'tube':tube})
    result = stage.run(project)
    assert max(codes(result,'print_bed_exceeded','big')[0].data['extents']) == pytest.approx(.300,abs=1e-6)
    assert codes(result,'print_overhang','ball')[0].data['max_angle_deg'] > 45
    thin = codes(result,'print_wall_thin','tube')[0]
    assert thin.severity.value == 'error'
    assert thin.data['min_thickness'] == pytest.approx(.5e-3,abs=.05e-3)
    assert not result.ok


def test_orientation_override_and_unknown_printer(tmp_path):
    project = make_project(tmp_path, {'ball':BRepPrimAPI_MakeSphere(10).Shape()},
        {'printer':'no-such-printer','parts':{'ball':{'orientation':[90,0,0]}}})
    result = stage.run(project)
    assert codes(result,'printer_unknown')
    entry = json.loads((project.root / 'build/fabrication/plan.json').read_text())['parts']['ball']
    assert entry['orientation']['override'] is True
    assert entry['orientation']['rotation_deg'] == pytest.approx([90,0,0])


def test_material_override_cannot_change_native_material(tmp_path):
    project = make_project(tmp_path, {'coupon':BRepPrimAPI_MakeBox(10,10,4).Shape()},
        {'parts':{'coupon':{'material':'petg'}}})
    result = stage.run(project)
    assert codes(result,'print_material_differs_from_pull')
    entry = json.loads((project.root / 'build/fabrication/plan.json').read_text())['parts']['coupon']
    assert entry['material'] == 'pla'


def test_print_false_is_not_applicable(tmp_path):
    project = make_project(tmp_path, {'coupon':BRepPrimAPI_MakeBox(10,10,4).Shape()},
        {'parts':{'coupon':{'print':False}}})
    result = stage.run(project)
    assert result.ok and codes(result,'not_applicable')


def test_blank_project_is_not_applicable(tmp_path):
    result = stage.run(make_project(tmp_path, {}))
    assert result.ok and [d.code for d in result.diagnostics] == ['not_applicable']
