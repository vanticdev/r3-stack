"""Display reuse invalidates independently of release checks and restored mtimes."""
import os
from pathlib import Path
from types import SimpleNamespace

from r3_orchestrator.api import freshness


def test_display_cache_invalidates_changed_artifacts_and_fingerprint(tmp_path, monkeypatch):
    source = tmp_path / 'build/cad/pulled-meshes/mesh.stl'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'first')
    revision = {'spec': 'one'}
    monkeypatch.setattr(freshness.PROJECTS, 'load', lambda root: SimpleNamespace(fingerprint=lambda: dict(revision)))
    monkeypatch.setattr(freshness, 'input_files', lambda root: [])
    calls = []
    def view(ref):
        calls.append(True)
        return source.read_bytes()
    cache = freshness.GeometryViews()
    ref = SimpleNamespace(root=tmp_path)
    assert cache.read(view, ref) == b'first'
    assert cache.read(view, ref) == b'first'
    assert len(calls) == 1
    stamp = source.stat()
    source.write_bytes(b'other')
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert cache.read(view, ref) == b'other'
    assert len(calls) == 2
    revision['spec'] = 'two'
    cache.read(view, ref)
    assert len(calls) == 3
    source.unlink()
    source.symlink_to(tmp_path / 'replacement')
    (tmp_path / 'replacement').write_bytes(b'third')
    assert cache.read(view, ref) == b'third'
