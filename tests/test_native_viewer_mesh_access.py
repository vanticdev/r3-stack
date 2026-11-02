"""Loaded viewer data remains an authenticated, unqualified inspection artifact."""
import hashlib
import io
import json
from unittest.mock import Mock

import pytest

from r3_model.pulled import PullError
from r3_onshape.native import NativeCadTransport


def transport(tmp_path, raw):
    (tmp_path / "bridge.json").write_text(json.dumps({
        "host": "127.0.0.1", "port": 8766, "token": "synthetic-token"}))
    client = NativeCadTransport(tmp_path)
    client._opener = Mock()
    client._opener.open.return_value = io.BytesIO(raw)
    return client


def test_viewer_mesh_uses_only_the_local_tool_route(tmp_path):
    response = {"snapshot": "a" * 64 + ".json", "microversion": "b" * 24,
                "total": 1, "exact_cad": False}
    client = transport(tmp_path, json.dumps(response).encode())
    arguments = {"url": "https://cad.onshape.com/documents/" + "a" * 24 +
                 "/w/" + "b" * 24 + "/e/" + "c" * 24,
                 "action": "list", "tab_id": 123}
    assert client.local("viewer_mesh", arguments) == response
    request = client._opener.open.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8766/local/viewer_mesh"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == arguments
    assert request.get_header("Authorization") == "Bearer synthetic-token"
    assert client._opener.open.call_count == 1
    assert not (tmp_path / "cad").exists()


@pytest.mark.parametrize("suffix", ["obj", "json"])
def test_exact_cached_bytes_without_geometry_conversion_or_remote_request(tmp_path, suffix):
    raw = b"# opaque synthetic cache payload; preserve every byte\n"
    handle = hashlib.sha256(raw).hexdigest() + "." + suffix
    client = transport(tmp_path, raw)
    assert client.artifact_bytes(handle) == raw
    request = client._opener.open.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8766/artifacts/" + handle
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer synthetic-token"
    assert client._opener.open.call_count == 1
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("handle", ["bridge.json", "../" + "a" * 64 + ".obj",
    "a" * 64 + ".step", "A" * 64 + ".obj", "a" * 64 + ".obj?token=bad"])
def test_binary_cache_access_does_not_expand_filesystem_or_route_access(tmp_path, handle):
    client = transport(tmp_path, b"ignored")
    with pytest.raises(PullError) as error:
        client.artifact_bytes(handle)
    assert error.value.code == "cad_native_invalid"
    client._opener.open.assert_not_called()


def test_modified_obj_is_rejected_without_repair_or_fallback(tmp_path):
    client = transport(tmp_path, b"modified mesh")
    with pytest.raises(PullError) as error:
        client.artifact_bytes("0" * 64 + ".obj")
    assert error.value.code == "cad_native_invalid"
    assert "checksum" in str(error.value).lower()
    assert client._opener.open.call_count == 1


def test_json_reader_still_refuses_obj(tmp_path):
    client = transport(tmp_path, b"ignored")
    with pytest.raises(PullError):
        client.artifact("0" * 64 + ".obj")
    client._opener.open.assert_not_called()


def test_agent_facing_tool_preserves_unqualified_viewer_result(tmp_path, monkeypatch):
    import r3_onshape.native as native
    from r3_orchestrator import mcp_server

    response = {"artifact": "a" * 64 + ".obj", "exact_cad": False,
                "closed_solid_verified": False, "units": "meter",
                "coordinate_frame": "part-local"}
    client = transport(tmp_path, json.dumps(response).encode())
    monkeypatch.setattr(native, "NativeCadTransport", lambda: client)
    arguments = {"url": "https://cad.onshape.com/documents/" + "a" * 24 +
                 "/w/" + "b" * 24 + "/e/" + "c" * 24,
                 "action": "download", "snapshot": "d" * 64 + ".json", "part_index": 0}
    assert mcp_server.r3_onshape_native("viewer_mesh", arguments) == {"ok": True, "result": response}
    request = client._opener.open.call_args.args[0]
    assert json.loads(request.data) == arguments
    assert client._opener.open.call_count == 1
