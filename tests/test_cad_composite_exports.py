"""Native body topology survives pinned composite pulls without mesh repair."""
from pathlib import Path
import copy
import hashlib
import gzip
import json
from unittest.mock import Mock

import numpy as np
import pytest

from r3_model.mesh_io import load_exported_mesh, load_exported_stl
from r3_model.pulled import PullError, load_pulled
from r3_onshape.step_geometry import part_from_step
from test_cad_pull import native_snapshot, project, fake_transport

FIXTURE = Path(__file__).parent / 'fixtures/cad-composite'


def original_step() -> bytes:
    data = gzip.decompress((FIXTURE / 'compound.step.gz').read_bytes())
    assert hashlib.sha256(data).hexdigest() == 'c6f2c5a99119b8efffc309ed206241e7dd5bdcd705bb3bad5bfe09b8428f949f'
    return data


def step_file(folder: Path) -> Path:
    path = folder / 'compound.step'
    path.write_bytes(original_step())
    return path


def test_native_body_grouping_preserves_all_closed_meshes(tmp_path):
    soup = load_exported_stl(FIXTURE / 'compound.stl')
    assert not soup.is_watertight  # STL has lost ownership at the shared edge.
    mesh = load_exported_mesh(FIXTURE / 'compound.gltf')
    assert len(mesh.faces) == len(soup.faces) == 24
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0
    assert len(mesh.vertices) == 16  # never weld the two native bodies together
    assert np.array_equal(np.unique(mesh.vertices, axis=0), soup.vertices)
    exact = part_from_step('synthetic', step_file(tmp_path))
    assert len(exact.faces) == 12
    assert exact.manifold().volume() == pytest.approx(mesh.volume)


def test_one_open_native_body_cannot_hide_inside_a_closed_composite(tmp_path):
    data = json.loads((FIXTURE / 'compound.gltf').read_text())
    indices = data['meshes'][0]['primitives'][0]['indices']
    data['accessors'][indices]['count'] -= 3
    path = tmp_path / 'open.gltf'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='closed positive-volume'):
        load_exported_mesh(path)


def test_step_composite_cannot_drop_a_surface_outside_its_solids(tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from r3_onshape.step_geometry import read_step_shape, _part_from_shape

    shape = read_step_shape(step_file(tmp_path))
    extra = TopExp_Explorer(shape, TopAbs_FACE).Current()
    BRep_Builder().Add(shape, extra)
    with pytest.raises(PullError, match='outside its native solids'):
        _part_from_shape('synthetic', shape)


@pytest.mark.parametrize('section', ['buffers', 'images'])
def test_external_gltf_resources_cannot_escape_the_pinned_file(tmp_path, section):
    data = json.loads((FIXTURE / 'compound.gltf').read_text())
    data[section] = [{'uri': '../../unqualified.bin'}]
    path = tmp_path / 'external.gltf'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='embed all'):
        load_exported_mesh(path)


def composite_transport(snapshot):
    geometry = (original_step(), (FIXTURE / 'compound.stl').read_bytes())
    snapshot['rows'][0]['body_type'] = 'composite'
    transport = fake_transport(geometry, snapshot)
    calls = []
    for kind in ('stl', 'step', 'gltf'):
        def export(*args, kind=kind):
            calls.append((kind, args))
            return original_step() if kind == 'step' else (FIXTURE / ('compound.' + kind)).read_bytes()
        setattr(transport, kind, export)
    return transport, calls


def test_composite_pull_pins_native_gltf_and_refreshes_consumers(project, native_snapshot):
    from r3_model.description import check_pulled
    from r3_model.project import Project
    from r3_model.pulled_view import world_mesh, cad_view
    from r3_onshape.pull import pull
    transport, calls = composite_transport(native_snapshot)
    result = pull(project, transport=transport)
    assert result['ok'], result
    model = load_pulled(project.root)
    part = model.parts['body']
    assert part.gltf == part.mesh and part.gltf.suffix == '.gltf'
    assert part.gltf.read_bytes() == (FIXTURE / 'compound.gltf').read_bytes()
    assert part.sha256['gltf'] == hashlib.sha256(part.gltf.read_bytes()).hexdigest()
    check_pulled(project.root, model)
    reloaded = Project.load(project.root)
    assert reloaded.spec.components['body'].pulled.gltf == 'parts/body.gltf'
    assert world_mesh(model, 'body').is_watertight
    assert cad_view(reloaded)['parts']['body']['mesh_triangles'] == 24
    assert [c[0] for c in calls] == ['stl', 'step', 'gltf']
    assert all(c[1][:4] == ('doc', 'version', 'studio', 'body') for c in calls)
    part.gltf.chmod(0o644)
    part.gltf.write_bytes(b'modified')
    part.gltf.chmod(0o444)
    with pytest.raises(PullError, match='SHA-256'):
        load_pulled(project.root)


def test_composite_export_failure_never_substitutes_stl(project, native_snapshot):
    from r3_onshape.pull import pull
    transport, calls = composite_transport(native_snapshot)
    def reject(*args):
        calls.append(('gltf', args))
        raise PullError('cad_native_request_failed', 'synthetic rejection')
    transport.gltf = reject
    result = pull(project, transport=transport)
    assert not result['ok'] and result['code'] == 'cad_native_request_failed'
    assert [c[0] for c in calls] == ['stl', 'step', 'gltf']
    assert not (project.root / 'cad/pulled').exists()


def test_fabrication_uses_pinned_body_groups_and_keeps_strength_checks(project, native_snapshot):
    from r3_fab import stage
    from r3_fab.geometry import build_geometry
    from r3_onshape.pull import pull
    native_snapshot['definition']['parts'][0]['material'] = {'name': 'PLA', 'density': 1240}
    transport, _ = composite_transport(native_snapshot)
    assert pull(project, transport=transport)['ok']
    geometry = build_geometry(project, 'body')
    assert geometry.source == 'pulled_gltf' and geometry.mesh.is_volume
    assert geometry.mesh.volume == pytest.approx(2000, rel=1e-6)  # fabrication millimetres
    result = stage.run(project)
    plan = json.loads((project.root / 'build/fabrication/plan.json').read_text())
    assert plan['parts']['body']['geometry_source'] == 'pulled_gltf'
    assert not any(d.code in {'print_geometry_estimated', 'print_geometry_untrusted'} for d in result.diagnostics)
    assert any(d.code == 'print_strength_geometry_unavailable' for d in result.diagnostics)
    assert not result.ok  # no native retention/load-face evidence was supplied


def test_fabrication_rejects_geometry_outside_the_verified_pull(project, native_snapshot, monkeypatch):
    from r3_fab import stage
    from r3_fab.geometry import build_geometry
    from r3_onshape.pull import pull
    native_snapshot['definition']['parts'][0]['material'] = {'name': 'PLA', 'density': 1240}
    transport, _ = composite_transport(native_snapshot)
    assert pull(project, transport=transport)['ok']
    untrusted = build_geometry(project, 'body')
    untrusted.source = 'catalog'
    monkeypatch.setattr(stage, 'build_geometry', lambda *_: untrusted)
    result = stage.run(project)
    assert not result.ok
    assert any(d.code == 'print_geometry_untrusted' and d.severity.value == 'error' for d in result.diagnostics)


def test_repeated_native_composite_exports_once_per_source(tmp_path, native_snapshot):
    from r3_onshape.pull import describe, download, target

    transport, calls = composite_transport(native_snapshot)
    other = copy.deepcopy(native_snapshot['rows'][0])
    other.update(occurrence_path=['second'], name='second')
    native_snapshot['rows'].append(other)
    pin = target('https://cad.onshape.com/documents/doc/v/version/e/assembly')
    manifest = describe(native_snapshot, pin, {})
    download(transport, manifest, tmp_path, pin['url'])
    assert [kind for kind, _ in calls] == ['stl', 'step', 'gltf']
    assert manifest['parts']['body']['sha256'] == manifest['parts']['second']['sha256']


def test_rest_metadata_selects_body_preserving_export_without_feature_reads():
    from test_cad_rest_snapshot import Rest, URL
    from r3_onshape.rest_snapshot import assembly
    from r3_onshape.pull import describe, target

    rest = Rest()
    original = rest.api_request
    def request(method, path, query=None):
        response = original(method, path, query)
        if '/parts/' in path and not path.endswith('/massproperties'):
            response[0]['bodyType'] = 'composite'
        return response
    rest.api_request = request
    manifest = describe(assembly(rest, URL), target(URL), {})
    assert manifest['parts']['coupon']['gltf'] == 'parts/coupon.gltf'
    assert manifest['parts']['coupon']['body_type'] == 'composite'
    assert len(rest.calls) == 4 and not any('/features' in path for _, path, _ in rest.calls)


def test_gltf_transport_uses_only_pinned_native_export(tmp_path):
    from r3_onshape.native import NativeCadTransport
    (tmp_path / 'bridge.json').write_text(json.dumps({'port': 8766, 'token': 'synthetic-only'}))
    transport = NativeCadTransport(tmp_path)
    transport._request = Mock(side_effect=[json.dumps({'id': 'job', 'requestState': 'DONE',
        'resultDocumentId': 'doc', 'resultExternalDataIds': ['file']}).encode(), b'unchanged-native-file'])
    assert transport.gltf('doc', 'version', 'studio', 'part/a', 'setting') == b'unchanged-native-file'
    calls = transport._request.call_args_list
    assert calls[0].args[:2] == ('POST', '/native/api/v17/partstudios/d/doc/v/version/e/studio/translations')
    assert calls[0].args[2] == {'formatName': 'GLTF', 'storeInDocument': False, 'partIds': 'part/a',
        'resolution': 'custom', 'distanceTolerance': 5e-5, 'angularTolerance': 0.03490658503988659,
        'maximumChordLength': 10, 'configuration': 'setting'}
    assert calls[1].args == ('GET', '/native/api/v17/documents/d/doc/externaldata/file')


def test_native_original_body_id_option_cannot_hide_collapsed_topology():
    """Same synthetic pin: the native option collapses composite body ownership."""
    collapsed = FIXTURE / 'original-body-ids.gltf'
    assert hashlib.sha256(collapsed.read_bytes()).hexdigest() == '2fb9090cecea3fc785e82677b5f9b44ad8db3be79c4551941446f679d2b9de9a'
    data = json.loads(collapsed.read_text())
    assert len(data['meshes']) == 1
    assert sum(data['accessors'][p['indices']]['count'] // 3
               for m in data['meshes'] for p in m['primitives']) == 24
    with pytest.raises(ValueError, match='closed positive-volume'):
        load_exported_mesh(collapsed)
    # Default native export keeps the identical 24 facets in two closed bodies.
    mesh = load_exported_mesh(FIXTURE / 'compound.gltf')
    assert len(mesh.faces) == 24 and mesh.is_watertight
