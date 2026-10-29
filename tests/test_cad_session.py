"""Headless Onshape session transport against a local fake server: login sets
cookies, writes carry X-XSRF-TOKEN, cookies persist (mode 0600), redirects are
followed, expired sessions are reported."""

import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from r3_onshape.session import SessionExpired, SessionTransport

SEEN: list[dict] = []


class FakeOnshape(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - silence
        pass

    def _send(self, code: int, body: dict | None = None, cookies: tuple[str, ...] = (), location: str = "") -> None:
        data = json.dumps(body or {}).encode()
        self.send_response(code)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        SEEN.append({"method": "GET", "path": self.path, "cookie": self.headers.get("Cookie", ""), "xsrf": self.headers.get("X-XSRF-TOKEN")})
        if self.path == "/signin":
            self._send(200, cookies=("XSRF-TOKEN=tok123; Path=/",))
        elif self.path.startswith("/api/v17/users/sessioninfo"):
            if "on.sid=abc" in self.headers.get("Cookie", ""):
                self._send(200, {"id": "u1", "name": "Test User"})
            else:
                self._send(401, {"message": "not logged in"})
        elif self.path.startswith("/api/v17/redirect"):
            self._send(307, location="/api/v17/target")
        elif self.path.startswith("/api/v17/target"):
            self._send(200, {"ok": True})
        else:
            self._send(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        SEEN.append({"method": "POST", "path": self.path, "cookie": self.headers.get("Cookie", ""),
                     "xsrf": self.headers.get("X-XSRF-TOKEN"), "body": body})
        if self.path.startswith("/api/v17/users/session"):
            if body.get("password") == "secret":
                self._send(200, {"id": "u1"}, cookies=("on.sid=abc; Path=/; HttpOnly",))
            else:
                self._send(401, {"message": "bad credentials"})
        else:
            self._send(200, {"echo": body})


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), FakeOnshape)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    SEEN.clear()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_login_cookies_xsrf_and_persistence(server, tmp_path):
    session_file = tmp_path / "session.json"
    api = SessionTransport(server, session_file)
    assert not api.healthy()
    api.login("user@example.com", "secret")
    assert api.healthy()
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
    api.json("POST", "/partstudios/d/D/w/W/e/E/features", body={"feature": {"featureType": "extrude", "namespace": ""}})
    write = [s for s in SEEN if s["method"] == "POST" and "features" in s["path"]][-1]
    assert write["xsrf"] == "tok123" and "on.sid=abc" in write["cookie"]
    # A new process reuses the persisted session without logging in again.
    again = SessionTransport(server, session_file)
    assert again.healthy()
    # Redirects (e.g. signed export URLs) are followed by the client itself.
    assert again.json("GET", "/redirect") == {"ok": True}


def test_rejected_login_and_expired_session(server, tmp_path):
    api = SessionTransport(server, tmp_path / "s.json")
    with pytest.raises(SessionExpired):
        api.login("user@example.com", "wrong")
    api.import_cookies("on.sid=stale; XSRF-TOKEN=t")
    with pytest.raises(SessionExpired):
        api.session_info()
