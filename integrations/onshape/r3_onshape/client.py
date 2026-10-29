"""Onshape REST transports.

Two transports expose the same ``request`` method, so sync logic is
transport-independent:

* :class:`ApiKeyTransport`: official API keys (``ONSHAPE_ACCESS_KEY`` /
  ``ONSHAPE_SECRET_KEY``). This is the headless transport for servers and CI.
  Requests are HMAC-SHA256 signed by default (Onshape's "request signature"
  scheme). Basic auth, which Onshape documents as "for local testing only", is
  available with ``ONSHAPE_API_AUTH=basic``.
* :class:`NativeBridgeTransport`: the local ``onshape-native`` companion
  (browser-session auth). It proxies official ``/api/v17`` paths. It needs a
  logged-in browser, so it is a local-development convenience only.

``R3_ONSHAPE_TRANSPORT`` selects one explicitly: ``api-keys``, ``native``,
``none`` or ``auto`` (the default: the bridge when connected, else API keys).
"""

from __future__ import annotations

import base64
import email.utils
import hashlib
import hmac
import json
import os
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .policy import check_request

API = "/api/v17"


class OnshapeError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Onshape HTTP {status}: {message[:500]}")
        self.status = status


class Transport:
    name = "abstract"

    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None,
                accept: str = "application/json", timeout: float = 120.0) -> bytes:
        raise NotImplementedError

    def json(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None) -> Any:
        raw = self.request(method, path, query, body)
        return json.loads(raw) if raw else None


def _url(base: str, path: str, query: dict[str, Any] | None) -> str:
    url = base + (path if path.startswith("/api/") else API + path)
    if query:
        clean = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in query.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface redirects as HTTPError so the transport can re-sign them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_REDIRECTS = (301, 302, 303, 307, 308)


class _HttpTransport(Transport):
    base = ""
    # True for transports whose auth depends on the URL (request signatures):
    # redirects are followed manually with fresh headers.
    resign_redirects = False

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _auth_headers(self, method: str, url: str, content_type: str) -> dict[str, str]:
        return self._headers()

    def _open(self, method: str, url: str, data: bytes | None, accept: str, timeout: float) -> bytes:
        content_type = "application/json"
        headers = {"Accept": accept, **self._auth_headers(method, url, content_type)}
        if data is not None or self.resign_redirects:
            headers["Content-Type"] = content_type
        if not self.resign_redirects:
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        opener = urllib.request.build_opener(_NoRedirect)
        for _ in range(5):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with opener.open(req, timeout=timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                location = error.headers.get("Location") if error.code in _REDIRECTS else None
                if not location:
                    raise
                error.read()
                # Onshape: "generate a new Authorization header for the redirect URL".
                url = urllib.parse.urljoin(url, location)
                if error.code == 303:
                    method, data = "GET", None
                headers = {"Accept": accept, "Content-Type": content_type, **self._auth_headers(method, url, content_type)}
        raise OnshapeError(310, f"too many redirects for {method} {url}")

    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None,
                accept: str = "application/json", timeout: float = 120.0) -> bytes:
        check_request(method, path, body)  # direct-CAD policy: no FeatureScript, ever
        data = json.dumps(body).encode() if body is not None else None
        url = _url(self.base, path, query)
        for attempt in range(4):
            try:
                return self._open(method, url, data, accept, timeout)
            except urllib.error.HTTPError as error:
                text = error.read().decode(errors="replace")
                # Only idempotent reads are retried, and only on throttling/transient errors.
                # The native bridge reports a reloaded browser frame as 502.
                if method in ("GET", "DELETE") and error.code in (429, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise OnshapeError(error.code, text) from None
        raise OnshapeError(0, "unreachable")


class NativeBridgeTransport(_HttpTransport):
    """``onshape-native`` local companion (http://127.0.0.1:8766)."""

    name = "onshape-native"

    def __init__(self, runtime: Path | None = None) -> None:
        candidates = [Path(p) for p in (os.environ.get("ONSHAPE_NATIVE_RUNTIME", ""),) if p]
        candidates.append(Path.home() / "Documents" / "onshape-stuff" / "onshape-native" / ".runtime")
        if runtime:
            candidates.insert(0, runtime)
        for candidate in candidates:
            config = candidate / "bridge.json"
            if config.exists():
                cfg = json.loads(config.read_text())
                self.base = f"http://{cfg.get('host', '127.0.0.1')}:{cfg['port']}"
                self._token = cfg["token"]
                return
        raise FileNotFoundError("onshape-native bridge.json not found (set ONSHAPE_NATIVE_RUNTIME)")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self._token}

    def healthy(self) -> bool:
        try:
            raw = self.request("GET", "/health", timeout=5)
            return bool(json.loads(raw).get("extension_connected"))
        except (OSError, OnshapeError, ValueError):
            return False

    def request(self, method: str, path: str, query=None, body=None, accept="application/json", timeout=120.0) -> bytes:
        if path == "/health":
            req = urllib.request.Request(self.base + "/health", headers=self._headers())
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        return super().request(method, path, query, body, accept, timeout)


_NONCE_ALPHABET = string.ascii_letters + string.digits


def sign_request(method: str, url: str, nonce: str, date: str, content_type: str, access_key: str, secret_key: str) -> str:
    """Onshape API-key request signature (``Authorization`` header value).

    String to sign, lowercased: method, nonce, date, content type, URL path and
    URL query (empty if none), each followed by a newline; signature =
    base64(HMAC-SHA256(secret, string)). Header: ``On <access>:HmacSHA256:<signature>``.
    Source: https://onshape-public.github.io/docs/auth/apikeys/ (request signature).
    """
    parsed = urllib.parse.urlsplit(url)
    text = f"{method}\n{nonce}\n{date}\n{content_type}\n{parsed.path}\n{parsed.query}\n".lower()
    digest = hmac.new(secret_key.encode(), text.encode(), hashlib.sha256).digest()
    return f"On {access_key}:HmacSHA256:{base64.b64encode(digest).decode()}"


class ApiKeyTransport(_HttpTransport):
    """Official Onshape API keys: the headless transport for servers and CI.

    ``auth="hmac"`` (default) signs every request, including each redirect hop;
    ``auth="basic"`` sends the keys as HTTP basic credentials.
    """

    name = "onshape-api-keys"
    resign_redirects = True

    def __init__(self, access_key: str, secret_key: str, base: str = "https://cad.onshape.com", auth: str = "hmac") -> None:
        if auth not in ("hmac", "basic"):
            raise ValueError(f"auth must be 'hmac' or 'basic', not {auth!r}")
        self.base = base.rstrip("/")
        self.auth = auth
        self._access, self._secret = access_key, secret_key

    @classmethod
    def from_env(cls) -> "ApiKeyTransport | None":
        """Keys from ``ONSHAPE_ACCESS_KEY``/``ONSHAPE_SECRET_KEY``; ``ONSHAPE_BASE_URL``
        (enterprise domains) and ``ONSHAPE_API_AUTH`` (hmac|basic) are optional."""
        access, secret = os.environ.get("ONSHAPE_ACCESS_KEY"), os.environ.get("ONSHAPE_SECRET_KEY")
        if not (access and secret):
            return None
        return cls(access, secret, base=os.environ.get("ONSHAPE_BASE_URL") or "https://cad.onshape.com",
                   auth=(os.environ.get("ONSHAPE_API_AUTH") or "hmac").lower())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Basic " + base64.b64encode(f"{self._access}:{self._secret}".encode()).decode()}

    def _auth_headers(self, method: str, url: str, content_type: str) -> dict[str, str]:
        if self.auth == "basic":
            return self._headers()
        nonce = "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(25))
        date = email.utils.formatdate(usegmt=True)
        return {"Date": date, "On-Nonce": nonce,
                "Authorization": sign_request(method, url, nonce, date, content_type, self._access, self._secret)}


TRANSPORT_CHOICES = ("auto", "api-keys", "native", "none")


def _native_if_connected() -> NativeBridgeTransport | None:
    try:
        native = NativeBridgeTransport()
    except FileNotFoundError:
        return None
    return native if native.healthy() else None


def default_transport() -> Transport | None:
    """The transport selected by ``R3_ONSHAPE_TRANSPORT`` (default ``auto``: the
    native bridge if connected, else API keys from the environment, else None)."""
    choice = (os.environ.get("R3_ONSHAPE_TRANSPORT") or "auto").lower()
    if choice not in TRANSPORT_CHOICES:
        raise ValueError(f"R3_ONSHAPE_TRANSPORT must be one of {', '.join(TRANSPORT_CHOICES)}, not {choice!r}")
    if choice == "none":
        return None
    if choice == "api-keys":
        return ApiKeyTransport.from_env()
    native = _native_if_connected()
    if native is not None or choice == "native":
        return native
    return ApiKeyTransport.from_env()


def describe_transport() -> dict[str, Any]:
    """What ``default_transport`` would use, without secrets or network calls
    to Onshape (only the local bridge health endpoint is probed)."""
    choice = (os.environ.get("R3_ONSHAPE_TRANSPORT") or "auto").lower()
    keys = ApiKeyTransport.from_env() if choice in ("auto", "api-keys") else None
    try:
        bridge = NativeBridgeTransport() if choice in ("auto", "native") else None
        bridge_configured = bridge is not None
    except FileNotFoundError:
        bridge, bridge_configured = None, False
    bridge_connected = bool(bridge and bridge.healthy())
    if bridge_connected:
        selected = NativeBridgeTransport.name
    elif keys is not None and choice != "native":
        selected = ApiKeyTransport.name
    else:
        selected = None
    return {"choice": choice, "selected": selected, "headless": selected == ApiKeyTransport.name,
            "api_keys": {"configured": keys is not None, "auth": keys.auth if keys else None, "base": keys.base if keys else None},
            "native_bridge": {"configured": bridge_configured, "connected": bridge_connected}}
