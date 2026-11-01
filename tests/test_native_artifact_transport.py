"""Read exact authenticated cached responses without companion filesystem access."""
import hashlib
import io
import json
from unittest.mock import Mock

import pytest

from r3_model.pulled import PullError
from r3_onshape.native import NativeCadTransport


def client(tmp_path, raw):
    (tmp_path / 'bridge.json').write_text(json.dumps({'host': '127.0.0.1', 'port': 8766, 'token': 'synthetic-token'}))
    value = NativeCadTransport(tmp_path)
    value._opener = Mock()
    value._opener.open.return_value = io.BytesIO(raw)
    return value


def test_complete_cached_response_is_one_authenticated_local_read(tmp_path):
    raw = json.dumps({'fixture': [{'index': i, 'text': 'synthetic' * 900} for i in range(30)]}).encode()
    handle = hashlib.sha256(raw).hexdigest() + '.json'
    value = client(tmp_path, raw)
    assert not (tmp_path / 'artifacts').exists()
    assert value.artifact(handle) == json.loads(raw)
    request = value._opener.open.call_args.args[0]
    assert request.full_url == 'http://127.0.0.1:8766/artifacts/' + handle
    assert request.get_method() == 'GET'
    assert request.get_header('Authorization') == 'Bearer synthetic-token'
    assert value._opener.open.call_count == 1


def test_corrupt_cached_response_fails_without_fallback(tmp_path):
    value = client(tmp_path, b'{"corrupt":true}')
    with pytest.raises(PullError) as error:
        value.artifact('0' * 64 + '.json')
    assert error.value.code == 'cad_native_invalid'
    assert 'checksum' in str(error.value).lower()
    assert value._opener.open.call_count == 1


@pytest.mark.parametrize('handle', ['bridge.json', '../bridge.json', 'A' * 64 + '.json', 'a' * 64 + '.bin'])
def test_invalid_handles_never_make_a_request(tmp_path, handle):
    value = client(tmp_path, b'{}')
    with pytest.raises(PullError):
        value.artifact(handle)
    value._opener.open.assert_not_called()


@pytest.mark.parametrize('method,path', [
    ('GET', '/artifacts'), ('GET', '/artifacts/bridge.json'),
    ('POST', '/artifacts/' + 'a' * 64 + '.json'),
    ('GET', '/artifacts/' + 'a' * 64 + '.json/../bridge.json'),
])
def test_cache_routes_cannot_enumerate_write_or_traverse(tmp_path, method, path):
    value = client(tmp_path, b'{}')
    with pytest.raises(PullError) as error:
        value._request(method, path)
    assert error.value.code == 'cad_native_route_forbidden'
    value._opener.open.assert_not_called()
