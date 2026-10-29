"""HTTP for sourcing providers: caching, robots.txt, recorded fixtures.

* Every response is cached on disk (``$R3_SOURCING_CACHE``, default
  ``~/.cache/r3/sourcing``) and reused for ``ttl`` seconds, so repeated
  searches do not hit vendors again.
* Before fetching from a host, its ``robots.txt`` is read (and cached); a
  disallowed URL raises :class:`RobotsDisallowed`. Only public pages and
  documented APIs are used. Nothing behind a login is fetched.
* ``fixtures=<dir>`` serves recorded responses; with ``offline=True`` (or
  ``R3_SOURCING_OFFLINE=1``) any request not in the cache or fixtures raises
  :class:`NetworkDisabled`. Tests use both. ``record=<dir>`` writes every
  network response there as a fixture.
* Secrets never reach fixtures or cache keys: query parameters and form fields
  named like keys/secrets/tokens are redacted, and request headers are not stored.
* PDFs are read with ``pypdf``. The extracted text is cached (and recorded) as a
  ``.txt`` document next to the response metadata, with the original's sha256 and size.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

USER_AGENT = "r3-sourcing/0.1 (+https://github.com/; robot design part sourcing; respects robots.txt)"
DEFAULT_TTL = 24 * 3600.0
_SECRET_NAMES = ("key", "secret", "token", "password", "apikey", "client_id")


class SourcingHttpError(RuntimeError):
    def __init__(self, url: str, status: int, message: str) -> None:
        super().__init__(f"HTTP {status} for {url}: {message[:300]}")
        self.url, self.status = url, status


class NetworkDisabled(SourcingHttpError):
    def __init__(self, url: str) -> None:
        super().__init__(url, 0, "network disabled (offline mode) and no cached or recorded response")


class RobotsDisallowed(SourcingHttpError):
    def __init__(self, url: str) -> None:
        super().__init__(url, 0, "disallowed by the site's robots.txt")


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    retrieved_at: float = field(default_factory=time.time)
    from_cache: bool = False

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class DocumentText:
    url: str
    text: str
    pages: int
    sha256: str
    size: int
    retrieved_at: float
    from_cache: bool = False


def redact_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = [(k, "REDACTED" if any(s in k.lower() for s in _SECRET_NAMES) else v)
             for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _redact_body(body: bytes | None) -> bytes:
    if not body:
        return b""
    text = body.decode("utf-8", errors="replace")
    if "=" in text and "&" in text or text.startswith(("grant_type", "client_id")):
        pairs = urllib.parse.parse_qsl(text, keep_blank_values=True)
        if pairs:
            return urllib.parse.urlencode([(k, "REDACTED" if any(s in k.lower() for s in _SECRET_NAMES) else v)
                                           for k, v in pairs]).encode()
    return body


def request_key(method: str, url: str, body: bytes | None = None) -> str:
    material = f"{method.upper()} {redact_url(url)}\n".encode() + hashlib.sha256(_redact_body(body)).digest()
    return hashlib.sha256(material).hexdigest()[:24]


def default_cache_dir() -> Path:
    return Path(os.environ.get("R3_SOURCING_CACHE") or Path.home() / ".cache" / "r3" / "sourcing")


class _Store:
    """``<dir>/<key>.json`` metadata + ``<key>.body`` (or ``<key>.txt`` documents)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def load(self, key: str) -> tuple[dict[str, Any], bytes] | None:
        meta = self.root / f"{key}.json"
        if not meta.exists():
            return None
        data = json.loads(meta.read_text())
        body_path = self.root / data.get("body_file", f"{key}.body")
        return data, body_path.read_bytes() if body_path.exists() else b""

    def save(self, key: str, meta: dict[str, Any], body: bytes, suffix: str = ".body") -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{key}{suffix}").write_bytes(body)
        (self.root / f"{key}.json").write_text(json.dumps({**meta, "body_file": f"{key}{suffix}"}, indent=2, sort_keys=True))


class HttpClient:
    def __init__(self, cache_dir: Path | str | None = None, fixtures: Path | str | None = None, offline: bool | None = None,
                 record: Path | str | None = None, ttl: float = DEFAULT_TTL, respect_robots: bool = True,
                 timeout: float = 30.0, user_agent: str = USER_AGENT) -> None:
        self.cache = _Store(Path(cache_dir) if cache_dir else default_cache_dir())
        self.fixtures = _Store(Path(fixtures)) if fixtures else None
        self.recorder = _Store(Path(record)) if record else None
        self.offline = bool(int(os.environ.get("R3_SOURCING_OFFLINE", "0"))) if offline is None else offline
        self.ttl, self.respect_robots, self.timeout, self.user_agent = ttl, respect_robots, timeout, user_agent
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        #: URLs requested through this client (redacted), for provider reports.
        self.log: list[str] = []

    # ---------------------------------------------------------------- public
    def get(self, url: str, headers: dict[str, str] | None = None, ttl: float | None = None, check_robots: bool = True,
            record: bool = True) -> Response:
        return self.request("GET", url, headers=headers, ttl=ttl, check_robots=check_robots, record=record)

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None, ttl: float | None = None) -> Response:
        body = json.dumps(payload, sort_keys=True).encode()
        return self.request("POST", url, body=body, headers={"Content-Type": "application/json", **(headers or {})}, ttl=ttl,
                            check_robots=False)

    def post_form(self, url: str, form: dict[str, str], ttl: float = 0.0) -> Response:
        body = urllib.parse.urlencode(form).encode()
        return self.request("POST", url, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, ttl=ttl,
                            check_robots=False)

    def request(self, method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None,
                ttl: float | None = None, check_robots: bool = True, record: bool = True) -> Response:
        """Cached request. ``check_robots`` is False for documented APIs (robots.txt governs crawlers of pages)."""
        key = request_key(method, url, body)
        self.log.append(f"{method} {redact_url(url)}")
        ttl = self.ttl if ttl is None else ttl
        for store, fresh_only in ((self.fixtures, False), (self.cache, True)):
            if store is None:
                continue
            hit = store.load(key)
            if hit is None:
                continue
            meta, data = hit
            if fresh_only and ttl > 0 and time.time() - meta.get("retrieved_at", 0) > ttl and not self.offline:
                continue
            if fresh_only and ttl <= 0 and not self.offline:
                continue
            return Response(meta["url"], meta["status"], data, meta.get("headers", {}), meta.get("retrieved_at", 0), True)
        if self.offline:
            raise NetworkDisabled(redact_url(url))
        if check_robots and self.respect_robots and not self.allowed(url):
            raise RobotsDisallowed(redact_url(url))
        response = self._fetch(method, url, body, headers or {})
        meta = {"method": method, "url": redact_url(response.url), "request_url": redact_url(url), "status": response.status,
                "headers": response.headers, "retrieved_at": response.retrieved_at,
                "body_sha256": hashlib.sha256(response.body).hexdigest(), "size": len(response.body)}
        if response.status < 400:
            if ttl > 0:
                self.cache.save(key, meta, response.body)
            if self.recorder is not None and record:
                self.recorder.save(key, meta, response.body)
        if response.status >= 400:
            raise SourcingHttpError(redact_url(url), response.status, response.text)
        return response

    def document_text(self, url: str, max_bytes: int = 120_000_000) -> DocumentText:
        """Text of a PDF (pypdf), cached as an extracted-text document."""
        key = "doc-" + request_key("GET", url)
        for store in (self.fixtures, self.cache):
            hit = store.load(key) if store is not None else None
            if hit is not None:
                meta, data = hit
                return DocumentText(meta["url"], data.decode("utf-8"), meta["pages"], meta["sha256"], meta["size"],
                                    meta["retrieved_at"], True)
        # The raw PDF is not cached or recorded (product briefs reach 50 MB); its extracted text is.
        response = self.get(url, ttl=0.0, record=False)
        if len(response.body) > max_bytes:
            raise SourcingHttpError(url, 0, f"document is {len(response.body)} bytes (limit {max_bytes})")
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(response.body))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        meta = {"url": redact_url(url), "pages": len(reader.pages), "sha256": hashlib.sha256(response.body).hexdigest(),
                "size": len(response.body), "retrieved_at": response.retrieved_at, "extracted_with": "pypdf"}
        for store in (self.cache, self.recorder):
            if store is not None:
                store.save(key, meta, text.encode("utf-8"), suffix=".txt")
        return DocumentText(meta["url"], text, meta["pages"], meta["sha256"], meta["size"], meta["retrieved_at"])

    def download(self, url: str, target: Path) -> Path:
        """Download a file (vendor STEP model) into ``target``; reuses the cache."""
        response = self.get(url, ttl=365 * 24 * 3600.0)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.body)
        return target

    # -------------------------------------------------------------- robots
    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = self.request("GET", origin + "/robots.txt", ttl=7 * 24 * 3600.0, check_robots=False)
                parser.parse(response.text.splitlines())
            except SourcingHttpError as error:
                # RFC 9309: an unavailable robots.txt (4xx) means no restrictions.
                parser.parse([] if error.status and 400 <= error.status < 500 else ["User-agent: *", "Disallow: /"])
            self._robots[origin] = parser
        return parser.can_fetch(self.user_agent, url)

    # --------------------------------------------------------------- network
    def _fetch(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> Response:
        request = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": self.user_agent,
                                                                                 "Accept": "*/*", **headers})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as handle:
                data = handle.read()
                kept = {k: v for k, v in handle.headers.items() if k.lower() in ("content-type", "etag", "last-modified")}
                return Response(handle.geturl(), handle.status, data, kept)
        except urllib.error.HTTPError as error:
            return Response(url, error.code, error.read() or b"", {})
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SourcingHttpError(redact_url(url), 0, str(getattr(error, "reason", error))) from None
