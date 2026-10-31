"""Dedicated native CAD transport: editor/local calls and UI exports only.

Geometry exports use native UI routes. Other API operations use only the
authenticated loopback browser-session bridge, never API keys.
Writes are submitted exactly once. Unknown outcomes require inspection.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
import urllib.error
import urllib.request

from r3_model.pulled import PullError

from .client import NativeBridgeTransport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NativeRateLimitError(PullError):
    """Preserve server pacing without retrying or guessing a reset time."""

    def __init__(self, retry_after: str | None, method: str = "", resource: str = "") -> None:
        details: dict[str, Any] = {"http_status": 429, "retry_after": retry_after,
                                   "method": method, "resource": resource}
        now = datetime.now(timezone.utc)
        try:
            if retry_after is not None:
                if re.fullmatch(r"[0-9]+", retry_after):
                    seconds = int(retry_after)
                else:
                    date = parsedate_to_datetime(retry_after)
                    date = date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
                    seconds = max(0, math.ceil((date - now).total_seconds()))
                details.update(retry_after_seconds=seconds, resume_at=(now + timedelta(seconds=seconds)).isoformat())
        except (ValueError, TypeError, OverflowError):
            pass  # Keep the original header; do not invent a fallback deadline.
        super().__init__("cad_native_rate_limited",
                         f"Onshape {method} {resource}: HTTP 429; Retry-After: {retry_after or 'not supplied'}. "
                         "Do not retry this resource before the reported reset; continue independent offline work.", details)


class NativeCadTransport:
    """Authenticated loopback client; unsupported operations fail closed."""

    def __init__(self, runtime: Path | None = None) -> None:
        bridge = NativeBridgeTransport(runtime)
        self.base, self.runtime = bridge.base, bridge.runtime
        self._headers = bridge._headers()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        if urlsplit(self.base).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise PullError("cad_native_unavailable", "onshape-native must use a loopback companion")

    def _request(self, method: str, path: str, body: Any = None, query: dict | None = None,
                 timeout: float = 120) -> bytes:
        if not (path.startswith("/native/api/v17/") or re.match(r"^/api/v[0-9]+/", path) or path.startswith("/local/") or path == "/command"):
            raise PullError("cad_native_route_forbidden", "Only loopback browser API, native exports, local and editor routes are supported")
        if ".." in path or "?" in path or "#" in path:
            raise PullError("cad_native_route_forbidden", "Invalid native route")
        if path.startswith("/api/"):
            from .policy import check_request
            check_request(method, path, body)
            if (re.search(r"/(stl|gltf|parasolid|externaldata|foreigndata|translate|exportinternal)(/|$)", path)
                    or ("/translations" in path and any(part in path for part in ("/partstudios/", "/assemblies/")))):
                raise PullError("cad_native_route_forbidden", "Geometry exports require /native/api/v17")
        url = self.base + path + ("?" + urlencode(query) if query else "")
        raw = json.dumps(body, allow_nan=False).encode() if body is not None else None
        headers = {**self._headers, "Content-Type": "application/json"}
        for _ in range(4):
            request = urllib.request.Request(url, data=raw, method=method, headers=headers)
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                # UI export redirects point to this companion's externaldata
                # route. Credentials must never follow an arbitrary redirect.
                if error.code == 307 and method == "GET":
                    redirect = urlsplit(error.headers.get("Location", ""))
                    if redirect.netloc and redirect.netloc != urlsplit(self.base).netloc:
                        raise PullError("cad_native_route_forbidden", "Refusing export redirect outside companion") from None
                    if not redirect.path.startswith("/native/api/v17/documents/") or "/externaldata/" not in redirect.path:
                        raise PullError("cad_native_route_forbidden", "Refusing non-native export redirect") from None
                    url = self.base + redirect.path
                    continue
                if error.code == 429:
                    raise NativeRateLimitError(error.headers.get("Retry-After"), method, path) from None
                detail = error.read().decode(errors="replace")[:600]
                hint = "; restart the updated onshape-native companion to expose /native routes" if error.code == 404 else ""
                raise PullError("cad_native_request_failed", f"Native {method} {path}: HTTP {error.code}: {detail}{hint}") from None
            except (OSError, TimeoutError) as error:
                raise PullError("cad_native_unavailable", f"Native bridge unavailable or command outcome unknown: {error}; inspect before retrying writes") from None
        raise PullError("cad_native_request_failed", "Too many native export redirects")

    def api_request(self, method: str, endpoint: str, body: Any = None, query: dict | None = None) -> Any:
        """One browser-session API operation; geometry exports stay native-only."""
        from .policy import check_request
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not re.match(r"^/api/v[0-9]+/", endpoint):
            raise PullError("cad_native_route_forbidden", "Use a versioned browser-session API endpoint")
        if (re.search(r"/(stl|gltf|parasolid|externaldata|foreigndata|translate|exportinternal)(/|$)", endpoint)
                or ("/translations" in endpoint and "/partstudios/" in endpoint)
                or ("/translations" in endpoint and "/assemblies/" in endpoint)):
            raise PullError("cad_native_route_forbidden", "Geometry exports require /native/api/v17")
        check_request(method, endpoint, body)
        raw = self._request(method, endpoint, body=body, query=query)
        return json.loads(raw) if raw else None

    def local(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {"resolve_target", "search_commands", "browse_commands", "document_tree", "element_tree",
                   "document_history", "document_edit", "sidebar_edit", "display_state", "set_visibility",
                   "mate_animation", "view_control", "capture_viewport", "assembly_transform", "native_measure",
                   "assembly_feature", "open_document", "editor_tab", "artifact_page"}
        if tool not in allowed:
            raise PullError("cad_native_route_forbidden", f"Unsupported local operation {tool!r}")
        from .policy import check_request
        check_request("POST", "/local/" + tool, arguments)
        if str(arguments.get("element_type", "")).lower() == "featurestudio":
            raise PullError("cad_native_route_forbidden", "Feature Studios are disabled")
        result = json.loads(self._request("POST", "/local/" + tool, arguments, timeout=300))
        if result.get("error"):
            raise PullError("cad_native_request_failed", str(result["error"]))
        return result

    def command(self, job: dict[str, Any]) -> dict[str, Any]:
        allowed = {"state", "schema", "native", "open", "editor_tab", "tabs", "ui_inspect", "ui_action", "display"}
        if job.get("kind") not in allowed:
            raise PullError("cad_native_route_forbidden", "Only native editor commands are allowed; REST proxy and FeatureScript are disabled")
        text = json.dumps(job).lower()
        if any(value in text for value in ("featurescript", "featurestudio", "evalfeature")):
            raise PullError("cad_native_route_forbidden", "FeatureScript is disabled")
        result = json.loads(self._request("POST", "/command", job, timeout=300))
        if result.get("error"):
            raise PullError("cad_native_request_failed", str(result["error"]))
        return result

    def artifact(self, handle: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{64}\.json", handle):
            raise PullError("cad_native_invalid", "Invalid native snapshot handle")
        path = self.runtime / "artifacts" / handle
        if not path.is_file():
            raise PullError("cad_native_invalid", "Native snapshot is unavailable on this worker")
        return json.loads(path.read_text())

    def assembly(self, url: str) -> dict[str, Any]:
        from .rest_snapshot import assembly
        return assembly(self, url)

    def stl(self, did: str, vid: str, eid: str, pid: str, configuration: str = "") -> bytes:
        path = f"/native/api/v17/parts/d/{quote(did, safe='')}/v/{quote(vid, safe='')}/e/{quote(eid, safe='')}/partid/{quote(pid, safe='')}/stl"
        query = {"mode": "binary", "units": "METER", "chordTolerance": 5e-5, "angleTolerance": 0.03490658503988659}
        if configuration:
            query["configuration"] = configuration
        return self._request("GET", path, query=query)

    def step(self, did: str, vid: str, eid: str, pid: str, configuration: str = "") -> bytes:
        base = f"/native/api/v17/partstudios/d/{quote(did, safe='')}/v/{quote(vid, safe='')}/e/{quote(eid, safe='')}"
        body = {"formatName": "STEP", "storeInDocument": False, "partIds": pid}
        if configuration:
            body["configuration"] = configuration
        job = json.loads(self._request("POST", base + "/translations", body))
        deadline = time.monotonic() + 1800
        while job.get("requestState") not in ("DONE", "FAILED"):
            if time.monotonic() > deadline:
                raise PullError("cad_pull_translation_pending", f"STEP translation {job.get('id')} has unknown outcome; poll it, do not resubmit")
            time.sleep(5)
            job = json.loads(self._request("GET", "/native/api/v17/translations/" + quote(job["id"], safe="")))
        if job["requestState"] == "FAILED":
            raise PullError("cad_pull_translation_failed", f"STEP export failed: {job.get('failureReason')}")
        files = job.get("resultExternalDataIds") or []
        if len(files) != 1 or not job.get("resultDocumentId"):
            raise PullError("cad_pull_translation_failed", "Expected one selected-part STEP file; refusing an ambiguous export")
        path = f"/native/api/v17/documents/d/{quote(job['resultDocumentId'], safe='')}/externaldata/{quote(files[0], safe='')}"
        return self._request("GET", path)

    def capture(self, url: str) -> tuple[bytes, dict[str, Any]]:
        from .rest_snapshot import capture
        return capture(self, url)
