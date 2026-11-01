"""Invalid native exports identify topology failures without changing geometry."""
import hashlib
import json

import pytest
import trimesh

from r3_model.mesh_io import load_exported_mesh
from test_cad_composite_exports import FIXTURE, composite_transport
from test_cad_pull import project, native_snapshot


def open_export():
    value = json.loads((FIXTURE / 'compound.gltf').read_text())
    index = value['meshes'][0]['primitives'][0]['indices']
    value['accessors'][index]['count'] -= 3
    return json.dumps(value).encode()


def test_open_export_identifies_boundary_without_repairing_bytes(tmp_path):
    path = tmp_path / 'open.gltf'
    path.write_bytes(open_export())
    before = path.read_bytes()
    with pytest.raises(ValueError, match='closed positive-volume') as captured:
        load_exported_mesh(path)
    details = captured.value.details
    assert details['watertight'] is False
    assert details['finite_vertices'] is True
    assert details['boundary_edges'] == 3
    assert details['non_manifold_edges'] == 0
    assert 'boundary_edges=3' in str(captured.value)
    assert path.read_bytes() == before


def test_nonmanifold_export_identifies_overused_edges_without_repair(tmp_path):
    mesh = load_exported_mesh(FIXTURE / 'compound.gltf')
    # One native node merges ownership of two closed coupons at a shared edge.
    files = trimesh.exchange.gltf.export_gltf(trimesh.Scene(mesh), embed_buffers=True)
    raw = next(v for k, v in files.items() if k.endswith('.gltf'))
    path = tmp_path / 'contact.gltf'
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='closed positive-volume') as captured:
        load_exported_mesh(path)
    details = captured.value.details
    assert details['watertight'] is False and details['winding_consistent'] is True
    assert details['positive_volume'] is True and details['boundary_edges'] == 0
    assert details['non_manifold_edges'] > 0
    assert path.read_bytes() == raw


def test_pull_returns_and_preserves_mesh_diagnostic_with_raw_export(project, native_snapshot):
    from r3_onshape.pull import pull
    transport, _ = composite_transport(native_snapshot)
    raw = open_export()
    transport.gltf = lambda *a: raw
    result = pull(project, transport=transport)
    assert not result['ok'] and result['code'] == 'cad_pull_geometry_invalid'
    assert result['component'] == 'body'
    assert result['mesh']['boundary_edges'] == 3
    assert not (project.root / 'cad/pulled').exists()
    failure, = (project.root / 'build/cad/failed-pulls').glob('*/failure.json')
    evidence = json.loads(failure.read_text())
    assert evidence['error']['details']['mesh'] == result['mesh']
    record, = [v for v in evidence['files'] if v['path'].endswith('.gltf')]
    assert (failure.parent / record['path']).read_bytes() == raw
    assert record['sha256'] == hashlib.sha256(raw).hexdigest()
