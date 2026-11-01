"""Project tools recover source choices and preserve catalog file ownership."""
from pathlib import Path

import pytest
import yaml

from r3_catalog.modelbank import ModelBank, ModelEntry
from r3_catalog.schema import ModelRef
from r3_orchestrator import catalog_ops, mcp_server


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path, monkeypatch):
    import r3_catalog
    empty = tmp_path / 'empty-platform-catalog'
    empty.mkdir()
    monkeypatch.setattr(r3_catalog, 'DATA_DIR', empty)
    monkeypatch.setenv('R3_MODEL_BANK', str(tmp_path / 'empty-platform-bank'))
    r3_catalog.default_catalog.cache_clear()
    yield
    r3_catalog.default_catalog.cache_clear()


def body(identifier='fixture-body'):
    return {'id': identifier, 'name': 'Synthetic source-selection fixture', 'kind': 'structure',
            'mechanical': {'size': [.01, .01, .01], 'mass': .01}}


def project(tmp_path):
    root = tmp_path / 'fixture'
    (root / 'catalog/components').mkdir(parents=True)
    (root / 'robot.yaml').write_text('robot: {name: fixture, mobility: stationary}\ncomponents: {body: {type: fixture-body}}\n')
    return root


def test_choose_synthetic_source_without_reading_platform_source(tmp_path):
    root = project(tmp_path)
    (root / 'catalog/components/family.yaml').write_text(yaml.safe_dump({'components': [body()]}))
    bank = ModelBank(project=root)
    for identifier in ('source-a', 'source-b'):
        bank.put(ModelEntry(id=identifier, name='Synthetic model record', category='other', catalog_component='fixture-body'))
    blocked = mcp_server.r3_get_catalog_component('fixture-body', project=str(root))
    assert blocked['code'] == 'catalog_model_selection_required'
    assert not blocked['ok'] and blocked['data']['candidate_ids'] == ['source-a', 'source-b']
    selected = blocked['data']['component']
    selected['model'] = ModelRef(source='vendor', params={'bank_id': 'source-b'}).model_dump(mode='json')
    saved = mcp_server.r3_put_catalog_component(selected, project=str(root))
    assert saved['ok'], saved
    assert saved['path'] == 'catalog/components/family.yaml'
    assert not (root / 'catalog/components/fixture-body.yaml').exists()
    read = mcp_server.r3_get_catalog_component('fixture-body', project=str(root))
    assert read['ok'], read
    assert read['component']['model']['params']['bank_id'] == 'source-b'
    assert not (root / 'cad/pulled').exists()


def test_named_family_update_keeps_other_entries_and_metadata(tmp_path):
    root = project(tmp_path)
    path = root / 'catalog/components/family.yaml'
    sibling = body('other-coupon')
    path.write_text(yaml.safe_dump({'notes': 'Synthetic family provenance', 'components': [body(), sibling]}))
    changed = {**body(), 'name': 'Updated synthetic label'}
    result = catalog_ops.put_entry(root, 'component', changed)
    assert result['ok'] and result['path'] == 'catalog/components/family.yaml'
    document = yaml.safe_load(path.read_text())
    assert document['notes'] == 'Synthetic family provenance'
    assert len(document['components']) == 2
    assert next(item for item in document['components'] if item['id'] == 'other-coupon') == sibling
    assert catalog_ops.get_entry(root, 'component', 'fixture-body')['entry']['name'] == changed['name']


def test_ambiguous_project_owners_are_not_rewritten(tmp_path):
    root = project(tmp_path)
    for name in ('first', 'second'):
        (root / f'catalog/components/{name}.yaml').write_text(yaml.safe_dump({'components': [body()]}))
    before = {path: path.read_bytes() for path in (root / 'catalog/components').glob('*.yaml')}
    result = catalog_ops.put_entry(root, 'component', body())
    assert not result['ok'] and result['code'] == 'catalog_entry_ambiguous'
    assert result['paths'] == ['catalog/components/first.yaml', 'catalog/components/second.yaml']
    assert {path: path.read_bytes() for path in (root / 'catalog/components').glob('*.yaml')} == before


def test_existing_owner_symlink_cannot_escape_project(tmp_path, monkeypatch):
    root = project(tmp_path)
    outside = tmp_path / 'outside.yaml'
    outside.write_text(yaml.safe_dump({'components': [body()]}))
    before = outside.read_bytes()
    (root / 'catalog/components/family.yaml').symlink_to(outside)
    read = Path.read_text
    def confined_read(path, *args, **kwargs):
        assert path.resolve() != outside.resolve(), 'Catalog owner lookup read outside the project'
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', confined_read)
    result = catalog_ops.put_entry(root, 'component', body())
    assert not result['ok'] and result['code'] == 'catalog_entry_invalid'
    assert outside.read_bytes() == before
    assert not (root / 'catalog/components/fixture-body.yaml').exists()
