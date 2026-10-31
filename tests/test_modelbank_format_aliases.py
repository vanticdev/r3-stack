"""Equivalent archive suffixes keep provenance and strict extraction checks."""
import hashlib
import io
import zipfile
import argparse

import pytest

from r3_catalog.modelbank import Source
from r3_catalog.modelbank.fetch import acquire, unpack


@pytest.mark.parametrize('alias,canonical', [('step.zip', 'stp.zip'), ('step.rar', 'stp.rar')])
def test_archive_alias_normalizes_in_saved_provenance(alias, canonical):
    source = Source(kind='vendor_step', url='https://fixture.test/source', format=alias)
    assert source.format == canonical
    assert Source.model_validate_json(source.model_dump_json()).format == canonical


def test_step_zip_alias_acquires_original_bytes_and_checks_members(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_CACHE', str(tmp_path / 'cache'))
    stream = io.BytesIO()
    contents = b'ISO-10303-21; synthetic fixture'
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('../../fixture.step', contents)
        archive.writestr('other.stp', contents)
    original = stream.getvalue()

    def download(_url, target):
        target.write_bytes(original)
        return target

    source = Source(kind='vendor_step', url='https://fixture.test/source.zip', format='step.zip')
    path, pinned = acquire(source, pin=True, downloader=download)
    assert path.name == 'source.zip'
    assert path.read_bytes() == original
    assert pinned.sha256 == hashlib.sha256(original).hexdigest()
    with pytest.raises(ValueError, match='multiple STEP'):
        unpack(path, pinned)
    result = unpack(path, pinned.model_copy(update={'member': '../../fixture.step'}))
    assert result == path.parent / 'model.step'
    assert result.read_bytes() == contents
    assert not (tmp_path / 'fixture.step').exists()
    with pytest.raises(ValueError, match='sha256 mismatch'):
        acquire(pinned.model_copy(update={'sha256': 'a' * 64}), downloader=download)


def test_step_zip_alias_cannot_pin_an_error_page(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_CACHE', str(tmp_path / 'cache'))

    def download(_url, target):
        target.write_bytes(b'<html>Login required</html>')
        return target

    source = Source(kind='vendor_step', url='https://fixture.test/source.zip', format='step.zip')
    with pytest.raises(ValueError, match='not a STEP ZIP'):
        acquire(source, pin=True, downloader=download)


@pytest.mark.parametrize('format', ['step.zip', 'step.rar'])
def test_cli_accepts_the_same_archive_aliases(format):
    from r3_orchestrator.models_cli import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(), argparse.ArgumentParser(add_help=False))
    args = parser.parse_args(['models', 'add', '--name', 'Fixture', '--category', 'other',
                              '--url', 'https://fixture.test/source', '--format', format])
    assert Source(kind='vendor_step', url=args.url, format=args.format).format == format.replace('step.', 'stp.')
