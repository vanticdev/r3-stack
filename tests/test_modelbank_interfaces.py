"""CLI, HTTP and MCP use the same bank; no live network is used by these tests."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from r3_catalog.modelbank import ModelBank, ModelEntry
from r3_catalog.modelbank import service
from r3_orchestrator.cli import main


@pytest.fixture
def bank(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_BANK', str(tmp_path / 'bank'))
    monkeypatch.setenv('R3_MODEL_CACHE', str(tmp_path / 'cache'))
    bank = ModelBank()
    bank.put(ModelEntry(id='motor', name='Fixture motor', mpn='M-42', category='motor', manufacturer='Fixture'))
    return bank


def test_cli_and_mcp(bank, capsys):
    from r3_orchestrator.mcp_server import r3_models_search, r3_models_fetch
    assert main(['models', 'search', 'M42', '--json']) == 0
    assert '"id": "motor"' in capsys.readouterr().out
    assert main(['models', 'show', 'missing', '--json']) == 1
    assert r3_models_search('M42')['models'][0]['id'] == 'motor'
    assert r3_models_fetch('motor')['code'] == 'model_unavailable'


def test_http_routes_and_asset_confinement(bank, tmp_path):
    from r3_orchestrator.api.server import create_app
    with TestClient(create_app([tmp_path], web_dist=tmp_path / 'dist')) as client:
        assert client.get('/api/models?q=M42').json()['models'][0]['id'] == 'motor'
        assert client.get('/api/models/motor').json()['model']['category'] == 'motor'
        assert client.get('/api/models/missing').status_code == 422
        assert client.post('/api/models/motor/fetch').status_code == 422
        assert client.get('/api/models/motor/assets/glb').status_code == 404
        assert client.get('/api/models/motor/assets/secret').status_code == 422
        assert client.post('/api/models', json={'name': 'private file', 'category': 'motor', 'url': 'file:///etc/passwd'}).status_code == 422
        assert client.get('/api/models?specs=not-json').status_code == 422
        assert '/api/models/ingest/run' in client.get('/api/openapi.json').json()['paths']


def test_manual_add_pins_and_converts(bank, monkeypatch):
    from r3_catalog.modelbank.schema import Source
    def acquire(source, pin=False):
        assert pin
        return Path('/fixture/part.step'), source.model_copy(update={'sha256': 'a' * 64, 'retrieved_at': '2026-10-29'})
    monkeypatch.setattr(service, 'acquire', acquire)
    monkeypatch.setattr(service, 'convert', lambda path, source: {'bbox_m': [0.02, 0.03, 0.04]})
    result = service.add('Motor 2', 'motor', 'M-43', 'Fixture', 'https://vendor.test/motor.step')
    assert result['ok']
    assert bank.get('fixture-m-43').sources[0].sha256 == 'a' * 64
    assert bank.get('fixture-m-43').validation.status == 'unverified'
    with pytest.raises(ValueError, match='already exists'):
        service.add('Motor 2', 'motor', 'M-43', 'Fixture', 'https://vendor.test/motor.step')


def test_public_onshape_rejection_is_reported_once(bank, monkeypatch):
    from r3_onshape.native import NativeCadTransport
    calls=[]
    def reject(self,*args,**kwargs):
        calls.append(args)
        raise OSError('Native browser search unavailable')
    monkeypatch.setattr(NativeCadTransport,'__init__',lambda self:None)
    monkeypatch.setattr(NativeCadTransport,'api_request',reject)
    from r3_catalog.modelbank.ingest import ingest
    result=service.guard(lambda:ingest(bank,'onshape',query='Unrelated synthetic coupon'))
    assert result['code']=='model_bank_error' and 'native' in result['message'].lower()
    assert len(calls)==1


def test_cli_explicit_project_uses_local_overlay(bank, tmp_path, capsys):
    project = tmp_path / 'unrelated-instrument'
    local = ModelBank(project=project)
    local.put(ModelEntry(id='synthetic-local', name='Synthetic local fixture', category='other'))
    assert main(['models', 'search', 'synthetic-local', '--project', str(project), '--json']) == 0
    assert '"id": "synthetic-local"' in capsys.readouterr().out
    assert main(['models', 'show', 'synthetic-local', '--project', str(project), '--json']) == 0
    assert '"id": "synthetic-local"' in capsys.readouterr().out
    with pytest.raises(KeyError):
        bank.get('synthetic-local')
