"""R3 MCP server (stdio): the ``r3`` operations as typed tools for Codex and Claude Code.

Run with ``python -m r3_orchestrator.mcp_server`` (or ``r3-mcp``). The project
directory comes from each tool's ``project`` argument, else ``$R3_PROJECT``,
else the server's working directory.

Permission tiers:

* design and simulation tools (status, validate, stages, spec read/patch,
  catalog, scenarios, artifacts) are free to call;
* ``deploy_fleet`` (physical hardware) and ``create_github_repo`` (external
  account) refuse to act unless called with ``confirm=true``.

Tool functions are plain Python callables; the decorator only registers them,
so tests and other Python code can call them directly.
"""

from __future__ import annotations

from typing import Any

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from mcp.types import ToolAnnotations

from . import api, spec_ops
from .cli import configure_headless

INSTRUCTIONS = """R3 robot project tools. robot.yaml is the single source of truth.
Loop: r3_get_spec -> r3_patch_spec (pass the sha256 you read; on spec_conflict re-read and re-apply)
-> r3_validate -> r3_get_diagnostics / r3_diagnose -> fix -> repeat. Diagnostics carry stable codes,
subjects (component/wire/pin ids) and numeric data. deploy_fleet and create_github_repo need confirm=true
and must only be called when the human explicitly asked for them."""

mcp = _Server("r3", instructions=INSTRUCTIONS)

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_DESIGN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
_GATED = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)


def _guard(call: Any) -> dict[str, Any]:
    """Turn expected failures into structured results instead of protocol errors."""
    try:
        return call()
    except api.ProjectNotFound as error:
        return {"ok": False, "code": "project_not_found", "message": str(error)}
    except KeyError as error:
        return {"ok": False, "code": "usage_error", "message": str(error).strip("'\"")}
    except FileNotFoundError as error:
        return {"ok": False, "code": "file_not_found", "message": str(error)}


@mcp.tool(annotations=_READ)
def r3_status(project: str = "") -> dict[str, Any]:
    """Project identity, robot.yaml revision, git state, pinned revisions and the status of every stage."""
    return _guard(lambda: api.status(project or None))


@mcp.tool(annotations=_DESIGN)
def r3_validate(project: str = "", stages: list[str] | None = None, scenarios: list[str] | None = None) -> dict[str, Any]:
    """Run pipeline stages in order (default all: model, cad, pcb, wiring, electrical, sim) and regenerate the report.
    Returns per-stage status and every diagnostic."""
    return _guard(lambda: api.validate(project or None, stages or None, scenarios=scenarios or None))


@mcp.tool(annotations=_DESIGN)
def r3_run_stage(stage: str, project: str = "", scenarios: list[str] | None = None) -> dict[str, Any]:
    """Run a single stage: model, cad, pcb, wiring, electrical, sim or report. Returns its diagnostics,
    metrics and assumptions."""
    return _guard(lambda: api.run_stage(project or None, stage, scenarios=scenarios or None))


@mcp.tool(annotations=_READ)
def r3_get_diagnostics(project: str = "", stage: str = "", severity: str = "", subject: str = "") -> dict[str, Any]:
    """Stored diagnostics from the last run, filtered by stage, severity (error|warning|info) and/or subject id."""
    return _guard(lambda: api.stored_diagnostics(project or None, stage or None, severity or None, subject or None))


@mcp.tool(annotations=_READ)
def r3_diagnose(project: str = "", errors_only: bool = False) -> dict[str, Any]:
    """Errors and warnings grouped by subject (component, wire, binding) with remediation hints."""
    return _guard(lambda: api.diagnose(project or None, include_warnings=not errors_only))


@mcp.tool(annotations=_READ)
def r3_get_spec(project: str = "", section: str = "") -> dict[str, Any]:
    """Read robot.yaml (optionally one dotted section such as `software.bindings`). Returns `sha256`,
    which r3_patch_spec requires as `expected_sha256`."""
    return _guard(lambda: spec_ops.get_spec(api.resolve_project(project or None), section or None))


@mcp.tool(annotations=_EDIT)
def r3_patch_spec(patch: dict[str, Any], expected_sha256: str, project: str = "", dry_run: bool = False) -> dict[str, Any]:
    """Apply an RFC 7386 JSON merge patch to robot.yaml. Objects merge, null deletes, lists replace.
    Rejected with `spec_conflict` if robot.yaml no longer has `expected_sha256` (someone else edited it),
    or `spec_invalid` (with errors) if the result fails the r3/robot@1 schema. Comments are preserved."""
    return _guard(lambda: spec_ops.patch_spec(api.resolve_project(project or None), patch, expected_sha256, dry_run=dry_run))


@mcp.tool(annotations=_READ)
def r3_list_catalog(project: str = "", kind: str = "", query: str = "") -> dict[str, Any]:
    """Catalog components (filter by kind, e.g. motor, sensor, or `connector` / `wire`), connectors and wire gauges."""
    return _guard(lambda: api.list_catalog(project or None, kind or None, query or None))


@mcp.tool(annotations=_DESIGN)
def r3_run_scenario(scenario: str, project: str = "") -> dict[str, Any]:
    """Run one digital-twin scenario (ids from r3_status summary.scenarios) and return its diagnostics."""
    return _guard(lambda: api.deploy_sim(project or None, [scenario]))


@mcp.tool(annotations=_READ)
def r3_get_artifact(path: str, project: str = "") -> dict[str, Any]:
    """Read a file under build/ (e.g. `model/graph.json`, `report/summary.json`, `model/layout.png`).
    JSON is parsed, text returned, images base64-encoded. A directory path lists its files."""
    return _guard(lambda: api.get_artifact(project or None, path))


@mcp.tool(annotations=_READ)
def r3_doctor(require: list[str] | None = None) -> dict[str, Any]:
    """Environment check: versions of MuJoCo (and its working offscreen GL backend), libngspice, kicad-cli,
    KiCad IPC, Freerouting, Java, Docker, the Onshape transport and Sionna RT, plus which stages can run
    headless here. With `require` (stage names), ok=false if any of them cannot."""
    from .doctor import run_doctor

    return run_doctor(require or None)


@mcp.tool(annotations=_GATED)
def deploy_fleet(confirm: bool = False, project: str = "", deploy_args: list[str] | None = None, force: bool = False) -> dict[str, Any]:
    """Deploy robot software to PHYSICAL robots via BLITZ backend/deploy.py. Requires confirm=true, which
    must reflect an explicit human request. Refuses unless the current robot.yaml validated without errors
    (force=true overrides)."""
    return _guard(lambda: api.deploy_fleet(project or None, confirm=confirm, deploy_args=deploy_args, force=force))


@mcp.tool(annotations=_EDIT)
def r3_create_project(name: str, parent: str = ".", template: str = "rc-car") -> dict[str, Any]:
    """Scaffold a new local R3 project directory (robot.yaml, cad/, pcb/, backend/, CI workflow, agent docs)."""
    return api.create_project(name, parent=parent, template=template)


@mcp.tool(annotations=_GATED)
def create_github_repo(name: str, directory: str, private: bool = True, confirm: bool = False) -> dict[str, Any]:
    """Create a GitHub repository with `gh repo create` from an existing scaffolded directory and push it.
    External, account-visible action: requires confirm=true reflecting an explicit human request."""
    if not confirm:
        return {"ok": False, "code": "confirmation_required",
                "message": "creating a GitHub repository is an external action; call again with confirm=true"}
    from pathlib import Path

    from r3_github import scaffold

    return scaffold.create_remote(name, Path(directory).expanduser().resolve(), private=private)


def main() -> None:
    configure_headless()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
