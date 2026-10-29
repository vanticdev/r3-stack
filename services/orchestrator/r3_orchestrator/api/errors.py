"""HTTP errors. Every error response body is ``{"code", "message", "data"}``.

``code`` reuses the stable codes of the CLI/MCP payloads (``spec_conflict``,
``confirmation_required``, ...), so a client handles the same failure the same
way whichever interface produced it.
"""

from __future__ import annotations

from typing import Any

#: HTTP status per error code; anything else is 400.
STATUS_BY_CODE: dict[str, int] = {
    "not_found": 404,
    "project_not_found": 404,
    "artifact_missing": 404,
    "scenario_not_found": 404,
    "run_not_found": 404,
    "agent_session_not_found": 404,
    "spec_section_missing": 404,
    "sourcing_slot_not_found": 404,
    "sourcing_not_searched": 404,
    "sourcing_job_not_found": 404,
    "sourcing_busy": 409,
    "sourcing_search_stale": 409,
    "sourcing_candidate_rejected": 422,
    "artifact_outside_build": 403,
    "spec_conflict": 409,
    "project_exists": 409,
    "agent_busy": 409,
    "validation_required": 409,
    "spec_invalid": 422,
    "patch_invalid": 422,
    "request_invalid": 422,
    "usage_error": 422,
    "create_failed": 422,
    "project_invalid": 422,
    "confirmation_required": 428,
    "agent_cli_missing": 503,
    "api_unavailable": 503,
    "internal_error": 500,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}
        self.status = status or STATUS_BY_CODE.get(code, 400)

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "data": self.data}


def raise_for(payload: dict[str, Any], default_code: str = "operation_failed") -> dict[str, Any]:
    """Return a successful CLI/MCP payload unchanged; raise its structured failure as an ApiError."""
    if payload.get("ok", True):
        return payload
    code = str(payload.get("code") or default_code)
    data = {k: v for k, v in payload.items() if k not in ("ok", "code", "message")}
    raise ApiError(code, str(payload.get("message") or code.replace("_", " ")), data)
