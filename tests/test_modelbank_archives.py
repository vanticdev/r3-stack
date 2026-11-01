"""RAR acquisition uses the original vendor bytes and never archive paths."""
from types import SimpleNamespace

import pytest

from r3_catalog.modelbank import Source
from r3_catalog.modelbank.fetch import acquire, unpack


def test_zip_ignores_macos_metadata_without_hiding_real_model_ambiguity(tmp_path):
    import zipfile

    path = tmp_path / 'fixture.zip'
    selected = Source(kind='vendor_step', url='https://fixture.test/coupon.zip', format='stp.zip')
    contents = b'ISO-10303-21;synthetic extraction fixture'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('coupon.step', contents)
        archive.writestr('__MACOSX/._coupon.step', b'\x00\x05\x16\x07resource fork')
        archive.writestr('._coupon.step', b'\x00\x05\x16\x07resource fork')
    assert unpack(path, selected).read_bytes() == contents
    with pytest.raises(ValueError, match='zero or multiple STEP'):
        unpack(path, selected.model_copy(update={'member': '__MACOSX/._coupon.step'}))
    with zipfile.ZipFile(path, 'a') as archive:
        archive.writestr('other.stp', b'ISO-10303-21;second real fixture')
    with pytest.raises(ValueError, match='multiple STEP'):
        unpack(path, selected)
    assert unpack(path, selected.model_copy(update={'member': 'coupon.step'})).read_bytes() == contents


def source(**kwargs):
    return Source(kind='vendor_step', url='https://vendor.test/cad.rar', format='stp.rar', **kwargs)


def test_reject_html_rar(tmp_path, monkeypatch):
    pytest.importorskip('rarfile')
    monkeypatch.setenv('R3_MODEL_CACHE', str(tmp_path / 'cache'))
    def download(url, target):
        target.write_bytes(b'<html>Please log in</html>')
        return target
    with pytest.raises(ValueError, match='not a STEP RAR'):
        acquire(source(), pin=True, downloader=download)


def test_rar_member_selection_and_bounds(tmp_path, monkeypatch):
    rarfile = pytest.importorskip('rarfile')
    from io import BytesIO
    import importlib
    module = importlib.import_module('r3_catalog.modelbank.fetch')
    members = [SimpleNamespace(filename='../../motor.step', file_size=16, isdir=lambda: False),
               SimpleNamespace(filename='__MACOSX/._fixture.step', file_size=16, isdir=lambda: False)]
    class Archive:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def infolist(self): return members
        def open(self, member): return BytesIO(b'ISO-10303-21;')
    monkeypatch.setattr(rarfile, 'RarFile', lambda path: Archive())
    path = tmp_path / 'vendor.rar'
    target = unpack(path, source())
    assert target == tmp_path / 'model.step'
    assert target.read_bytes() == b'ISO-10303-21;'
    assert not (tmp_path.parent / 'motor.step').exists()
    members.append(SimpleNamespace(filename='other.stp', file_size=16, isdir=lambda: False))
    with pytest.raises(ValueError, match='multiple STEP'):
        unpack(path, source())
    assert unpack(path, source(member='../../motor.step')) == target
    members[0].file_size = module.MAX_BYTES + 1
    with pytest.raises(ValueError, match='uncompressed STEP'):
        unpack(path, source(member='../../motor.step'))
    members[0].file_size = 1  # even dishonest archive metadata cannot bypass the read limit
    monkeypatch.setattr(module, 'MAX_BYTES', 5)
    with pytest.raises(ValueError, match='uncompressed STEP'):
        unpack(path, source(member='../../motor.step'))


def test_component_resolver_accepts_pinned_rar_bank_source(tmp_path, monkeypatch):
    import importlib
    from r3_catalog.modelbank import ModelBank, ModelEntry
    from r3_catalog.schema import ModelRef
    from r3_connectors.models import resolve_step
    module = importlib.import_module('r3_catalog.modelbank.fetch')
    entry = ModelEntry(id='vendor-actuator', name='Actuator', category='other',
                       sources=[source(sha256='a' * 64, solid_indices=[1])])
    monkeypatch.setattr(ModelBank, 'get', lambda self, name: entry)
    archive = tmp_path / 'vendor.rar'
    selected = tmp_path / 'selected.step'
    monkeypatch.setattr(module, 'acquire', lambda item: (archive, item))
    calls = []
    def unpack_selected(path, item):
        calls.append((path, item.solid_indices))
        return selected
    monkeypatch.setattr(module, 'unpack', unpack_selected)
    ref = ModelRef(source='vendor', params={'bank_id': entry.id})
    assert resolve_step(ref) == selected
    assert calls == [(archive, [1])]
