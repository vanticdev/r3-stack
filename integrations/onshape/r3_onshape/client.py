"""Onshape REST transports.

Preferred: the local ``onshape-native`` companion (browser-session auth, no
paid API keys). It proxies official ``/api/v17`` paths. Fallback: official API
keys (``ONSHAPE_ACCESS_KEY`` / ``ONSHAPE_SECRET_KEY``), e.g. in CI. Both expose
the same ``request`` method, so sync logic is transport-independent.
"""

from __future__ import annotations

import base64
import json
import os
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


class _HttpTransport(Transport):
    base = ""

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def request(self, method: str, path: str, query: dict[str, Any] | None = None, body: Any = None,
                accept: str = "application/json", timeout: float = 120.0) -> bytes:
        check_request(method, path, body)  # direct-CAD policy: no FeatureScript, ever
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": accept, **self._headers()}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(_url(self.base, path, query), data=data, method=method, headers=headers)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return response.read()
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


class ApiKeyTransport(_HttpTransport):
    """Official Onshape API keys (basic auth), for CI."""

    name = "onshape-api-keys"

    def __init__(self, access_key: str, secret_key: str, base: str = "https://cad.onshape.com") -> None:
        self.base = base
        self._auth = "Basic " + base64.b64encode(f"{access_key}:{secret_key}".encode()).decode()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._auth}


def default_transport() -> Transport | None:
    """Native bridge if connected, else API keys from the environment, else None."""
    try:
        native = NativeBridgeTransport()
        if native.healthy():
            return native
    except FileNotFoundError:
        pass
    access, secret = os.environ.get("ONSHAPE_ACCESS_KEY"), os.environ.get("ONSHAPE_SECRET_KEY")
    if access and secret:
        return ApiKeyTransport(access, secret)
    return None
