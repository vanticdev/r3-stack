"""Native-only export boundary; synthetic bytes, no account or robot data."""
from pathlib import Path
import json
from unittest.mock import Mock

import pytest

from r3_model.pulled import PullError
from r3_onshape.native import NativeCadTransport


def transport(tmp_path):
    (tmp_path / 'bridge.json').write_text(json.dumps({'host': '127.0.0.1', 'port': 8766, 'token': 'test-only-token'}))
    return NativeCadTransport(tmp_path)


@pytest.mark.parametrize('path', ['/api/v17/partstudios/d/d/v/v/e/e/stl', '/partstudios/d/d/v/v/e/e/stl', 'https://example.test'])
def test_public_routes_refused_before_network(tmp_path, path):
    client = transport(tmp_path)
    client._opener = Mock()
    with pytest.raises(PullError, match='public /api is disabled'):
        client._request('GET', path)
    client._opener.open.assert_not_called()


def test_part_exports_pin_version_and_exact_part(tmp_path):
    client = transport(tmp_path)
    client._request = Mock(return_value=b'stl-bytes')
    assert client.stl('document', 'version', 'studio', 'part/a') == b'stl-bytes'
    args = client._request.call_args
    assert '/v/version/' in args.args[1]
    assert '/partid/part%2Fa/stl' in args.args[1]
    assert args.kwargs['query']['units'] == 'METER'
    assert args.kwargs['query']['chordTolerance'] == 5e-5


def test_step_uses_translation_result_document(tmp_path, monkeypatch):
    client = transport(tmp_path)
    client._request = Mock(side_effect=[json.dumps({'id': 'job', 'requestState': 'ACTIVE'}).encode(),
        json.dumps({'id': 'job', 'requestState': 'DONE', 'resultDocumentId': 'result-document', 'resultExternalDataIds': ['file']}).encode(), b'step-bytes'])
    monkeypatch.setattr('r3_onshape.native.time.sleep', lambda _: None)
    assert client.step('source-document', 'version', 'studio', 'part') == b'step-bytes'
    calls = client._request.call_args_list
    assert calls[0].args[0] == 'POST'
    assert calls[0].args[2]['partIds'] == ['part']
    assert calls[1].args[1] == '/native/api/v17/translations/job'
    assert calls[2].args[1] == '/native/api/v17/documents/d/result-document/externaldata/file'


def test_no_retry_or_whole_element_fallback_on_failed_submission(tmp_path):
    client = transport(tmp_path)
    client._request = Mock(side_effect=PullError('cad_native_request_failed', 'rejected part'))
    with pytest.raises(PullError):
        client.step('d', 'v', 'e', 'missing')
    assert client._request.call_count == 1


@pytest.mark.parametrize('kind', ['rest', 'evaluate', 'api_write'])
def test_editor_commands_cannot_proxy_rest(tmp_path, kind):
    client = transport(tmp_path)
    client._request = Mock()
    with pytest.raises(PullError):
        client.command({'kind': kind})
    client._request.assert_not_called()
