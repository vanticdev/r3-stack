import io,json
from email.message import Message
from unittest.mock import Mock
from urllib.error import HTTPError
import pytest
from r3_model.pulled import PullError
from r3_onshape.native import NativeCadTransport

@pytest.mark.parametrize("path,body,route_hint", [
    ("/api/v9/documents/missing", b'{"message":"Not found.","status":404}', False),
    ("/native/api/v17/translations/missing", b'{"message":"Not found.","status":404}', False),
    ("/native/api/v17/translations/missing", b"Not Found", True),
    ("/artifacts/" + "a" * 64 + ".json", b'{"error":"Artifact missing"}', False),
])
def test_missing_object_is_not_a_companion_restart(tmp_path,path,body,route_hint):
    (tmp_path/"bridge.json").write_text(json.dumps({"host":"127.0.0.1","port":8766,"token":"synthetic-token"}))
    client=NativeCadTransport(tmp_path);client._opener=Mock()
    client._opener.open.side_effect=HTTPError("http://127.0.0.1:8766"+path,404,"missing",Message(),io.BytesIO(body))
    with pytest.raises(PullError) as captured:client._request("GET",path)
    error=captured.value
    assert error.code == "cad_native_request_failed"
    assert error.details == {"http_status":404,"method":"GET","resource":path}
    assert body.decode() in str(error)
    assert "restart" not in str(error).lower()
    assert ("route may be unavailable" in str(error)) == route_hint
    client._opener.open.assert_called_once()

@pytest.mark.parametrize('method,path,status,body,authentication_required', [
    ('GET', '/api/v9/documents/d/example/w/main/currentmicroversion', 401,
     {'message': 'Unauthenticated API request', 'status': 401}, True),
    ('POST', '/api/v9/assemblies/d/example/w/main/e/assembly/features', 401,
     {'message': 'Unauthenticated API request', 'status': 401}, True),
    ('GET', '/native/api/v17/translations/translationformats', 401,
     {'message': 'Unauthenticated API request', 'status': 401}, True),
    ('GET', '/api/v9/documents/example', 401, {'error': 'Unauthorized'}, False),
    ('GET', '/api/v9/documents/example', 403,
     {'message': 'Resource does not exist, or you do not have permission to access it.', 'status': 403}, False),
    ('POST', '/local/api_catalog', 401,
     {'message': 'Unauthenticated API request', 'status': 401}, False),
])
def test_upstream_authentication_is_distinct_from_bridge_and_permission_errors(
        tmp_path, method, path, status, body, authentication_required):
    (tmp_path / 'bridge.json').write_text(json.dumps(
        {'host': '127.0.0.1', 'port': 8766, 'token': 'synthetic-token'}))
    client = NativeCadTransport(tmp_path)
    client._opener = Mock()
    raw = json.dumps(body).encode()
    client._opener.open.side_effect = HTTPError(
        'http://127.0.0.1:8766' + path, status, 'rejected', Message(), io.BytesIO(raw))
    with pytest.raises(PullError) as captured:
        client._request(method, path, body={} if method == 'POST' else None)
    error = captured.value
    assert error.code == ('cad_native_authentication_required' if authentication_required
                          else 'cad_native_request_failed')
    assert error.details == {'http_status': status, 'method': method, 'resource': path}
    assert raw.decode() in str(error)
    assert ('sign in' in str(error).lower()) == authentication_required
    assert 'restart' not in str(error).lower()
    client._opener.open.assert_called_once()  # Never replay an uncertain write.
