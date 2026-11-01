"""Offline contracts: FTS invalidation, provenance, ingestion and safe cache reuse."""
import hashlib
import json
from pathlib import Path

import pytest

from r3_catalog.modelbank import ModelBank, ModelEntry, Source, Spec
from r3_catalog.modelbank.fetch import acquire, unpack, validate
from r3_catalog.modelbank.ingest import ingest_page, ingest_onshape


def entry(identifier='motor-1', **kwargs):
    return ModelEntry(id=identifier, name='Pololu metal gearmotor', mpn='37D-4754', manufacturer='Pololu', category='motor', **kwargs)


def test_search_rebuild_filters_and_fuzzy(tmp_path):
    bank = ModelBank(tmp_path / 'data', tmp_path / 'index.sqlite')
    bank.put(entry(key_specs={'mass': Spec(value=0.2, unit='kg')}))
    assert bank.search('37D4754')[0]['id'] == 'motor-1'
    assert bank.search('37D-4755')[0]['id'] == 'motor-1'
    assert bank.search('gearmotor', category='servo') == []
    assert bank.search('', manufacturer='Pololu', specs={'mass': [0.1, 0.3]})
    assert bank.search('', specs={'mass': [0, 0.1]}) == []
    bank.put(ModelEntry(id='servo', name='Steering servo', category='servo'))
    assert bank.search('steering')[0]['id'] == 'servo'
    assert bank.search('" OR * --') == []
    with pytest.raises(ValueError):
        bank.search('', specs={'x\');DROP TABLE models;--': [0, 1]})


def test_cache_rechecks_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_CACHE', str(tmp_path / 'cache'))
    data = b'ISO-10303-21; fixture'
    def download(_url, target):
        target.write_bytes(data)
        return target
    source = Source(kind='vendor_step', url='https://vendor.example/motor.step')
    path, pinned = acquire(source, pin=True, downloader=download)
    assert pinned.sha256 == hashlib.sha256(data).hexdigest()
    path.write_bytes(b'corrupt cache')
    path, _ = acquire(pinned, downloader=download)
    assert path.read_bytes() == data
    with pytest.raises(ValueError, match='sha256 mismatch'):
        acquire(pinned.model_copy(update={'sha256': 'a' * 64}), downloader=download)
    with pytest.raises(ValueError, match='pointer'):
        acquire(source.model_copy(update={'requires_login': True}), pin=True)


def test_archive_is_unambiguous_and_does_not_extract_paths(tmp_path):
    import zipfile
    path = tmp_path / 'source.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('../../escape.step', b'ISO-10303-21;')
    source = Source(kind='vendor_step', url='https://x.test/m.zip', format='stp.zip')
    assert unpack(path, source) == tmp_path / 'model.step'
    assert not (tmp_path.parent / 'escape.step').exists()
    with zipfile.ZipFile(path, 'a') as archive:
        archive.writestr('other.step', b'ISO-10303-21;')
    with pytest.raises(ValueError, match='multiple'):
        unpack(path, source)


def test_bbox_cannot_verify_holes():
    model = entry(key_specs={'dimensions': Spec(value=[0.01, 0.02, 0.03], unit='m')})
    assert validate(model, [0.01, 0.02, 0.03]).status == 'unverified'
    assert validate(model, [0.01, 0.02, 0.03], {'shaft': True}).status == 'passed'
    assert validate(model, [0.1, 0.02, 0.03], {'shaft': True}).status == 'mismatch'
    assert validate(model, [0.01, 0.02, 0.03], {'shaft': False}).status == 'mismatch'


@pytest.mark.parametrize('provider', ['pololu', 'raspberrypi', 'arducam', 'ublox', 'sparkfun', 'adafruit'])
def test_resource_plugins_offline(provider):
    fixture = json.loads((Path(__file__).parent / 'fixtures/modelbank/resources.json').read_text())[provider]
    models = ingest_page(provider, fixture['url'], name='Board', html=fixture['html'], pin=False)
    if provider not in ('pololu', 'raspberrypi'):
        assert models == []
        return
    assert len(models) == 1
    assert models[0].sources[0].kind == 'vendor_step'
    assert not models[0].sources[0].sha256  # discovery is not acquisition evidence


def test_database_never_scrapes_login():
    models = ingest_page('traceparts', 'https://www.traceparts.com/en/search/motor', name='Motor')
    assert models[0].sources[0].requires_login
    assert not models[0].available


def test_onshape_search_requires_public_versions():
    class API:
        def json(self, method, path, query=None):
            if path == '/documents':
                assert query['filter'] == 4
                return {'items': [{'id': 'public', 'name': 'Pi', 'isPublic': True}, {'id': 'private', 'isPublic': False}]}
            if path.endswith('/versions'):
                return [{'id': 'v1'}]
            return [{'id': 'studio', 'name': 'PCB', 'elementType': 'PARTSTUDIO'}]
    models = ingest_onshape('Pi', API())
    assert len(models) == 1
    assert models[0].sources[0].vid == 'v1'


def test_public_discovery_limits_models_and_ranks_matching_tabs():
    calls = []

    class API:
        def json(self, method, path, query=None):
            calls.append(path)
            if path == '/documents':
                assert query['q'] == 'synthetic probe' and query['limit'] == 1
                return {'items': [{'id': 'library', 'name': 'Public instrument library', 'isPublic': True}]}
            if path.endswith('/versions'):
                return [{'id': 'pin'}]
            return [{'id': str(i), 'name': name, 'elementType': 'PARTSTUDIO'}
                    for i, name in enumerate(['Housing', 'Lid', 'Synthetic-Probe', 'Probe fixture'])]

    models = ingest_onshape('synthetic probe', API(), limit=1)
    assert [model.name for model in models] == ['Public instrument library / Synthetic-Probe']
    assert models[0].sources[0].vid == 'pin'
    assert calls == ['/documents', '/documents/d/library/versions', '/documents/d/library/v/pin/elements']


def test_direct_official_resource_is_ingestable_without_scraping():
    models = ingest_page('pololu', 'https://www.pololu.com/file/0J1733/d36v50fx-step-down-voltage-regulator.step', name='Regulator', pin=False)
    assert len(models) == 1 and models[0].sources[0].format == 'step'


def test_add_rejects_login_html_before_pinning(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_CACHE', str(tmp_path / 'cache'))
    def login(_url, target):
        target.write_text('<html>Please sign in</html>')
        return target
    with pytest.raises(ValueError, match='not a STEP'):
        acquire(Source(kind='vendor_step', url='https://vendor.test/m.step'), pin=True, downloader=login)
