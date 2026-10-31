"""Synthetic exported geometry: precise vertex identity and strict closure."""
import io
import numpy as np
import trimesh
from r3_model.mesh_io import load_exported_stl


def test_small_distinct_vertices_survive_in_si_and_source_bytes_unchanged(tmp_path):
    mesh = trimesh.creation.box(extents=(4e-9, .01, .01))
    mesh.apply_translation([.05, 0, 0])
    raw = mesh.export(file_type='stl')
    # Reproduce the old decoder's loss; no tolerance is widened by the fix.
    old = trimesh.load(io.BytesIO(raw), file_type='stl', process=True)
    assert not old.is_watertight and len(old.vertices) == 4
    path = tmp_path / 'synthetic-shim.stl'; path.write_bytes(raw)
    exact = load_exported_stl(path)
    assert exact.is_watertight and exact.volume > 0 and len(exact.vertices) == 8
    original = trimesh.load(io.BytesIO(raw), file_type='stl', process=False)
    np.testing.assert_array_equal(exact.triangles, original.triangles)
    assert path.read_bytes() == raw


def test_open_and_reversed_exports_are_not_repaired(tmp_path):
    mesh = trimesh.creation.box()
    for name, faces in [('open', mesh.faces[:-1]), ('reversed', mesh.faces[:, ::-1])]:
        path = tmp_path / f'{name}.stl'
        trimesh.Trimesh(mesh.vertices, faces, process=False).export(path)
        decoded = load_exported_stl(path)
        assert not (decoded.is_watertight and decoded.volume > 0)
