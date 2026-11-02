"""Fastener qualification selects native datums, never local CAD placements."""
import hashlib
import json

import numpy as np
import pytest
import yaml
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from r3_model.project import Project
from r3_model.pulled import PullError
from r3_model.pulled_view import apply_pulled, cad_view
from r3_model.spec import Fastener
from r3_orchestrator.spec_ops import get_spec, patch_spec
from pulled_fixture import write_pull
from test_cad_pulled_consumers import project


def native_fixture(project):
    path = project.root/'robot.yaml'
    data = yaml.safe_load(path.read_text())
    data['components']['target'] = {'type':'synthetic-coupon'}
    data['components']['retaining-member'] = {'type':'synthetic-coupon'}
    path.write_text(yaml.safe_dump(data))
    pose = np.array([[1,0,0,.1],[0,0,-1,.2],[0,1,0,.3],[0,0,0,1.]])
    seat = np.array([[0,0,1,.002],[0,1,0,.003],[-1,0,0,.004],[0,0,0,1.]])
    shapes = {name:BRepPrimAPI_MakeBox(4,4,4).Shape() for name in data['components']}
    poses = {name:np.eye(4) for name in shapes};poses['coupon'] = pose
    connectors = {name:{'origin':np.eye(4)} for name in shapes}
    connectors['coupon']['seat'] = seat
    write_pull(project.root, shapes, poses=poses, connectors=connectors)
    return shapes, poses, connectors, pose @ seat


def add_fastener(project, **extra):
    definition = {'id':'native-clamp','type':'shcs-m3','body':'coupon',
                  'mate_connector':'seat','clamps':['target'],'length':.02,**extra}
    read = get_spec(project.root)
    result = patch_spec(project.root, {'mechanical':{'fasteners':[definition]}}, read['sha256'])
    assert result['ok'], result
    return definition


def test_native_fastener_frame_is_composed_from_pinned_body_and_connector(project):
    _, _, _, frame = native_fixture(project)
    definition = add_fastener(project)
    manifest = project.root/'cad/pulled/pull.json'
    before = hashlib.sha256(manifest.read_bytes()).hexdigest()
    current = Project.load(project.root)
    f = current.spec.mechanical.fasteners[0]
    np.testing.assert_allclose(f.position, frame[:3,3], atol=1e-12)
    np.testing.assert_allclose(f.axis, frame[:3,2], atol=1e-12)
    evidence = current.assembly.to_json()['fastener_frames']['native-clamp']
    assert evidence['body'] == 'coupon' and evidence['mate_connector'] == 'seat'
    assert evidence['source'] == 'pulled'
    np.testing.assert_allclose(evidence['matrix'], frame, atol=1e-12)
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == before
    authored = yaml.safe_load((project.root/'robot.yaml').read_text())['mechanical']['fasteners'][0]
    assert authored == definition and 'position' not in authored and 'axis' not in authored


def test_repull_changes_only_the_derived_fastener_frame(project):
    shapes, poses, connectors, _ = native_fixture(project)
    definition = add_fastener(project)
    poses['coupon'][:3,3] = [-.2,.4,.7]
    write_pull(project.root, shapes, poses=poses, connectors=connectors)
    current = Project.load(project.root)
    frame = poses['coupon'] @ connectors['coupon']['seat']
    np.testing.assert_allclose(current.spec.mechanical.fasteners[0].position, frame[:3,3], atol=1e-12)
    assert yaml.safe_load((project.root/'robot.yaml').read_text())['mechanical']['fasteners'][0] == definition


def test_native_nut_identity_is_preserved(project):
    native_fixture(project)
    add_fastener(project,nut='nut-m3-nyloc',nut_body='retaining-member',grip=.01)
    f = Project.load(project.root).spec.mechanical.fasteners[0]
    assert f.part == 'coupon' and f.nut_part == 'retaining-member'


@pytest.mark.parametrize('override', [{'position':[0,0,0]},{'axis':[1,0,0]},{'body':None}])
def test_native_selector_rejects_authored_placement_and_missing_body(override):
    with pytest.raises(ValueError):
        Fastener.model_validate({'id':'fixture','type':'shcs-m3','body':'coupon',
                                 'mate_connector':'seat',**override})


def test_missing_native_seat_never_uses_a_default_frame(project):
    native_fixture(project)
    add_fastener(project,mate_connector='absent')
    current = Project.load(project.root)
    for consumer in (apply_pulled,cad_view):
        with pytest.raises(PullError) as error:
            consumer(current)
        assert error.value.code == 'cad_native_connector_missing'


def test_legacy_fastener_still_requires_position():
    with pytest.raises(ValueError):
        Fastener.model_validate({'id':'fixture','type':'shcs-m3'})
    legacy = Fastener.model_validate({'id':'fixture','type':'shcs-m3','position':[1,2,3]})
    assert legacy.axis == [0,0,-1]


def native_bolt_stack(tmp_path, *, nut=True):
    from test_joint_physics import fixture_project
    from pulled_fixture import publish_project
    from r3_model.pulled import load_pulled
    from r3_onshape.step_geometry import read_step_shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf,gp_Pnt
    p = fixture_project(tmp_path,nut=nut)
    f = p.spec.mechanical.fasteners[0]
    pulled = load_pulled(p.root)
    scale = gp_Trsf();scale.SetScale(gp_Pnt(0,0,0),1000)
    shapes = {name:BRepBuilderAPI_Transform(read_step_shape(part.step),scale,True).Shape()
              for name,part in pulled.parts.items()}
    head = np.eye(4);head[:3,:3] = np.diag([1,-1,-1]);head[:3,3] = f.position
    frames = {name:dict(part.mate_connectors) for name,part in pulled.parts.items()}
    frames[f.part]['head-seat'] = np.linalg.inv(pulled.poses[f.part]) @ head
    # The native mate tree attaches the two plates directly; physical bolt
    # capacity links them through the actual screw, independent of that tree.
    attachments = [('plates','fastened','base','part',[0,0,0],[0,0,1],None),
                   ('bolt','fastened','base',f.part,[0,0,0],[0,0,1],None)]
    if nut:
        attachments.append(('nut','fastened','base',f.nut_part,[0,0,0],[0,0,1],None))
    p = publish_project(p,shapes,mass={n:v.mass for n,v in pulled.parts.items()},
                        material={n:v.material for n,v in pulled.parts.items()},
                        attachments=attachments,connector_frames=frames)
    read = get_spec(p.root)
    definition = read['spec']['mechanical']['fasteners'][0]
    definition.pop('position');definition.pop('axis')
    definition.update(body=f.part,mate_connector='head-seat')
    if nut:definition['nut_body'] = f.nut_part
    result = patch_spec(p.root,{'mechanical':{'fasteners':[definition]}},read['sha256'])
    assert result['ok'],result
    return Project.load(p.root)


def test_native_bolt_capacities_survive_a_different_cad_mate_tree(tmp_path):
    from r3_model.connections import compile_connections
    from r3_sim.pulled_connections import pulled_connections
    from test_joint_physics import vehicle_for,run_steps
    p = native_bolt_stack(tmp_path)
    authored = {c.id:c for c in compile_connections(p).connections if c.kind=='screw'}
    actual = pulled_connections(p)
    retained = {c.id:c for c in actual.connections}
    assert authored.keys() <= retained.keys()
    for key,expected in authored.items():
        c = retained[key]
        assert c.axial == expected.axial and c.shear == expected.shear
        assert c.coefficients == expected.coefficients
        np.testing.assert_allclose(c.point,expected.point,atol=1e-12)
        np.testing.assert_allclose(c.axis,expected.axis,atol=1e-12)
    assert not [d for d in actual.diagnostics if d.severity.value=='error']
    support = [d for d in actual.diagnostics if d.code=='cad_mate_retention_path']
    assert support and all(d.data['connections'] for d in support)
    v = vehicle_for(p,'gravity_inverted');run_steps(v,1000)
    assert not v.joints.events
    assert not [d for d in v.joints.diagnostics if d.severity.value=='error']


def test_native_bolt_without_retainer_still_fails_and_detaches(tmp_path):
    from r3_sim.pulled_connections import pulled_connections
    from test_joint_physics import vehicle_for,run_steps
    p = native_bolt_stack(tmp_path,nut=False)
    actual = pulled_connections(p)
    assert any(d.code=='joint_capacity_zero' for d in actual.diagnostics)
    assert any(d.code=='joint_capacity_unknown' for d in actual.diagnostics)
    v = vehicle_for(p,'gravity_inverted');run_steps(v,1000)
    assert any(d.code=='part_detached' for d in v.joints.diagnostics)
    assert abs(v.body_pose('part')[0][2]-v.body_pose('base')[0][2])>.05


def test_unknown_native_fastener_strength_cannot_qualify_a_mate_path(tmp_path):
    from r3_sim.pulled_connections import pulled_connections
    p = native_bolt_stack(tmp_path)
    p.catalog.fasteners['shcs-m3'].joint_physics.pop('proof_stress',None)
    p.catalog.joint_defaults.pop('proof_stress',None)
    actual = pulled_connections(p)
    assert any(d.code=='joint_capacity_unknown' for d in actual.diagnostics)
    assert not any(d.code=='cad_mate_retention_path' for d in actual.diagnostics)


def test_native_fastener_unknown_body_and_nut_fail_typed_authoring(project):
    native_fixture(project)
    read = get_spec(project.root)
    for extra in ({'body':'absent'}, {'nut':'nut-m3-nyloc','nut_body':'absent','grip':.01}):
        definition = {'id':'fixture','type':'shcs-m3','body':'coupon',
                      'mate_connector':'seat','clamps':['target'],**extra}
        reply = patch_spec(project.root,{'mechanical':{'fasteners':[definition]}},read['sha256'])
        assert not reply['ok']
        assert get_spec(project.root)['sha256'] == read['sha256']


def test_native_fastener_cannot_glue_an_unmated_part(tmp_path):
    from r3_model.pulled import load_pulled
    from r3_onshape.step_geometry import read_step_shape
    from r3_sim.pulled_connections import pulled_connections
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf,gp_Pnt
    p = native_bolt_stack(tmp_path)
    native = load_pulled(p.root)
    scale=gp_Trsf();scale.SetScale(gp_Pnt(0,0,0),1000)
    shapes={name:BRepBuilderAPI_Transform(read_step_shape(part.step),scale,True).Shape()
            for name,part in native.parts.items()}
    manifest=json.loads((p.root/'cad/pulled/pull.json').read_text())
    write_pull(p.root,shapes,poses=native.poses,
               connectors={name:part.mate_connectors for name,part in native.parts.items()},
               mates=[mate for mate in manifest['mates'] if mate['id']!='plates'],
               mass={name:part.mass for name,part in native.parts.items()},
               material={name:part.material for name,part in native.parts.items()})
    actual=pulled_connections(Project.load(p.root))
    assert any(d.code=='cad_fastener_attachment_missing' for d in actual.diagnostics)
    assert not any(c.kind=='screw' and 'part' in (c.a,c.b) for c in actual.connections)


def test_native_fastener_cannot_name_itself_as_its_nut():
    with pytest.raises(ValueError,match='different native components'):
        Fastener.model_validate({'id':'fixture','type':'shcs-m3','body':'coupon',
                                 'mate_connector':'seat','nut':'nut-m3-nyloc','nut_body':'coupon','grip':.01})
