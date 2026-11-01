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
