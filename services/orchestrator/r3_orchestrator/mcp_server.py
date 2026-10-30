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
New robot: r3_guide (read it first), r3_create_project(template="blank"), then grow the spec component by component.
Purchased CAD: r3_models_search first; on a miss find a public model and r3_models_add it with provenance.
Parts: r3_list_catalog / r3_get_catalog_component; your own printed parts with r3_put_catalog_component.
Loop: r3_get_spec -> r3_patch_spec (pass the sha256 you read; on spec_conflict re-read and re-apply)
-> r3_validate -> r3_get_diagnostics / r3_diagnose -> fix -> repeat. Parts declared by requirements
(`source:` in robot.yaml) are found with r3_source_search and pinned with r3_source_choose. Diagnostics carry stable codes,
subjects (component/wire/pin ids) and numeric data. deploy_fleet and create_github_repo need confirm=true
and must only be called when the human explicitly asked for them."""

mcp = _Server("r3", instructions=INSTRUCTIONS)

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_DESIGN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
_GATED = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)
# Reads public vendor data and official APIs over the network; writes only build/sourcing/.
_SEARCH = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

#: Permission tier of every registered tool: ``read``, ``design``, ``edit`` or
#: ``gated``. Headless agent sessions (``r3 serve``) pre-approve every tier but
#: ``gated``; gated tools additionally refuse to act without ``confirm=true``.
TOOL_TIERS: dict[str, str] = {}
_TIER_OF = {id(_READ): "read", id(_DESIGN): "design", id(_EDIT): "edit", id(_GATED): "gated", id(_SEARCH): "design"}


def _tool(annotations: ToolAnnotations) -> Any:
    def register(function: Any) -> Any:
        TOOL_TIERS[function.__name__] = _TIER_OF[id(annotations)]
        return mcp.tool(annotations=annotations)(function)
    return register


def gated_tools() -> list[str]:
    return sorted(name for name, tier in TOOL_TIERS.items() if tier == "gated")


def ungated_tools() -> list[str]:
    return sorted(name for name, tier in TOOL_TIERS.items() if tier != "gated")


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


@_tool(_READ)
def r3_status(project: str = "") -> dict[str, Any]:
    """Project identity, robot.yaml revision, git state, pinned revisions and the status of every stage."""
    return _guard(lambda: api.status(project or None))


@_tool(_DESIGN)
def r3_validate(project: str = "", stages: list[str] | None = None, scenarios: list[str] | None = None) -> dict[str, Any]:
    """Run pipeline stages in order (default all: assembly, model, cad, fabrication, pcb, wiring, connectors, electrical,
    sim) and regenerate the report.
    Returns per-stage status and every diagnostic."""
    return _guard(lambda: api.validate(project or None, stages or None, scenarios=scenarios or None))


@_tool(_DESIGN)
def r3_run_stage(stage: str, project: str = "", scenarios: list[str] | None = None) -> dict[str, Any]:
    """Run a single stage: assembly, model, cad, fabrication, pcb, wiring, connectors, electrical, sim or report. Returns
    its diagnostics, metrics and assumptions. A stage with nothing to check yet reports `not_configured` (info, with a
    hint saying what to add); `not_applicable` means the robot has nothing for it (e.g. no custom PCB)."""
    return _guard(lambda: api.run_stage(project or None, stage, scenarios=scenarios or None))


@_tool(_READ)
def r3_connection_points(project: str = "", component: str = "", free_only: bool = False,
                         fits_type: str = "") -> dict[str, Any]:
    """All wireable mating sites, including unused placements. Robot-frame origins, cable exit directions,
    pin/signal/net maps, occupancy and blocking, compatible housing types, and conservative free-access reach.
    Filters combine: component id, unoccupied/unblocked sites only, and a compatible connector type.
    Recomputed from current inputs; does not build or write the project."""
    return _guard(lambda: api.connection_points(project or None, component, free_only, fits_type))


@_tool(_READ)
def r3_get_diagnostics(project: str = "", stage: str = "", severity: str = "", subject: str = "") -> dict[str, Any]:
    """Stored diagnostics from the last run, filtered by stage, severity (error|warning|info) and/or subject id."""
    return _guard(lambda: api.stored_diagnostics(project or None, stage or None, severity or None, subject or None))


@_tool(_READ)
def r3_diagnose(project: str = "", errors_only: bool = False) -> dict[str, Any]:
    """Errors and warnings grouped by subject (component, wire, binding) with remediation hints."""
    return _guard(lambda: api.diagnose(project or None, include_warnings=not errors_only))


@_tool(_READ)
def r3_get_spec(project: str = "", section: str = "") -> dict[str, Any]:
    """Read robot.yaml (optionally one dotted section such as `software.bindings`). Returns `sha256`,
    which r3_patch_spec requires as `expected_sha256`."""
    return _guard(lambda: spec_ops.get_spec(api.resolve_project(project or None), section or None))


@_tool(_EDIT)
def r3_patch_spec(patch: dict[str, Any], expected_sha256: str, project: str = "", dry_run: bool = False) -> dict[str, Any]:
    """Apply an RFC 7386 JSON merge patch to robot.yaml. Objects merge, null deletes, lists replace.
    Rejected with `spec_conflict` if robot.yaml no longer has `expected_sha256` (someone else edited it),
    or `spec_invalid` (with errors) if the result fails the r3/robot@1 schema. Comments are preserved."""
    return _guard(lambda: spec_ops.patch_spec(api.resolve_project(project or None), patch, expected_sha256, dry_run=dry_run))


@_tool(_READ)
def r3_list_catalog(project: str = "", kind: str = "", query: str = "") -> dict[str, Any]:
    """Catalog components (filter by kind: computer, battery, motor, esc, servo, regulator, sensor, camera, cable, radio,
    wheel, gearbox, structure, ...; or `connector` / `wire` / `material` / `printer`), with their interfaces (names and
    kinds, for assembly joins), connectors, pin roles and behaviour model. `query` matches id, name and part number.
    Full entries: r3_get_catalog_component."""
    return _guard(lambda: api.list_catalog(project or None, kind or None, query or None))


@_tool(_READ)
def r3_get_catalog_component(component_id: str, project: str = "") -> dict[str, Any]:
    """One catalog entry in full: mechanical envelope and solids, interfaces (for assembly joins), pins, connectors,
    behaviour model parameters and provenance, plus whether it comes from the platform or the project catalog."""
    return _guard(lambda: api.get_catalog_component(project or None, component_id))


@_tool(_EDIT)
def r3_put_catalog_component(entry: dict[str, Any], project: str = "", dry_run: bool = False) -> dict[str, Any]:
    """Add or replace a component type in the project's catalog (catalog/components/<id>.yaml), validated against the
    catalog schema. Use it for the robot's own 3D-printed parts (mechanical.fabricated: true, material e.g. PETG,
    solids + interfaces with hole patterns that match the parts they join) and for bought parts missing from the
    platform catalog (with provenance). Returns `catalog_entry_invalid` with the schema errors otherwise."""
    return _guard(lambda: api.put_catalog_component(project or None, entry, dry_run=dry_run))


@_tool(_READ)
def r3_guide() -> dict[str, Any]:
    """How to build a robot from nothing with R3 (docs/guide/build-a-robot.md): the spec, sourcing, joins and
    interfaces, CAD (standard features only), wiring, electrical, firmware with r3_hal, sim scenarios, the release
    gate, the policies and every tool. Read it before starting a new robot."""
    return api.guide()


@_tool(_DESIGN)
def r3_run_scenario(scenario: str, project: str = "") -> dict[str, Any]:
    """Run one digital-twin scenario (ids from r3_status summary.scenarios) and return its diagnostics."""
    return _guard(lambda: api.deploy_sim(project or None, [scenario]))


@_tool(_READ)
def r3_get_artifact(path: str, project: str = "") -> dict[str, Any]:
    """Read a file under build/ (e.g. `model/graph.json`, `report/summary.json`, `model/layout.png`).
    JSON is parsed, text returned, images base64-encoded. A directory path lists its files."""
    return _guard(lambda: api.get_artifact(project or None, path))


@_tool(_READ)
def r3_release_status(project: str = "") -> dict[str, Any]:
    """Release gate: for every requirement, scenario expectation and stage, its status (verified, estimated,
    failed, stale, not_run) with evidence (stage, check, value, limit, artifact, input fingerprint).
    `fabrication_ready` is true only when everything is verified from current inputs."""
    return _guard(lambda: api.release(project or None))


@_tool(_READ)
def r3_doctor(require: list[str] | None = None) -> dict[str, Any]:
    """Environment check: versions of MuJoCo (and its working offscreen GL backend), libngspice, kicad-cli,
    KiCad IPC, Freerouting, Java, Docker, the Onshape transport and Sionna RT, plus which stages can run
    headless here. With `require` (stage names), ok=false if any of them cannot."""
    from .doctor import run_doctor

    return run_doctor(require or None)


def _sourcing(call: Any) -> dict[str, Any]:
    """``_guard``: an unknown slot (SourcingError is a KeyError) becomes a ``usage_error`` result."""
    return _guard(call)


@_tool(_READ)
def r3_source_slots(project: str = "") -> dict[str, Any]:
    """Components declared by requirements (`source:` in robot.yaml): requirement, pinned part (r3.lock.yaml
    sourcing.<slot>), last search summary, and which sourcing providers are available here (API keys, agent CLI)."""
    from . import sourcing

    return _sourcing(lambda: sourcing.list_slots(api.load_project(project or None)))


@_tool(_SEARCH)
def r3_source_search(component: str, project: str = "", providers: list[str] | None = None, top: int = 10,
                     offline: bool = False) -> dict[str, Any]:
    """Search online for real parts that meet a slot's `source.requires` (component id or source.group), rank them
    (hard filters, then twin checks: pin voltages/currents against the robot's nets, envelope interference, assembly
    interfaces, mass budget; plus price, stock, lead time and a penalty for estimated parameters) and store the full
    result in build/sourcing/<slot>/candidates.json. Every parameter carries a provenance URL and status
    (datasheet|vendor|estimated). Does not change the project; pin a result with r3_source_choose."""
    from . import sourcing

    def run() -> dict[str, Any]:
        project_obj = api.load_project(project or None)
        from r3_catalog.sourcing import HttpClient

        payload = sourcing.search_slot(project_obj, component, provider_ids=providers or None,
                                       http=HttpClient(offline=True) if offline else None)
        return sourcing.trimmed(payload, top)

    return _sourcing(run)


@_tool(_EDIT)
def r3_source_choose(component: str, candidate: str, project: str = "", allow_rejected: bool = False) -> dict[str, Any]:
    """Pin a candidate from the last r3_source_search of this slot: `candidate` is its rank ("1") or candidate id.
    Writes catalog/components/sourced_<id>.yaml (provenance per field) and r3.lock.yaml sourcing.<slot>, then
    re-checks the slot. Run r3_validate afterwards. Choosing again later is how a slot is re-sourced."""
    from . import sourcing

    return _sourcing(lambda: sourcing.choose(api.resolve_project(project or None), component, candidate, allow_rejected=allow_rejected))


@_tool(_GATED)
def deploy_fleet(confirm: bool = False, project: str = "", deploy_args: list[str] | None = None, force: bool = False) -> dict[str, Any]:
    """Deploy robot software to PHYSICAL robots via BLITZ backend/deploy.py. Requires confirm=true, which
    must reflect an explicit human request. Refuses unless the current robot.yaml validated without errors
    (force=true overrides)."""
    return _guard(lambda: api.deploy_fleet(project or None, confirm=confirm, deploy_args=deploy_args, force=force))


@_tool(_EDIT)
def r3_create_project(name: str, parent: str = ".", template: str = "blank") -> dict[str, Any]:
    """Scaffold a new local R3 project directory (robot.yaml, catalog/, cad/, backend/, agent docs). Local only: it never
    creates a GitHub repository. template="blank" starts from nothing (grow it with r3_patch_spec); an example robot
    id (e.g. "rc-car") copies that robot. Then pass project=<path> to the other tools (or set R3_PROJECT)."""
    return api.create_project(name, parent=parent, template=template)


@_tool(_GATED)
def create_github_repo(name: str, directory: str, private: bool = True, confirm: bool = False) -> dict[str, Any]:
    """Create a GitHub repository with `gh repo create` from an existing scaffolded directory and push it.
    External, account-visible action: requires confirm=true reflecting an explicit human request."""
    if not confirm:
        return {"ok": False, "code": "confirmation_required",
                "message": "creating a GitHub repository is an external action; call again with confirm=true"}
    from pathlib import Path

    from r3_github import scaffold

    return scaffold.create_remote(name, Path(directory).expanduser().resolve(), private=private)


@_tool(_READ)
def r3_models_search(query: str = "", category: str | None = None, manufacturer: str | None = None,
                     specs: dict[str, list[float | None]] | None = None, limit: int = 30) -> dict[str, Any]:
    """Search the bank FIRST for bought parts; spec ranges use SI. On a miss, find a public model and add it."""
    from r3_catalog.modelbank import service
    return service.guard(lambda: service.search(query, category, manufacturer, specs, limit))


@_tool(_SEARCH)
def r3_models_add(name: str, category: str, mpn: str = "", manufacturer: str = "", url: str = "", onshape: str = "",
                  model_id: str = "", license: str = "unspecified; verify redistribution terms", format: str = "step",
                  member: str = "", catalog_component: str | None = None) -> dict[str, Any]:
    """Download/convert a public model, pin its checksum and add a human-editable provenance record.
    Onshape sources need a public immutable /v/ URL. Unknown geometry remains unverified."""
    from r3_catalog.modelbank import service
    return service.guard(lambda: service.add(name, category, mpn, manufacturer, url, onshape, model_id, license, format, member, catalog_component))


@_tool(_SEARCH)
def r3_models_fetch(model_id: str) -> dict[str, Any]:
    """Fetch a checksum-pinned bank model and convert to GLB/STL for viewing and simulation."""
    from r3_catalog.modelbank import service
    return service.guard(lambda: service.fetch_model(model_id))


def main() -> None:
    configure_headless()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
