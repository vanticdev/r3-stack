"""A project fan-out shares one load and still discovers changed include graphs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import threading
import time

from r3_orchestrator.api import freshness
from r3_model import spec_files


def test_unchanged_cache_does_not_reparse_yaml_and_new_includes_invalidate(tmp_path, monkeypatch):
    (tmp_path / 'robot.yaml').write_text('robot: {$include: identity.yaml}\ncomponents: {}\n')
    (tmp_path / 'identity.yaml').write_text('name: synthetic\n')
    calls = []
    original = spec_files.load_spec_files
    def parsed(root):
        calls.append(Path(root))
        return original(root)
    monkeypatch.setattr(spec_files, 'load_spec_files', parsed)
    monkeypatch.setattr(freshness.Project, 'load', lambda root: SimpleNamespace(root=root))
    cache = freshness.ProjectCache()
    first = cache.load(tmp_path)
    cold = len(calls)
    assert cold > 0
    assert cache.load(tmp_path) is first
    assert len(calls) == cold, 'A hot cache must not parse the complete YAML graph again'
    (tmp_path / 'robot.yaml').write_text('robot: {$include: other.yaml}\ncomponents: {}\n')
    (tmp_path / 'other.yaml').write_text('name: another-synthetic\n')
    second = cache.load(tmp_path)
    assert second is not first
    (tmp_path / 'other.yaml').write_text('name: changed-synthetic\n')
    assert cache.load(tmp_path) is not second


def test_concurrent_cold_requests_share_exactly_one_project_load(tmp_path, monkeypatch):
    (tmp_path / 'robot.yaml').write_text('robot: {name: synthetic}\ncomponents: {}\n')
    monkeypatch.setattr(freshness, 'code_version', lambda: 'synthetic-code')
    calls, gate = [], threading.Lock()
    def loaded(root):
        with gate: calls.append(root)
        time.sleep(.04)
        return SimpleNamespace(root=root)
    monkeypatch.setattr(freshness.Project, 'load', loaded)
    cache = freshness.ProjectCache()
    barrier = threading.Barrier(6)
    def request(_):
        barrier.wait(timeout=5)
        return cache.load(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(request, range(6)))
    assert len(calls) == 1
    assert all(result is results[0] for result in results)
