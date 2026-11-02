"""Connector consumers use the pinned native mesh in every acquisition mode."""
import json

import numpy as np
import trimesh
import yaml

from r3_connectors.stage import ConnectorStage, run
from r3_model.diagnostics import StageResult
from r3_model.project import Project
from r3_model.pulled import load_pulled
from test_cad_pulled_consumers import project
from test_cad_pull import native_snapshot
from test_native_mesh_prototype import install


def test_connector_components_render_from_pinned_obj_without_step(project, native_snapshot):
    native = install(project, native_snapshot)
    result = run(Project.load(project.root), render=False)
    assert not result.errors
    assert any(d.code == 'cad_geometry_estimated' for d in result.diagnostics)
    data = json.loads((project.root/'build/connectors/connectors.json').read_text())
    item = data['components']['coupon']
    assert item['source'] == 'pulled'
    assert item['geometry_qualification'] == 'display_mesh'
    assert item['source_sha256'] == load_pulled(project.root).parts['coupon'].sha256['obj']
    mesh = trimesh.load_scene(project.root/'build/connectors'/item['glb']).to_mesh()
    original = trimesh.load_mesh(load_pulled(project.root).parts['coupon'].mesh, process=False)
    assert len(mesh.faces) == len(original.faces)
    np.testing.assert_allclose(mesh.bounds, original.bounds, atol=1e-9)
    assert load_pulled(project.root).parts['coupon'].mesh.read_bytes() == native.raw


def test_mesh_pair_reads_native_obj_and_does_not_qualify_a_plain_block_as_contacts(project, native_snapshot):
    path = project.root/'catalog/components/coupon.yaml'
    body = yaml.safe_load(path.read_text())
    body['components'][0]['connectors'] = {name: {'type':'header-1x3-2.54','pins':{}} for name in ('A','B')}
    path.write_text(yaml.safe_dump(body))
    spec = yaml.safe_load((project.root/'robot.yaml').read_text())
    spec['components']['coupon']['onshape'] = {'mate_connectors':{'A':'origin','B':'origin'}}
    (project.root/'robot.yaml').write_text(yaml.safe_dump(spec))
    install(Project.load(project.root), native_snapshot)
    current = Project.load(project.root)
    result = StageResult(stage='connectors')
    stage = ConnectorStage(current, result)
    stage.component_connectors()
    stage.mesh_pair('coupon.A','coupon.B','direct')
    check = stage.data['checks']['mesh_mates']['coupon.A|coupon.B']
    assert check['status'] == 'unverifiable'
    assert [d.code for d in result.diagnostics] == ['connector_unverifiable', 'connector_unverifiable']
    assert load_pulled(project.root).parts['coupon'].sha256['obj'] in json.dumps(check)


def test_composite_connector_projection_uses_pinned_gltf_bodies(project, native_snapshot):
    from r3_onshape.pull import pull
    from test_cad_composite_exports import composite_transport
    from r3_model.mesh_io import load_exported_mesh

    path = project.root/'robot.yaml'
    description = yaml.safe_load(path.read_text())
    description['components']['body'] = description['components'].pop('coupon')
    path.write_text(yaml.safe_dump(description))
    project = Project.load(project.root)
    transport, _ = composite_transport(native_snapshot)
    pulled = pull(project, url="https://cad.onshape.com/documents/doc/v/version/e/assembly",
                  transport=transport)
    assert pulled['ok'], pulled
    result = run(Project.load(project.root), render=False)
    assert not result.errors
    native = load_pulled(project.root).parts['body']
    data = json.loads((project.root/'build/connectors/connectors.json').read_text())
    item = data['components']['body']
    assert item['source_sha256'] == native.sha256['gltf']
    assert item['geometry_qualification'] == 'exported_solid'
    mesh = trimesh.load_scene(project.root/'build/connectors'/item['glb'], process=False).to_mesh()
    original = load_exported_mesh(native.mesh)
    assert len(mesh.faces) == len(original.faces) == 24
    np.testing.assert_allclose(mesh.bounds, original.bounds, atol=1e-9)
