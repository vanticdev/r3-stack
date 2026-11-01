"""Ambiguous synthetic model sources stay errors, with recoverable choice data."""
from types import ModuleType, SimpleNamespace
import sys

from fastapi.testclient import TestClient
import pytest

from r3_catalog.modelbank import ModelBank
from r3_orchestrator import api, mcp_server, stages
from r3_orchestrator.api.server import create_app


def ambiguity(tmp_path, monkeypatch):
    bank = ModelBank(tmp_path / 'empty-bank', tmp_path / 'index.sqlite')
    entries = [SimpleNamespace(id=name, catalog_component='fixture-body', catalog_aliases=[])
               for name in ('source-b', 'source-a')]
    monkeypatch.setattr(bank, 'entries', lambda **kwargs: entries)
    component = SimpleNamespace(id='fixture-body', model=None)
    def resolve(*args, **kwargs):
        return bank.resolve(component)
    return bank, component, resolve


def test_ambiguity_has_candidates_without_selecting_one(tmp_path, monkeypatch):
    _, _, resolve = ambiguity(tmp_path, monkeypatch)
    with pytest.raises(ValueError) as captured:
        resolve()
    error = captured.value
    assert getattr(error, 'code', None) == 'catalog_model_selection_required'
    assert error.details['candidate_ids'] == ['source-a', 'source-b']
    assert error.details['component_id'] == 'fixture-body'
    assert error.details['field'] == 'model.params.bank_id'


def test_explicit_selection_still_uses_exact_validated_id(tmp_path, monkeypatch):
    bank, component, _ = ambiguity(tmp_path, monkeypatch)
    component.model = SimpleNamespace(params={'bank_id': 'source-b'})
    selected = object()
    monkeypatch.setattr(bank, 'get', lambda name: selected if name == 'source-b' else pytest.fail(name))
    assert bank.resolve(component) is selected


def test_mcp_selection_error_is_data_not_a_protocol_failure(tmp_path, monkeypatch):
    _, _, resolve = ambiguity(tmp_path, monkeypatch)
    result = mcp_server._guard(resolve)
    assert not result['ok']
    assert result['code'] == 'catalog_model_selection_required'
    assert result['data']['candidate_ids'] == ['source-a', 'source-b']
    assert result['data']['component']['id'] == 'fixture-body'


def test_stage_preserves_selection_failure_and_writes_result(tmp_path, monkeypatch):
    _, _, resolve = ambiguity(tmp_path, monkeypatch)
    module = ModuleType('r3_synthetic_selection_fixture')
    module.run = resolve
    monkeypatch.setitem(sys.modules, module.__name__, module)
    stage = stages.StageSpec('fixture', module.__name__, 'Synthetic selection fixture')
    monkeypatch.setitem(stages.ALL_STAGES, stage.name, stage)
    monkeypatch.setattr(stages, '_precheck', lambda *args: None)
    project = SimpleNamespace(root=tmp_path, fingerprint=lambda: {})
    result = stages.run_stage(project, 'fixture')
    assert not result.ok
    assert [item.code for item in result.errors] == ['catalog_model_selection_required']
    assert result.errors[0].data['candidate_ids'] == ['source-a', 'source-b']
    assert (tmp_path / 'build/fixture/result.json').is_file()


def test_http_selection_error_is_actionable_422(tmp_path, monkeypatch):
    _, _, resolve = ambiguity(tmp_path, monkeypatch)
    root = tmp_path / 'fixture'
    root.mkdir()
    (root / 'robot.yaml').write_text('robot: {name: fixture, mobility: stationary}\ncomponents: {}\n')
    monkeypatch.setattr(api, 'list_catalog', resolve)
    with TestClient(create_app(projects_roots=[tmp_path]), raise_server_exceptions=False) as client:
        result = client.get('/api/projects/fixture/catalog')
    assert result.status_code == 422
    assert result.json()['code'] == 'catalog_model_selection_required'
    assert result.json()['data']['candidate_ids'] == ['source-a', 'source-b']


def test_unexpected_value_error_is_not_relabelled_as_a_selection():
    def broken():
        raise ValueError('Synthetic programming defect')
    with pytest.raises(ValueError, match='programming defect'):
        mcp_server._guard(broken)
