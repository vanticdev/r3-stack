"""HTTP errors. Every error response body is ``{"code", "message", "data"}``.

``code`` reuses the stable codes of the CLI/MCP payloads (``spec_conflict``,
``confirmation_required``, ...), so a client handles the same failure the same
way whichever interface produced it.
"""

from __future__ import annotations

import traceback
from pathlib import Path
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
    "agent_resume_unavailable": 409,
    "agent_process_unavailable": 409,
    "validation_required": 409,
    "spec_invalid": 422,
    "spec_read_only": 422,
    "patch_invalid": 422,
    "request_invalid": 422,
    "usage_error": 422,
    "catalog_model_selection_required": 422,
    "create_failed": 422,
    "project_invalid": 422,
    "confirmation_required": 428,
    "agent_cli_missing": 503,
    "api_unavailable": 503,
    "server_code_stale": 503,
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


#: Validation errors listed in ``data.errors`` of a ``project_invalid`` error; the rest are counted.
MAX_LISTED_ERRORS = 5


def _first_line(text: str, limit: int = 300) -> str:
    line = next((part.strip() for part in str(text).splitlines() if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "\u2026"


def project_load_error(error: Exception, spec_path: str | Path, data: dict[str, Any] | None = None) -> ApiError:
    """A readable ``project_invalid`` error for a project that does not load.

    ``message`` is one short sentence; ``data.errors`` lists up to
    ``MAX_LISTED_ERRORS`` problems as ``{path, message, type}`` (pydantic field
    paths joined with ``.``, e.g. ``components.gps.type``), ``data.total`` counts
    them all and ``data.details`` keeps the full text (pydantic dump or traceback).
    """
    from pydantic import ValidationError
    import yaml

    from r3_model.spec_files import SpecError

    source_error = error if isinstance(error, SpecError) else None
    if source_error is not None and isinstance(error.__cause__, (ValidationError, yaml.YAMLError)):
        error = error.__cause__
    name = Path(spec_path).name
    errors: list[dict[str, str]]
    if isinstance(error, ValidationError):
        raw = error.errors(include_url=False)
        errors = [{"path": ".".join(str(part) for part in e.get("loc", ())), "message": str(e.get("msg", "")),
                   "type": str(e.get("type", ""))} for e in raw]
        total = error.error_count()
        first = errors[0] if errors else {"path": "", "message": ""}
        where = f"{first['path']}: " if first["path"] else ""
        message = (f"{name} is invalid: {total} validation error{'s' if total != 1 else ''} in {error.title}; "
                   f"first: {where}{first['message']}")
        details = str(error)
    elif isinstance(error, yaml.YAMLError):
        mark = getattr(error, "problem_mark", None)
        problem = getattr(error, "problem", None) or _first_line(str(error))
        at = f"line {mark.line + 1}, column {mark.column + 1}: " if mark is not None else ""
        errors = [{"path": "", "message": f"{at}{problem}", "type": "yaml_syntax"}]
        total = 1
        message = f"{name} is not valid YAML: {at}{problem}"
        details = str(error)
    else:
        errors = [{"path": "", "message": _first_line(str(error)) or type(error).__name__, "type": type(error).__name__}]
        total = 1
        message = f"{name} does not load: {type(error).__name__}: {errors[0]['message']}"
        details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if source_error is not None and any(e["file"] != "robot.yaml" for e in source_error.errors):
        for index, entry in enumerate(errors):
            source = source_error.errors[min(index, len(source_error.errors) - 1)]
            entry.update({"file": source["file"], "code": source["code"], "source_path": source.get("source_path", source["path"])})
            if source["loc"]:
                entry["path"] = source["loc"]
    return ApiError("project_invalid", message, {**(data or {}), "exception": type(error).__name__, "file": str(spec_path),
                                                 "errors": errors[:MAX_LISTED_ERRORS], "total": total, "details": details})
