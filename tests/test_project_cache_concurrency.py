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


def test_include_identity_checks_resolve_each_source_once(tmp_path, monkeypatch):
    count = 32
    (tmp_path / 'robot.yaml').write_text('items:\n' + ''.join(
        f'  item{i}: {{$include: item{i}.yaml}}\n' for i in range(count)))
    for i in range(count):
        (tmp_path / f'item{i}.yaml').write_text(f'value: {i}\n')
    resolved = []
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        resolved.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    files = spec_files.load_spec_files(tmp_path)
    assert len(files.texts) == count + 1
    assert len(resolved) <= 2 * (count + 1) + 1


def test_include_alias_still_rejected(tmp_path):
    import pytest
    (tmp_path / 'original.yaml').write_text('value: 1\n')
    (tmp_path / 'alias.yaml').symlink_to('original.yaml')
    (tmp_path / 'robot.yaml').write_text(
        'first: {$include: original.yaml}\nsecond: {$include: alias.yaml}\n')
    with pytest.raises(spec_files.SpecError, match='already included'):
        spec_files.load_spec_files(tmp_path)


def test_evicted_projects_do_not_retain_load_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(freshness, 'code_version', lambda: 'synthetic-code')
    monkeypatch.setattr(freshness, 'input_files', lambda *args: [])
    monkeypatch.setattr(freshness.Project, 'load', lambda root: SimpleNamespace(root=root))
    cache = freshness.ProjectCache(size=2)
    for i in range(20):
        cache.load(tmp_path / str(i))
    assert len(cache._entries) == len(cache._inputs) == 2
    assert len(cache._loads) == 0
