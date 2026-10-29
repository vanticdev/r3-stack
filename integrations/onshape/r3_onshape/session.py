"""Headless Onshape transport over a logged-in web session (no browser, no extension).

Onshape's own web client authenticates ``/api`` calls with its session cookies
and, for state-changing requests, an ``X-XSRF-TOKEN`` header copied from the
``XSRF-TOKEN`` cookie. :class:`SessionTransport` does exactly that from a plain
Python process, so R3 can build CAD on a server:

* ``login``: ``POST /api/users/session`` with email/password (and a TOTP code if
  two-factor auth is on) creates a session.
* ``import_cookies``: alternatively start from a ``Cookie`` header copied once
  from a logged-in browser (for accounts where scripted login is challenged).

The cookie jar is persisted to ``~/.config/r3/onshape-session.json`` (mode
0600, outside any repository) and refreshed from every ``Set-Cookie``, so the
session stays alive across runs. Redirects (e.g. signed export URLs) are
followed by this client. Every request first passes the direct-CAD policy.

CLI::

    python -m r3_onshape.session login --email you@example.com --password-file /path/secret [--totp 123456]
    python -m r3_onshape.session import-cookies --cookie-file /path/cookie_header.txt
    python -m r3_onshape.session status
    python -m r3_onshape.session logout
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .client import API, OnshapeError, Transport, _url
from .policy import check_request

DEFAULT_BASE = "https://cad.onshape.com"
SESSION_FILE = Path(os.environ.get("R3_ONSHAPE_SESSION", Path.home() / ".config" / "r3" / "onshape-session.json"))
USER_AGENT = "r3-onshape/1.0 (+headless session client)"


class QuotaExhausted(OnshapeError):
    """An endpoint's Onshape API quota is used up (HTTP 429; every quota response stops immediately)."""


class SessionExpired(OnshapeError):
    pass


def _cookie_to_dict(cookie: http.cookiejar.Cookie) -> dict[str, Any]:
    return {"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path,
            "secure": cookie.secure, "expires": cookie.expires}


def _dict_to_cookie(data: dict[str, Any]) -> http.cookiejar.Cookie:
    domain = data.get("domain") or "cad.onshape.com"
    return http.cookiejar.Cookie(
        version=0, name=data["name"], value=data["value"], port=None, port_specified=False, domain=domain,
        domain_specified=True, domain_initial_dot=domain.startswith("."), path=data.get("path") or "/", path_specified=True,
        secure=bool(data.get("secure", True)), expires=data.get("expires"), discard=False, comment=None, comment_url=None,
        rest={}, rfc2109=False)


class SessionTransport(Transport):
    """Onshape REST over a persisted, logged-in web session."""

    name = "onshape-session"

    def __init__(self, base: str = DEFAULT_BASE, session_file: Path | None = None) -> None:
        self.base = base.rstrip("/")
        self.session_file = Path(session_file or SESSION_FILE)
        self.jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                   urllib.request.HTTPRedirectHandler())
        self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self) -> None:
        if not self.session_file.exists():
            return
        data = json.loads(self.session_file.read_text())
        if data.get("base", self.base) != self.base:
            return
        for item in data.get("cookies", []):
            self.jar.set_cookie(_dict_to_cookie(item))

    def save(self) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"base": self.base, "saved_at": time.time(), "cookies": [_cookie_to_dict(c) for c in self.jar]}
        tmp = self.session_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.session_file)

    def clear(self) -> None:
        self.jar.clear()
        if self.session_file.exists():
            self.session_file.unlink()

    def _cookie(self, name: str) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return urllib.parse.unquote(cookie.value)
        return None

    @property
    def has_session(self) -> bool:
        return any(True for _ in self.jar)

    # ------------------------------------------------------------------ requests
    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None,
                accept: str = "application/json", timeout: float = 120.0) -> bytes:
        check_request(method, path, body)  # direct-CAD policy: no FeatureScript, ever
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": accept, "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        xsrf = self._cookie("XSRF-TOKEN")
        if xsrf and method != "GET":
            headers["X-XSRF-TOKEN"] = xsrf
        req = urllib.request.Request(_url(self.base, path, query), data=data, method=method, headers=headers)
        for attempt in range(9):
            try:
                with self._opener.open(req, timeout=timeout) as response:
                    raw = response.read()
                self.save()  # keep rotated cookies
                return raw
            except urllib.error.HTTPError as error:
                text = error.read().decode(errors="replace")
                if error.code in (401, 403) and "login" in text.lower() or error.code == 401:
                    raise SessionExpired(error.code, f"Onshape session is not logged in or expired: {text[:200]}") from None
                if error.code == 429:
                    raise QuotaExhausted(429, f"{method} {path}: {text}", error.headers) from None
                if method in ("GET", "DELETE") and error.code in (502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise OnshapeError(error.code, text) from None
            except urllib.error.URLError as error:
                if attempt < 3 and method in ("GET", "DELETE"):
                    time.sleep(2 ** attempt)
                    continue
                raise OnshapeError(0, str(error)) from None
        raise OnshapeError(0, "unreachable")

    # ------------------------------------------------------------------- session
    def login(self, email: str, password: str, totp: str | None = None) -> dict[str, Any]:
        """Create a session with credentials. Raises OnshapeError on rejection
        (wrong password, TOTP required, or a reCAPTCHA challenge)."""
        self.jar.clear()
        # Prime anonymous cookies (XSRF-TOKEN) exactly like the sign-in page does.
        try:
            self._opener.open(urllib.request.Request(self.base + "/signin", headers={"User-Agent": USER_AGENT}), timeout=30).read()
        except urllib.error.HTTPError:
            pass
        body: dict[str, Any] = {"email": email, "password": password}
        if totp:
            body.update({"totp": totp, "rememberTotp": True})
        info = self.json("POST", "/users/session", body=body)
        self.save()
        return info or {}

    def import_cookies(self, cookie_header: str, domain: str = "cad.onshape.com") -> None:
        """Start from a browser ``Cookie`` header (``name=value; name2=value2``)."""
        self.jar.clear()
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.strip().split("=", 1)
            self.jar.set_cookie(_dict_to_cookie({"name": name, "value": value, "domain": domain, "path": "/", "secure": True}))
        self.save()

    def session_info(self) -> dict[str, Any]:
        return self.json("GET", "/users/sessioninfo") or {}

    def healthy(self) -> bool:
        if not self.has_session:
            return False
        try:
            info = self.session_info()
            return bool(info.get("id"))
        except (OnshapeError, ValueError, OSError) as error:
            from .quota import raise_if_quota
            raise_if_quota(error)
            return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m r3_onshape.session", description="Headless Onshape session for R3")
    parser.add_argument("--base", default=os.environ.get("ONSHAPE_BASE", DEFAULT_BASE))
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login")
    login.add_argument("--email", default=os.environ.get("ONSHAPE_EMAIL"))
    login.add_argument("--password-file", help="file containing the password (never pass it on the command line)")
    login.add_argument("--totp")
    imp = sub.add_parser("import-cookies")
    imp.add_argument("--cookie-file", required=True, help="file containing a browser Cookie header")
    sub.add_parser("status")
    sub.add_parser("logout")
    args = parser.parse_args(argv)
    api = SessionTransport(args.base)
    if args.command == "login":
        password = Path(args.password_file).read_text().strip() if args.password_file else os.environ.get("ONSHAPE_PASSWORD", "")
        if not args.email or not password:
            parser.error("email and password are required (ONSHAPE_EMAIL / --password-file)")
        api.login(args.email, password, args.totp)
    elif args.command == "import-cookies":
        api.import_cookies(Path(args.cookie_file).read_text().strip())
    elif args.command == "logout":
        api.clear()
        print("session cleared")
        return 0
    info = api.session_info() if api.has_session else {}
    print(json.dumps({"logged_in": bool(info.get("id")), "user": info.get("name"), "session_file": str(api.session_file)}))
    return 0 if info.get("id") else 1


if __name__ == "__main__":
    sys.exit(main())
