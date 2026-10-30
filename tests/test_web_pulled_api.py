"""Development API uses the same immutable geometry authority as r3 serve."""
import importlib.util
import json
from pathlib import Path

from test_cad_pulled_consumers import project, pull


def projection(root, section):
    path = Path(__file__).resolve().parents[1] / 'apps/web/scripts/project_view.py'
    spec = importlib.util.spec_from_file_location('native_web_projection', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.view(root, 'coupon', section)


def test_development_view_refuses_stale_generated_cad(project):
    (project.build_dir('cad') / 'cad_model.json').write_text(json.dumps({'parts': {'coupon': {'mesh': 'old.stl'}}}))
    response = projection(project.root, 'cad')
    assert response['authority']['code'] == 'cad_pull_missing'
    assert response['meshes'] == {} and response['cad_model'] is None


def test_development_view_reads_native_mesh_and_pose(project):
    pull(project)
    cad = projection(project.root, 'cad')
    assert cad['authority']['ready']
    assert set(cad['meshes']) == {'coupon'}
    model = projection(project.root, 'model')
    assert model['graph']['components']['coupon']['xyz'] == [.1, .2, .3]
    assert model['graph']['components']['coupon']['shape'] == 'mesh'
