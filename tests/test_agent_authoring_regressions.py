"""Generic agent authoring contracts on unrelated synthetic geometry."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
from unittest.mock import Mock

import pytest

from r3_model.project import Project
from r3_model.pulled import PullError
from r3_onshape.exports import export_part
from r3_onshape.native import NativeCadTransport
from r3_orchestrator import mcp_server as m


@pytest.fixture
def project(tmp_path):
    from r3_github.scaffold import create_project
    root = tmp_path / 'synthetic-instrument'
    create_project(root, template='blank', platform_ref='test')
    return root


def test_hardware_categories_survive_project_tools_and_native_pull(project):
    from pulled_fixture import box, write_pull
    kinds = {'coupon': 'structure', 'retainer': 'fastener', 'feedthrough': 'connector'}
    for name, kind in kinds.items():
        assert m.r3_put_catalog_component({'id': name, 'kind': kind, 'name': name,
            'mechanical': {'size': [.01, .01, .004], 'mass': .01, 'material': 'PLA',
                           'fabricated': kind == 'structure'}}, project=str(project))['ok']
    current = m.r3_get_spec(project=str(project))
    assert m.r3_patch_spec({'components': {name: {'type': name} for name in kinds},
                           'assembly': {'root': 'coupon', 'joins': []}}, current['sha256'], project=str(project))['ok']
    write_pull(project, {name: box([10, 10, 4]) for name in kinds})
    model = Project.load(project)
    assert set(model.assembly.poses) == set(kinds)
    assert model.catalog.component('retainer').mechanical.fabricated is False
    assert model.catalog.component('feedthrough').kind == 'connector'
    assert [name for name in kinds if model.catalog.component(name).kind == 'structure'] == ['coupon']
    assert m.r3_list_catalog(project=str(project), kind='fastener')['components'][0]['id'] == 'retainer'
    enum = m.r3_get_schema('component')['schema']['properties']['kind']['enum']
    assert {'fastener', 'connector'} <= set(enum)


@pytest.fixture
def step_bytes(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    writer = STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeBox(12, 17, 5).Shape(), STEPControl_AsIs)
    path = tmp_path / 'coupon.step'
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path.read_bytes()


def test_agent_export_is_pinned_unchanged_native_and_not_a_pull(project, step_bytes, monkeypatch):
    native = NativeCadTransport.__new__(NativeCadTransport)
    native._request = Mock(side_effect=[json.dumps({'id':'job', 'requestState':'DONE', 'resultDocumentId':'doc',
                                                   'resultExternalDataIds':['file']}).encode(), step_bytes])
    def make_native(**kwargs):
        native.export_jobs = kwargs['export_jobs']
        assert native.export_jobs == project / 'build/cad/native-exports'
        return native
    monkeypatch.setattr('r3_onshape.exports.NativeCadTransport', make_native)
    result = m.r3_cad_export('https://cad.onshape.com/documents/doc/v/version/e/studio', 'part/a', project=str(project))
    assert result['ok'] and Path(result['file']).read_bytes() == step_bytes
    record = json.loads((project / result['manifest']).read_text())
    assert record['sha256'] == hashlib.sha256(step_bytes).hexdigest()
    assert record['onshape']['version_id'] == 'version' and record['onshape']['part_id'] == 'part/a'
    assert record['qualification'] == 'unverified_export'
    assert not (project / 'cad/pulled').exists()
    calls = native._request.call_args_list
    assert len(calls) == 2 and all('/native/api/v17/' in call.args[1] for call in calls)
    assert not any('/features' in call.args[1] for call in calls)
    assert calls[0].args[2]['partIds'] == 'part/a'
    assert 'r3_cad_export' in m.ungated_tools()


def test_workspace_export_requires_pin_and_never_guesses_revision(project):
    native = Mock()
    with pytest.raises(PullError, match='version'):
        export_part(SimpleNamespace(root=project), 'https://cad.onshape.com/documents/doc/w/work/e/studio', 'part', transport=native)
    native.step.assert_not_called()


def test_native_stl_export_preserves_bytes_and_units(project):
    native = Mock()
    native.stl.return_value = b'unchanged mesh export'
    result = export_part(SimpleNamespace(root=project), 'https://cad.onshape.com/documents/doc/v/pin/e/studio', 'part', 'stl', transport=native)
    assert result['units'] == 'm' and Path(result['file']).read_bytes() == native.stl.return_value
    native.stl.assert_called_once_with('doc', 'pin', 'studio', 'part', '')


def test_native_gltf_inspection_is_pinned_and_never_installed(project):
    native = Mock()
    data = (Path(__file__).parent / 'fixtures/cad-composite/compound.gltf').read_bytes()
    native.gltf.return_value = data
    result = export_part(SimpleNamespace(root=project), 'https://cad.onshape.com/documents/doc/v/pin/e/studio',
                         'compound', 'gltf', transport=native)
    assert result['units'] == 'm' and result['qualification'] == 'unverified_export'
    assert Path(result['file']).read_bytes() == data
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    native.gltf.assert_called_once_with('doc', 'pin', 'studio', 'compound', '')
    assert not (project / 'cad/pulled').exists()


def test_export_refuses_build_escape_before_request(project, tmp_path):
    (project / 'build').mkdir(exist_ok=True)
    (project / 'build/cad').symlink_to(tmp_path / 'outside')
    native = Mock()
    with pytest.raises(PullError, match='inside'):
        export_part(SimpleNamespace(root=project), 'https://cad.onshape.com/documents/doc/v/pin/e/studio', 'part', transport=native)
    native.step.assert_not_called()


def test_catalog_validation_errors_remain_actionable_tool_results(project):
    import yaml
    folder = project / 'catalog/components'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'invalid.yaml').write_text(yaml.safe_dump({'components': [{
        'id': 'broken-fixture', 'name': 'Synthetic invalid data', 'kind': 'invalid-fixture-kind',
        'mechanical': {'size': [.01, .01, .01], 'mass': .01}}]}))
    result = m.r3_get_catalog_component('broken-fixture', project=str(project))
    assert not result['ok'] and result['code'] == 'catalog_invalid'
    assert 'kind' in result['message'] and 'r3_get_schema' in result['hint']
