"""Headless Onshape: API-key request signatures, redirect re-signing, transport
selection and server-rendered shaded views. No network beyond 127.0.0.1."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from r3_onshape import client, views
from r3_onshape import session as onshape_session
from r3_onshape.client import ApiKeyTransport, sign_request
from r3_onshape.policy import FeatureScriptForbidden


def test_signature_matches_onshape_scheme():
    # Independent restatement of https://onshape-public.github.io/docs/auth/apikeys/ (request signature).
    url = "https://cad.onshape.com/api/v17/documents/d/ABC/w/DEF/elements?withThumbnails=False&elementType=PARTSTUDIO"
    nonce, date = "aB3dE5fG7hI9jK1lM3nO5pQ7r", "Mon, 11 Apr 2016 20:08:56 GMT"
    text = ("GET\n" + nonce + "\n" + date + "\napplication/json\n/api/v17/documents/d/ABC/w/DEF/elements\n"
            "withThumbnails=False&elementType=PARTSTUDIO\n").lower()
    expected = base64.b64encode(hmac.new(b"secret", text.encode(), hashlib.sha256).digest()).decode()
    assert sign_request("GET", url, nonce, date, "application/json", "access", "secret") == f"On access:HmacSHA256:{expected}"


class _Recorder(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):  # silence
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
        if self.path.startswith("/api/v17/redirect"):
            self.send_response(307)
            self.send_header("Location", "/api/v17/target?x=1")
            self.end_headers()
            return
        payload = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_DELETE = _handle


@pytest.fixture
def server():
    _Recorder.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", _Recorder.seen
    httpd.shutdown()


def _verify(request: dict, access: str, secret: str) -> None:
    headers = {k.lower(): v for k, v in request["headers"].items()}
    assert len(headers["on-nonce"]) >= 16 and headers["on-nonce"].isalnum()
    assert headers["date"].endswith("GMT")
    path, _, query = request["path"].partition("?")
    text = f"{request['method']}\n{headers['on-nonce']}\n{headers['date']}\n{headers['content-type']}\n{path}\n{query}\n".lower()
    signature = base64.b64encode(hmac.new(secret.encode(), text.encode(), hashlib.sha256).digest()).decode()
    assert headers["authorization"] == f"On {access}:HmacSHA256:{signature}"


def test_hmac_transport_signs_each_request_and_redirect(server):
    base, seen = server
    api = ApiKeyTransport("AK", "SK", base=base)
    assert api.json("GET", "/documents", query={"q": "sensor rig", "ownerType": 1}) == {"ok": True}
    assert api.json("POST", "/documents", body={"name": "x"}) == {"ok": True}
    assert api.json("GET", "/redirect", query={"a": "b"}) == {"ok": True}
    assert [r["path"] for r in seen] == ["/api/v17/documents?q=sensor+rig&ownerType=1", "/api/v17/documents",
                                         "/api/v17/redirect?a=b", "/api/v17/target?x=1"]
    for request in seen:
        _verify(request, "AK", "SK")
    assert json.loads(seen[1]["body"]) == {"name": "x"}
    # The redirect hop carries a fresh nonce and a signature for the new path.
    assert seen[2]["headers"]["On-Nonce"] != seen[3]["headers"]["On-Nonce"]


def test_basic_auth_option(server):
    base, seen = server
    ApiKeyTransport("AK", "SK", base=base, auth="basic").json("GET", "/documents")
    assert seen[0]["headers"]["Authorization"] == "Basic " + base64.b64encode(b"AK:SK").decode()
    with pytest.raises(ValueError):
        ApiKeyTransport("AK", "SK", auth="plain")


def test_policy_still_enforced_with_api_keys():
    api = ApiKeyTransport("k", "s", base="http://127.0.0.1:9")
    with pytest.raises(FeatureScriptForbidden):
        api.request("POST", "/partstudios/d/D/w/W/e/E/featurescript", body={"script": "x"})


@pytest.fixture
def no_bridge(monkeypatch, tmp_path):
    monkeypatch.setattr(client.Path, "home", classmethod(lambda cls: tmp_path))
    # Never pick up (or probe Onshape with) the developer's stored headless session.
    monkeypatch.setattr(onshape_session, "SESSION_FILE", tmp_path / "onshape-session.json")
    monkeypatch.delenv("ONSHAPE_NATIVE_RUNTIME", raising=False)
    for key in ("ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY", "ONSHAPE_BASE_URL", "ONSHAPE_API_AUTH", "R3_ONSHAPE_TRANSPORT"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_transport_selection_from_environment(no_bridge):
    monkeypatch = no_bridge
    assert client.default_transport() is None
    assert client.describe_transport()["selected"] is None
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "AK")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "very-secret")
    monkeypatch.setenv("ONSHAPE_BASE_URL", "https://acme.onshape.com/")
    api = client.default_transport()
    assert isinstance(api, ApiKeyTransport) and api.auth == "hmac" and api.base == "https://acme.onshape.com"
    info = client.describe_transport()
    assert info["selected"] == "onshape-api-keys" and info["headless"] is True
    assert "very-secret" not in json.dumps(info) and "AK" not in json.dumps(info)
    monkeypatch.setenv("R3_ONSHAPE_TRANSPORT", "none")
    assert client.default_transport() is None
    monkeypatch.setenv("R3_ONSHAPE_TRANSPORT", "native")
    assert client.default_transport() is None  # no bridge here; never falls back to keys when native is forced
    monkeypatch.setenv("R3_ONSHAPE_TRANSPORT", "api-keys")
    monkeypatch.setenv("ONSHAPE_API_AUTH", "basic")
    assert client.default_transport().auth == "basic"
    monkeypatch.setenv("R3_ONSHAPE_TRANSPORT", "session")
    forced = client.default_transport()  # forced session is returned even before login, so it can report expiry
    assert isinstance(forced, onshape_session.SessionTransport) and not forced.has_session
    assert client.describe_transport()["selected"] == "onshape-session"
    monkeypatch.setenv("R3_ONSHAPE_TRANSPORT", "bogus")
    with pytest.raises(ValueError):
        client.default_transport()


def test_auto_prefers_keys_then_stored_session(no_bridge, tmp_path):
    monkeypatch = no_bridge
    stored = onshape_session.SessionTransport()
    stored.jar.set_cookie(onshape_session._dict_to_cookie({"name": "JSESSIONID", "value": "x"}))
    stored.save()
    monkeypatch.setattr(onshape_session.SessionTransport, "healthy", lambda self: self.has_session)  # no network
    assert isinstance(client.default_transport(), onshape_session.SessionTransport)
    info = client.describe_transport()
    assert info["selected"] == "onshape-session" and info["headless"] is True and info["session"]["stored"] is True
    assert "JSESSIONID" not in json.dumps(info)
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "AK")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "SK")
    assert isinstance(client.default_transport(), ApiKeyTransport)
    assert client.describe_transport()["selected"] == "onshape-api-keys"


# ----------------------------------------------------------------- shaded views
def _png(color: tuple[int, int, int]) -> str:
    out = io.BytesIO()
    Image.new("RGB", (4, 3), color).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


class FakeApi(client.Transport):
    name = "fake"

    def __init__(self, assembly: bool = False, empty_assembly: bool = False) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.assembly = assembly
        self.empty_assembly = empty_assembly

    def request(self, method, path, query=None, body=None, accept="application/json", timeout=120.0):
        self.calls.append((method, path, dict(query or {})))
        if path.endswith("/elements"):
            elements = [{"id": "PS", "elementType": "PARTSTUDIO"}, {"id": "EMPTY", "elementType": "ASSEMBLY"}]
            elements += [{"id": "ASM", "elementType": "ASSEMBLY"}] if self.assembly else []
            return json.dumps(elements).encode()
        if path.startswith("/assemblies/") and not path.endswith("/shadedviews"):
            instances = [{"id": "i1"}] if path.endswith("/e/ASM") else []
            return json.dumps({"rootAssembly": {"instances": instances}}).encode()
        if path.endswith("/shadedviews"):
            return json.dumps({"images": [_png((200, 10, 10))]}).encode()
        raise AssertionError(path)


def test_iso_view_matrix_is_a_rotation():
    m = np.array([float(v) for v in views.VIEWS["iso"].split(",")]).reshape(3, 4)
    rotation = m[:, :3]
    assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-6) and np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-6)
    assert np.allclose(m[:, 3], 0)
    # Viewer on the +x, -y, +z side; model z points up on screen.
    assert np.allclose(rotation[2], np.array([1, -1, 1]) / np.sqrt(3), atol=1e-6) and rotation[1, 2] > 0.8


def test_render_document_views_writes_pngs(tmp_path: Path):
    api = FakeApi(assembly=True)
    paths = views.render_document_views(api, "D", "W", "PS", tmp_path)
    assert sorted(p.name for p in paths) == sorted(["iso.png", "front.png", "top.png", "right.png", "assembly_iso.png",
                                                     "assembly_front.png", "assembly_top.png", "assembly_right.png"])
    assert Image.open(paths[0]).getpixel((0, 0)) == (200, 10, 10)
    shaded = [c for c in api.calls if c[1].endswith("/shadedviews")]
    assert all(method == "GET" for method, _, _ in shaded)
    assert shaded[0][1] == "/partstudios/d/D/w/W/e/PS/shadedviews" and shaded[0][2]["viewMatrix"] == views.VIEWS["iso"]
    assert shaded[0][2]["pixelSize"] == 0 and shaded[0][2]["showAllParts"] is True
    assert shaded[1][2]["viewMatrix"] == "front"
    assert shaded[4][1] == "/assemblies/d/D/w/W/e/ASM/shadedviews" and "showAllParts" not in shaded[4][2]
    # The empty default Assembly of a new document is not rendered.
    only_empty = FakeApi(assembly=False)
    assert [p.name for p in views.render_document_views(only_empty, "D", "W", "PS", tmp_path / "b")] == \
        ["iso.png", "front.png", "top.png", "right.png"]


def test_shaded_view_decodes_nested_images_and_jpeg(tmp_path: Path):
    out = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 0, 255)).save(out, format="JPEG")

    class Jpeg(FakeApi):
        def request(self, method, path, query=None, body=None, accept="application/json", timeout=120.0):
            return json.dumps({"images": [[base64.b64encode(out.getvalue()).decode()]]}).encode()

    png = views.shaded_view(Jpeg(), views.ElementRef("D", "w", "W", "E"), "top")
    assert png.startswith(b"\x89PNG")

    class Empty(FakeApi):
        def request(self, *args, **kwargs):
            return b'{"images": []}'

    with pytest.raises(ValueError):
        views.shaded_view(Empty(), views.ElementRef("D", "w", "W", "E"), "top")


def test_cad_stage_views_hook(tmp_path: Path, monkeypatch):
    from r3_model.diagnostics import StageResult
    from r3_onshape import stage

    class P:
        root = tmp_path

        def build_dir(self, name=""):
            path = tmp_path / "build" / name
            path.mkdir(parents=True, exist_ok=True)
            return path

    result = StageResult(stage="cad", inputs={})
    stage._views(P(), FakeApi(), {"document_id": "D", "workspace_id": "W", "element_id": "PS"}, result)
    assert result.metrics["cad_views"] == 4
    assert sorted(p.name for p in (tmp_path / "build/cad/views").iterdir()) == ["front.png", "iso.png", "right.png", "top.png"]

    class Broken(FakeApi):
        def request(self, *args, **kwargs):
            raise client.OnshapeError(403, "forbidden")

    failed = StageResult(stage="cad", inputs={})
    stage._views(P(), Broken(), {"document_id": "D", "workspace_id": "W", "element_id": "PS"}, failed)
    assert [d.code for d in failed.diagnostics] == ["cad_views_failed"] and failed.ok
    monkeypatch.setenv("R3_CAD_VIEWS", "0")
    skipped = StageResult(stage="cad", inputs={})
    stage._views(P(), Broken(), {"document_id": "D", "workspace_id": "W", "element_id": "PS"}, skipped)
    assert not skipped.diagnostics
