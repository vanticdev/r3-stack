"""Dedicated native CAD transport: editor/local calls and UI exports only.

Geometry exports use native UI routes. Other API operations use only the
authenticated loopback browser-session bridge, never API keys.
Writes are submitted exactly once. Unknown outcomes require inspection.
"""
from __future__ import annotations

import json
import hashlib
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
from r3_model.spec import CadMeshExport

from .client import NativeBridgeTransport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NativeRateLimitError(PullError):
    """Preserve server pacing without retrying or guessing a reset time."""

    def __init__(self, retry_after: str | None, method: str = "", resource: str = "", *,
                 response_excerpt: str = "", remaining: str | None = None) -> None:
        details: dict[str, Any] = {"http_status": 429, "retry_after": retry_after,
                                   "method": method, "resource": resource}
        if response_excerpt:
            details["response_excerpt"] = response_excerpt[:600]
        if remaining is not None:
            details["rate_limit_remaining"] = remaining
        now = datetime.now(timezone.utc)
        details["observed_at"] = now.isoformat()
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


    @classmethod
    def from_evidence(cls, message: str, details: dict[str, Any]) -> "NativeRateLimitError":
        """Restore the original deadline; never rebase Retry-After on a later call."""
        error = cls.__new__(cls)
        PullError.__init__(error, "cad_native_rate_limited", message, dict(details))
        return error


class NativeCadTransport:
    """Authenticated loopback client; unsupported operations fail closed."""

    def __init__(self, runtime: Path | None = None, *, export_jobs: Path | None = None,
                 mesh_export: CadMeshExport | None = None) -> None:
        self._mesh_export = CadMeshExport.model_validate(mesh_export or {})
        bridge = NativeBridgeTransport(runtime)
        self.base, self.runtime = bridge.base, bridge.runtime
        self._headers = bridge._headers()
        self.export_jobs = export_jobs
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        if urlsplit(self.base).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise PullError("cad_native_unavailable", "onshape-native must use a loopback companion")

    def check_backoff(self, method: str, path: str) -> None:
        from .backoff import active, operator_active
        configured = operator_active(method, path)
        if configured is not None:
            until = configured['resume_at'] or 'an operator-confirmed reset (none reported)'
            raise PullError("cad_native_backoff_active",
                f"Known transport hold for {method} {configured['hold_resource']} until {until}; no request sent", configured)
        evidence = active(getattr(self, "export_jobs", None), method, path)
        if evidence is not None:
            raise NativeRateLimitError.from_evidence(evidence["message"], evidence["details"])

    def _request(self, method: str, path: str, body: Any = None, query: dict | None = None,
                 timeout: float = 120) -> bytes:
        cached_artifact = method == "GET" and re.fullmatch(r"/artifacts/[a-f0-9]{64}\.json", path)
        if not (cached_artifact or path.startswith("/native/api/v17/") or re.match(r"^/api/v[0-9]+/", path) or path.startswith("/local/") or path == "/command"):
            raise PullError("cad_native_route_forbidden", "Only loopback browser API, native exports, local and editor routes are supported")
        if ".." in path or "?" in path or "#" in path:
            raise PullError("cad_native_route_forbidden", "Invalid native route")
        if path.startswith("/api/"):
            from .policy import check_request
            check_request(method, path, body)
            if (re.search(r"/(stl|gltf|parasolid|externaldata|foreigndata|translate|exportinternal)(/|$)", path)
                    or ("/translations" in path and any(part in path for part in ("/partstudios/", "/assemblies/")))):
                raise PullError("cad_native_route_forbidden", "Geometry exports require /native/api/v17")
        self.check_backoff(method, path)
        url = self.base + path + ("?" + urlencode(query) if query else "")
        raw = json.dumps(body, allow_nan=False).encode() if body is not None else None
        headers = {**self._headers, "Content-Type": "application/json"}
        for _ in range(4):
            request_resource = urlsplit(url).path
            self.check_backoff(method, request_resource)
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
                    # Keep bounded upstream evidence so callers can distinguish
                    # the server's explanation from our computed resume time.
                    limited = NativeRateLimitError(error.headers.get("Retry-After"), method, request_resource,
                        response_excerpt=error.read(601).decode(errors="replace"),
                        remaining=error.headers.get("X-RateLimit-Remaining"))
                    from .backoff import remember
                    try:
                        remember(getattr(self, "export_jobs", None), method, request_resource, str(limited), limited.details)
                    except (OSError, PullError) as storage_error:
                        limited.details["backoff_persistence_error"] = str(storage_error)
                    raise limited from None
                raw_error = error.read().decode(errors="replace")
                detail = raw_error[:600]
                details: dict[str, Any] = {"http_status": error.code, "method": method, "resource": path}
                authentication_required = False
                try:
                    payload = json.loads(raw_error)
                    if isinstance(payload, dict):
                        if (error.code == 401 and payload.get("status") == 401
                                and payload.get("message") == "Unauthenticated API request"
                                and path.startswith(("/api/", "/native/api/v17/"))):
                            authentication_required = True
                        if isinstance(payload.get("translationId"), str):
                            details["translation_id"] = payload["translationId"]
                        if isinstance(payload.get("id"), str) and "requestState" in payload:
                            details["translation"] = payload
                except ValueError:
                    pass
                if authentication_required:
                    raise PullError("cad_native_authentication_required",
                        f"Native {method} {path}: HTTP 401: {detail}; "
                        "Onshape browser authentication is required. A connected extension does not "
                        "prove a signed-in session; sign in to Onshape in the connected browser. "
                        "Inspect persisted state before retrying any write.", details) from None
                # A missing upstream object is not evidence of a stale bridge.
                # Only the companion's plain router 404 suggests a missing route;
                # leave any restart decision to a health/capability inspection.
                missing_route = (error.code == 404 and path.startswith("/native/api/v17/")
                                 and raw_error.strip() == "Not Found")
                hint = "; inspect companion /health capabilities: this /native route may be unavailable" if missing_route else ""
                raise PullError("cad_native_request_failed", f"Native {method} {path}: HTTP {error.code}: {detail}{hint}", details) from None
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
                   "assembly_feature", "open_document", "editor_tab", "artifact_page", "api_catalog"}
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

    def artifact(self, handle: str) -> Any:
        """Read a complete cached response through the authenticated companion.

        An exact content-addressed handle grants no directory listing or access
        to the companion filesystem. This makes bulk inspection available from
        independent project sessions without repeating an Onshape request.
        """
        if not re.fullmatch(r"[a-f0-9]{64}\.json", handle):
            raise PullError("cad_native_invalid", "Invalid native snapshot handle")
        raw = self._request("GET", "/artifacts/" + handle)
        if hashlib.sha256(raw).hexdigest() != handle[:-5]:
            raise PullError("cad_native_invalid", "Native snapshot checksum mismatch")
        return json.loads(raw)

    def assembly(self, url: str) -> dict[str, Any]:
        from .rest_snapshot import assembly
        return assembly(self, url)

    @property
    def mesh_export(self) -> CadMeshExport:
        return getattr(self, "_mesh_export", None) or CadMeshExport()

    def stl(self, did: str, vid: str, eid: str, pid: str, configuration: str = "") -> bytes:
        path = f"/native/api/v17/parts/d/{quote(did, safe='')}/v/{quote(vid, safe='')}/e/{quote(eid, safe='')}/partid/{quote(pid, safe='')}/stl"
        settings = self.mesh_export
        query = {"mode": "binary", "units": "METER", "chordTolerance": settings.chord_tolerance,
                 "angleTolerance": settings.angle_tolerance}
        if settings.minimum_facet_width is not None:
            query["minFacetWidth"] = settings.minimum_facet_width
        if configuration:
            query["configuration"] = configuration
        return self._request("GET", path, query=query)

    def step(self, did: str, vid: str, eid: str, pid: str, configuration: str = "") -> bytes:
        from .translation import translated_file
        base = f"/native/api/v17/partstudios/d/{quote(did, safe='')}/v/{quote(vid, safe='')}/e/{quote(eid, safe='')}"
        body = {"formatName": "STEP", "storeInDocument": False, "partIds": pid}
        if configuration:
            body["configuration"] = configuration
        return translated_file(self, base + "/translations", body)

    def gltf(self, did: str, vid: str, eid: str, pid: str, configuration: str = "") -> bytes:
        from .translation import translated_file
        path = f"/native/api/v17/partstudios/d/{quote(did, safe='')}/v/{quote(vid, safe='')}/e/{quote(eid, safe='')}/translations"
        # The original-body-ID option collapses composite Body groups in glTF.
        # Keep native default grouping; never reconstruct that ownership locally.
        settings = self.mesh_export
        # Preserve the existing default request/cache identity (10, not 10.0).
        chord_length = settings.maximum_chord_length
        chord_length = int(chord_length) if chord_length.is_integer() else chord_length
        body = {"formatName": "GLTF", "storeInDocument": False, "partIds": pid,
                "resolution": "custom", "distanceTolerance": settings.chord_tolerance,
                "angularTolerance": settings.angle_tolerance,
                "maximumChordLength": chord_length}
        if configuration:
            body["configuration"] = configuration
        return translated_file(self, path, body)

    def capture(self, url: str) -> tuple[bytes, dict[str, Any]]:
        from .rest_snapshot import capture
        return capture(self, url)
