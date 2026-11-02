"""Project-selected native export accuracy, on unrelated synthetic coupons only."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest
from pydantic import ValidationError

from r3_model.spec import RobotSpec
from r3_onshape.native import NativeCadTransport
from test_cad_native_transport import transport
from test_cad_composite_exports import composite_transport
from test_cad_pull import native_snapshot, project

SETTINGS = {'chord_tolerance': '0.01 mm', 'angle_tolerance': '0.5 deg',
            'maximum_chord_length': '1 mm', 'minimum_facet_width': '0.001 mm'}
URL = 'https://cad.onshape.com/documents/doc/v/version/e/studio'


def configured():
    return RobotSpec.model_validate({'robot': {'name': 'precision-fixture'}, 'components': {},
                                    'cad': {'mesh_export': SETTINGS}}).cad.mesh_export


def test_project_accepts_tighter_native_mesh_settings():
    settings = configured()
    assert settings.chord_tolerance == pytest.approx(1e-5)
    assert settings.angle_tolerance == pytest.approx(.008726646259971648)
    assert settings.maximum_chord_length == .001
    assert settings.minimum_facet_width == pytest.approx(1e-6)


@pytest.mark.parametrize('field,value', [
    ('chord_tolerance', .0001), ('angle_tolerance', .1), ('maximum_chord_length', 11),
    ('minimum_facet_width', .0001), ('chord_tolerance', 0), ('angle_tolerance', float('nan')),
    ('maximum_chord_length', float('inf')), ('chord_tolerance', True),
])
def test_project_refuses_looser_or_invalid_native_settings(field, value):
    # First establish that the new settings themselves are supported.
    settings = configured().model_dump()
    settings[field] = value
    with pytest.raises(ValidationError):
        RobotSpec.model_validate({'robot': {'name': 'precision-fixture'}, 'components': {}, 'cad': {'mesh_export': settings}})


def test_transport_uses_project_accuracy_without_additional_requests(tmp_path, monkeypatch):
    from r3_model.spec import CadMeshExport
    client = transport(tmp_path)
    client._mesh_export = configured()
    client._request = Mock(return_value=b'unchanged mesh')
    client.stl('doc', 'version', 'studio', 'part')
    query = client._request.call_args.kwargs['query']
    assert query['chordTolerance'] == pytest.approx(1e-5)
    assert query['angleTolerance'] == pytest.approx(.008726646259971648)
    assert query['minFacetWidth'] == pytest.approx(1e-6)
    assert client._request.call_count == 1
    seen = []
    monkeypatch.setattr('r3_onshape.translation.translated_file', lambda _, path, body: seen.append((path, body)) or b'native')
    assert client.gltf('doc', 'version', 'studio', 'part') == b'native'
    assert len(seen) == 1 and seen[0][1]['distanceTolerance'] == pytest.approx(1e-5)
    assert seen[0][1]['maximumChordLength'] == .001
    assert not any('/features' in str(v) for v in seen)
    # Defaults retain the previous request values and omit the optional STL knob.
    client._mesh_export = CadMeshExport()
    client.stl('doc', 'version', 'studio', 'part')
    assert client._request.call_args.kwargs['query']['chordTolerance'] == 5e-5
    assert 'minFacetWidth' not in client._request.call_args.kwargs['query']


def test_inspection_settings_change_identity_without_changing_native_bytes(project):
    from r3_model.spec import CadMeshExport
    from r3_onshape.exports import export_part
    native = Mock()
    raw = b'unchanged synthetic native mesh'
    native.stl.return_value = raw
    selected = SimpleNamespace(root=project.root, spec=SimpleNamespace(cad=SimpleNamespace(mesh_export=configured())))
    first = export_part(selected, URL, 'coupon', 'stl', transport=native)
    selected.spec.cad.mesh_export = CadMeshExport()
    second = export_part(selected, URL, 'coupon', 'stl', transport=native)
    assert first['file'] != second['file']
    assert Path(first['file']).read_bytes() == Path(second['file']).read_bytes() == raw
    assert first['onshape']['mesh_export']['chord_tolerance'] == pytest.approx(1e-5)
    assert first['sha256'] == second['sha256']
    assert native.stl.call_count == 2 and not (project.root / 'cad/pulled').exists()


def test_pull_and_inspection_share_settings_and_preserve_strict_geometry(project, native_snapshot, monkeypatch):
    from r3_onshape.pull import pull
    project.spec.cad.mesh_export = configured()
    native, calls = composite_transport(native_snapshot)
    selected = []
    def make_native(**kwargs):
        selected.append(kwargs['mesh_export'])
        return native
    monkeypatch.setattr('r3_onshape.pull.NativeCadTransport', make_native)
    result = pull(project)
    assert result['ok'], result
    assert selected == [configured()]
    manifest = json.loads((project.root / 'cad/pulled/pull.json').read_text())
    assert manifest['mesh_export'] == configured().model_dump()
    assert [c[0] for c in calls] == ['stl', 'step', 'gltf']
    # An open native body is still rejected with the same selected export settings.
    index = json.loads((Path(__file__).parent / 'fixtures/cad-composite/compound.gltf').read_text())
    accessor = index['meshes'][0]['primitives'][0]['indices']
    index['accessors'][accessor]['count'] -= 3
    native.gltf = lambda *args: json.dumps(index).encode()
    old_manifest = (project.root / 'cad/pulled/pull.json').read_bytes()
    failed = pull(project)
    assert not failed['ok'] and failed['code'] == 'cad_pull_geometry_invalid'
    assert (project.root / 'cad/pulled/pull.json').read_bytes() == old_manifest


def test_export_cache_reuses_same_accuracy_and_separates_tighter_requests(tmp_path):
    from r3_model.spec import CadMeshExport
    client = transport(tmp_path)
    client.export_jobs = tmp_path / 'jobs'
    done = json.dumps({'id': 'job', 'requestState': 'DONE', 'resultDocumentId': 'doc',
                       'resultExternalDataIds': ['file']}).encode()
    client._request = Mock(side_effect=[done, b'first native file', done, b'finer native file', done, b'native STEP'])
    first = client.gltf('doc', 'version', 'studio', 'coupon')
    client._mesh_export = CadMeshExport(maximum_chord_length=10)
    assert client.gltf('doc', 'version', 'studio', 'coupon') == first
    assert client._request.call_count == 2
    client._mesh_export = configured()
    assert client.gltf('doc', 'version', 'studio', 'coupon') == b'finer native file'
    assert client.gltf('doc', 'version', 'studio', 'coupon') == b'finer native file'
    assert client._request.call_count == 4
    assert client.step('doc', 'version', 'studio', 'coupon') == b'native STEP'
    client._mesh_export = CadMeshExport()
    assert client.step('doc', 'version', 'studio', 'coupon') == b'native STEP'
    assert client._request.call_count == 6
    calls = client._request.call_args_list
    assert sum(c.args[0] == 'POST' for c in calls) == 3
    assert all('/native/api/v17/' in c.args[1] and '/features' not in c.args[1] for c in calls)


def test_mesh_settings_cannot_be_mutated_to_coarser_values():
    settings = configured()
    with pytest.raises(ValidationError):
        settings.chord_tolerance = .001


def test_agent_tools_can_select_accuracy_and_reject_relaxed_settings(project, monkeypatch):
    from r3_orchestrator import mcp_server as m
    current = m.r3_get_spec(project=str(project.root))
    changed = m.r3_patch_spec({'cad': {'mesh_export': SETTINGS}}, current['sha256'], project=str(project.root))
    assert changed['ok'], changed
    native = Mock()
    native.stl.return_value = b'unchanged synthetic native file'
    selected = []
    def make_native(**kwargs):
        selected.append(kwargs['mesh_export'])
        return native
    monkeypatch.setattr('r3_onshape.exports.NativeCadTransport', make_native)
    exported = m.r3_cad_export(URL, 'coupon', format='stl', project=str(project.root))
    assert exported['ok'] and selected == [configured()]
    assert exported['onshape']['mesh_export']['chord_tolerance'] == pytest.approx(1e-5)
    before = m.r3_get_spec(project=str(project.root))
    rejected = m.r3_patch_spec({'cad': {'mesh_export': {'chord_tolerance': .001}}}, before['sha256'], project=str(project.root))
    assert not rejected['ok']
    assert m.r3_get_spec(project=str(project.root))['sha256'] == before['sha256']
