"""Parsed description reuse still checks content and isolates callers."""
import os

import pytest

from r3_model.spec_files import SpecError, load_spec_files


def test_cached_description_checks_bytes_even_with_preserved_mtime(tmp_path):
    path = tmp_path / 'robot.yaml'
    path.write_text('robot: {name: first}\n')
    first = load_spec_files(tmp_path)
    stamp = path.stat()
    path.write_text('robot: {name: other}\n')
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = load_spec_files(tmp_path)
    assert second.data['robot']['name'] == 'other'
    assert first.sha256 != second.sha256
    second.data['robot']['name'] = 'mutated'
    assert load_spec_files(tmp_path).data['robot']['name'] == 'other'


def test_cached_description_rediscovers_includes_and_checks_symlinks(tmp_path):
    index = tmp_path / 'robot.yaml'
    index.write_text('robot: {$include: identity.yaml}\n')
    identity = tmp_path / 'identity.yaml'
    identity.write_text('name: first\n')
    load_spec_files(tmp_path)
    identity.unlink()
    identity.symlink_to(tmp_path.parent / 'outside.yaml')
    with pytest.raises(SpecError, match='escapes project'):
        load_spec_files(tmp_path)
    identity.unlink()
    index.write_text('robot: {$include: new.yaml}\n')
    (tmp_path / 'new.yaml').write_text('name: changed\n')
    assert load_spec_files(tmp_path).data['robot']['name'] == 'changed'


def test_cached_description_rejects_new_alias_between_includes(tmp_path):
    (tmp_path / 'robot.yaml').write_text('a: {$include: a.yaml}\nb: {$include: b.yaml}\n')
    for name in ('a.yaml', 'b.yaml'):
        (tmp_path / name).write_text('value: same\n')
    load_spec_files(tmp_path)
    (tmp_path / 'b.yaml').unlink()
    (tmp_path / 'b.yaml').symlink_to('a.yaml')
    with pytest.raises(SpecError, match='already included'):
        load_spec_files(tmp_path)


def test_spec_digest_checks_bytes_without_mutable_snapshot(tmp_path):
    from r3_model.spec_files import spec_sha256
    path = tmp_path / 'robot.yaml'
    path.write_text('robot: {name: first}\n')
    original = spec_sha256(tmp_path)
    path.write_text('robot: {name: other}\n')
    assert spec_sha256(tmp_path) != original
