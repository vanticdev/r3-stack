"""R3 HTTP API (FastAPI) and the web app host: ``r3 serve``.

Same data and operations as the ``r3`` CLI and the MCP server, over JSON for
the web app in ``apps/web`` (served from ``apps/web/dist``). OpenAPI lives at
``/api/openapi.json``, interactive docs at ``/api/docs``. Every error response
is ``{"code", "message", "data"}``. See ``docs/architecture/api.md``.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from r3_catalog.errors import ModelSelectionRequired
from r3_model.spec_files import SpecError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import api, spec_ops
from . import events as sse
from . import freshness
from . import projects as views
from .agents import AgentManager
from .errors import ApiError, project_load_error, raise_for
from .runs import RunManager
from .sourcing_jobs import SourcingJobs

API_VERSION = "1.0.0"


# ------------------------------------------------------------------ schemas
class _Open(BaseModel):
    """Response object whose documented fields are the stable contract; extra keys may be added."""

    model_config = ConfigDict(extra="allow")


class ErrorBody(BaseModel):
    code: str = Field(description="Stable snake_case error code, e.g. spec_conflict, confirmation_required.")
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class GithubOptions(BaseModel):
    private: bool = True
    description: str = ""


class CreateProjectRequest(BaseModel):
    name: str = Field(description="Directory and robot name: letters, digits, '.', '_' or '-'.")
    template: str = Field("blank", description="An example robot id, or 'blank'.")
    github: GithubOptions | None = Field(None, description="Also create the GitHub repository (needs confirm=true).")
    confirm: bool = Field(False, description="Required with `github`: creating a repository is an external action.")


class SpecPatchRequest(BaseModel):
    patch: dict[str, Any] = Field(description="RFC 7386 JSON merge patch: objects merge, null deletes, lists replace.")
    sha256: str = Field(description="sha256 of all included description files the patch was computed against (from GET .../spec).")
    dry_run: bool = False


class RunRequest(BaseModel):
    stages: list[str] | None = Field(None, description="Stages to run in pipeline order (default: all). The report always runs.")
    scenario: str | None = Field(None, description="Restrict the sim stage to one scenario.")
    scenarios: list[str] | None = Field(None, description="Restrict the sim stage to these scenarios.")


class DeployRequest(BaseModel):
    target: Literal["sim", "fleet"]
    confirm: bool = Field(False, description="fleet: required; deployment changes software on physical robots.")
    scenarios: list[str] | None = Field(None, description="sim: scenarios to run (default: all).")
    force: bool = Field(False, description="fleet: deploy without a clean validation of the current robot.yaml.")
    deploy_args: list[str] | None = Field(None, description="fleet: extra arguments for backend/deploy.py.")


class AgentRequest(BaseModel):
    prompt: str = Field(min_length=1)
    provider: Literal["claude", "codex"] = "claude"
    model: str | None = None
    resume_session_id: str | None = Field(None, pattern=r"^[0-9a-f]{12}$")


class SourcingSearchRequest(BaseModel):
    providers: list[str] | None = Field(None, description="Provider ids (default: the slot's source.providers, else all).")
    offline: bool = Field(False, description="Use only cached responses (no network).")


class SourcingChooseRequest(BaseModel):
    candidate: str | int = Field(description="Rank in the last search (1-based) or a candidate id.")
    allow_rejected: bool = Field(False, description="Pin even if the candidate fails a hard requirement.")


class SourcingJobCreated(_Open):
    job_id: str
    slot: str
    status: str
    events_url: str
    status_url: str


class ProjectSummary(_Open):
    id: str
    name: str
    path: str
    description: str = ""
    version: str = ""
    robot_yaml_sha256: str | None = None
    stages: dict[str, str] = Field(default_factory=dict, description="stage -> ok | failed | unavailable | missing")
    last_run: dict[str, Any] | None = None
    onshape_url: str | None = None
    error: str | None = Field(None, description="Set when robot.yaml does not load: the concise project_invalid message.")
    error_detail: ErrorBody | None = Field(None, description="The full project_invalid error: data.errors (path, message, type), "
                                                             "data.total and data.details.")


class ProjectList(_Open):
    projects: list[ProjectSummary]
    roots: list[str]
    templates: list[str]


class Evidence(_Open):
    stage: str
    check: str
    passed: bool | None
    value: Any = None
    limit: Any = None
    artifact: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    estimated: bool = False
    stale: bool = False
    detail: str = ""


class ReleaseItem(_Open):
    id: str = Field(description="requirement.<name>, scenario.<scenario>.<key> or stage.<name>")
    kind: Literal["requirement", "scenario_expectation", "stage"]
    subject: str
    target: Any
    status: Literal["verified", "estimated", "failed", "stale", "not_run"]
    evidence: list[Evidence]
    message: str = ""


class Release(_Open):
    schema_: str = Field(alias="schema")
    fabrication_ready: bool
    counts: dict[str, int]
    blocking: list[str]
    inputs: dict[str, str]
    items: list[ReleaseItem]


class ProjectDetail(_Open):
    id: str
    project: str
    robot: dict[str, Any]
    fingerprint: dict[str, str]
    git: dict[str, Any]
    lock: dict[str, Any]
    summary: dict[str, Any]
    stages: list[dict[str, Any]]
    results: dict[str, dict[str, Any]] = Field(description="build/<stage>/result.json with status, stale and artifact URLs.")
    report: dict[str, Any] | None = Field(None, description="build/report/summary.json")
    report_url: str | None = None
    release: Release
    links: dict[str, str | None]


class SpecDocument(_Open):
    yaml: str
    sha256: str
    spec: dict[str, Any]
    path: str


class SpecPatchResult(_Open):
    ok: bool
    dry_run: bool
    changed: bool
    previous_sha256: str
    sha256: str
    patched_sha256: str
    preserves_comments: bool
    graph_diagnostics: list[dict[str, Any]]
    yaml: str
    spec: dict[str, Any]


class RunCreated(_Open):
    run_id: str
    status: str
    events_url: str
    status_url: str


class RunStatus(_Open):
    run_id: str
    project: str
    kind: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "error"]
    stages: list[str] | None
    scenarios: list[str] | None
    current_stage: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    events: int
    result: dict[str, Any] | None


class AgentProvider(_Open):
    provider: str
    available: bool
    path: str | None
    version: str | None
    detail: str


class AgentCreated(_Open):
    session_id: str
    status: str
    provider: str
    events_url: str


class AgentStatus(_Open):
    session_id: str
    project: str
    provider: str
    model: str | None
    prompt: str
    status: Literal["starting", "running", "completed", "failed", "cancelled"]
    created_at: float
    finished_at: float | None
    returncode: int | None
    result: str
    error: str | None
    events: int


class DeployResult(_Open):
    ok: bool
    target: Literal["sim", "fleet"]
    run_id: str | None = None


ERRORS = {code: {"model": ErrorBody} for code in (400, 404, 409, 422, 428, 503)}
SSE_RESPONSE = {200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events; see docs/architecture/api.md"}}


# --------------------------------------------------------------------- app
def default_web_dist() -> Path:
    from r3_github.scaffold import PLATFORM_ROOT

    return PLATFORM_ROOT / "apps" / "web" / "dist"


def create_app(projects_roots: list[str | Path] | None = None, web_dist: str | Path | None = None,
               runs: RunManager | None = None, agents: AgentManager | None = None,
               doctor: Any = None, sourcing_jobs: SourcingJobs | None = None, code_guard: freshness.CodeGuard | None = None) -> FastAPI:
    """Build the app. ``doctor`` (a zero-argument callable) replaces ``run_doctor`` in tests.
    ``code_guard`` records the schema code this process loaded (default: now)."""
    registry = views.ProjectRegistry(projects_roots)
    guard = code_guard or freshness.CodeGuard()
    runs = runs or RunManager()
    agents = agents or AgentManager()
    sourcing_jobs = sourcing_jobs or SourcingJobs()
    dist = Path(web_dist) if web_dist else default_web_dist()
    doctor_cache: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        runs.shutdown()
        agents.shutdown()

    app = FastAPI(title="R3 API", version=API_VERSION, openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None,
                  lifespan=lifespan, description="Robot projects, pipeline runs, digital-twin results, release gate and "
                                                 "headless coding agents. Errors are `{code, message, data}`.")
    from .models import router as models_router
    app.include_router(models_router)
    app.state.registry, app.state.runs, app.state.agents, app.state.sourcing = registry, runs, agents, sourcing_jobs
    app.state.code_guard = guard
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])

    # ------------------------------------------------------------ errors
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(error.body(), status_code=error.status)

    @app.exception_handler(ModelSelectionRequired)
    async def _model_selection(_: Request, error: ModelSelectionRequired) -> JSONResponse:
        return JSONResponse({"code": error.code, "message": str(error),
                             "data": {**error.details, "hint": error.hint}}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_: Request, error: RequestValidationError) -> JSONResponse:
        errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]} for e in error.errors()]
        return JSONResponse({"code": "request_invalid", "message": "request body or parameters are invalid", "data": {"errors": errors}},
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(error.status_code, "http_error")
        return JSONResponse({"code": code, "message": str(error.detail), "data": {}}, status_code=error.status_code)

    @app.exception_handler(Exception)
    async def _internal(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"code": "internal_error", "message": f"{type(error).__name__}: {error}", "data": {}}, status_code=500)

    def project(pid: str) -> views.ProjectRef:
        guard.check()  # never answer with a schema older than the code on disk
        return registry.get(pid)

    async def call(function: Any, *args: Any, **kwargs: Any) -> Any:
        """Run blocking work off the event loop and map KeyError/ProjectNotFound to structured errors."""
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except api.ProjectNotFound as error:
            raise ApiError("project_not_found", str(error)) from error
        except KeyError as error:
            raise ApiError("usage_error", str(error).strip("'\"")) from error
        except (ValidationError, yaml.YAMLError, SpecError) as error:  # a project load outside views.load (status, release, ...)
            raise project_load_error(error, "robot.yaml") from error

    # ------------------------------------------------------------ system
    @app.get("/api/doctor", tags=["system"], summary="`r3 doctor`: tools, headless stage readiness, agent CLIs")
    async def get_doctor(refresh: bool = False) -> dict[str, Any]:
        if refresh or "payload" not in doctor_cache:
            from ..doctor import run_doctor

            payload = await run_in_threadpool(doctor or run_doctor)
            doctor_cache["payload"] = {**payload, "checked_at": time.time()}
        providers = await run_in_threadpool(agents.providers, refresh)
        return {**doctor_cache["payload"], "agents": providers}

    @app.get("/api/agent/providers", tags=["agent"], response_model=list[AgentProvider], summary="Claude Code / Codex CLI availability")
    async def get_providers(refresh: bool = False) -> list[dict[str, Any]]:
        return await run_in_threadpool(agents.providers, refresh)

    # ---------------------------------------------------------- projects
    @app.get("/api/projects", tags=["projects"], response_model=ProjectList)
    async def list_projects() -> dict[str, Any]:
        from r3_github.scaffold import available_templates

        guard.check()
        refs = list(registry.scan().values())
        rows = await run_in_threadpool(lambda: [views.summary_entry(ref, runs) for ref in refs])
        return {"projects": rows, "roots": [str(r) for r in registry.roots], "templates": available_templates()}

    @app.post("/api/projects", tags=["projects"], status_code=201, responses=ERRORS,
              summary="Scaffold a project (r3_github); with github+confirm also create the remote")
    async def create_project(body: CreateProjectRequest) -> dict[str, Any]:
        if body.github is not None and not body.confirm:
            raise ApiError("confirmation_required", "creating a GitHub repository is an external action; pass confirm=true",
                           {"github": body.github.model_dump()})
        if (registry.create_root / body.name).exists():
            raise ApiError("project_exists", f"{registry.create_root / body.name} already exists", {"name": body.name})
        payload = await call(api.create_project, body.name, parent=registry.create_root, template=body.template,
                             github=body.github is not None, private=body.github.private if body.github else True,
                             confirm=body.confirm)
        if not payload.get("ok") and isinstance(payload.get("github"), dict):
            # The local scaffold exists; only the remote failed. Report both.
            github = payload["github"]
            raise ApiError("github_failed", f"project scaffolded at {payload['path']} but GitHub creation failed: "
                           f"{github.get('message') or github.get('stderr') or github.get('code')}",
                           {**payload, "id": registry.id_of(Path(payload["path"]))}, status=502)
        raise_for(payload, "create_failed")
        return {**payload, "id": registry.id_of(Path(payload["path"]))}

    @app.get("/api/projects/{pid}", tags=["projects"], response_model=ProjectDetail, responses=ERRORS,
             summary="Spec summary, fingerprint, lock, stage results, report summary and the release gate")
    async def get_project(pid: str) -> dict[str, Any]:
        return await call(views.detail, project(pid))

    @app.get("/api/projects/{pid}/release", tags=["projects"], response_model=Release, responses=ERRORS,
             summary="Release gate alone (`r3 status --release`)")
    async def get_release(pid: str) -> dict[str, Any]:
        return await call(api.release, project(pid).root)

    @app.get("/api/projects/{pid}/spec", tags=["spec"], response_model=SpecDocument, responses=ERRORS)
    async def get_spec(pid: str) -> dict[str, Any]:
        return await call(views.spec_document, project(pid))

    @app.patch("/api/projects/{pid}/spec", tags=["spec"], response_model=SpecPatchResult, responses=ERRORS,
               summary="JSON merge patch with optimistic concurrency (409 spec_conflict on a stale sha256); keeps comments")
    async def patch_spec(pid: str, body: SpecPatchRequest) -> dict[str, Any]:
        ref = project(pid)
        result = raise_for(await call(spec_ops.patch_spec, ref.root, body.patch, body.sha256, dry_run=body.dry_run))
        document = await call(views.spec_document, ref)
        return {**result, "yaml": document["yaml"], "spec": document["spec"]}

    @app.get("/api/projects/{pid}/catalog", tags=["domains"], responses=ERRORS, summary="Catalog components, connectors and wires")
    async def get_catalog(pid: str, kind: str | None = None, query: str | None = None) -> dict[str, Any]:
        return await call(api.list_catalog, project(pid).root, kind, query)

    @app.get("/api/projects/{pid}/connection-points", tags=["domains"], responses=ERRORS,
             summary="Read all mating sites with pin maps, frames, occupancy, blocking and free-access reach")
    async def get_connection_points(pid: str, component: str = "", free_only: bool = False,
                                    fits_type: str = "") -> dict[str, Any]:
        return await call(api.connection_points, project(pid).root, component, free_only, fits_type)

    for name, view, text in (("model", views.model_view, "graph.json, assembly.json, compiled poses and joints"),
                             ("cad", views.cad_view, "cad_model.json, mesh URLs, Onshape URLs and views"),
                             ("wiring", views.wiring_view, "routes, routing detail, BOM, clearances, slack simulation"),
                             ("electrical", views.electrical_view, "operating points, wire/connector currents vs ratings, power tree"),
                             ("pcb", views.pcb_view, "board.json, renders, ERC/DRC, BOM, gerbers"),
                             ("software", views.software_view, "bindings (pin -> net -> device), modules, firmware logs"),
                             ("sim", views.sim_list, "scenarios with their last results")):
        def make(view: Any) -> Any:
            async def endpoint(pid: str) -> dict[str, Any]:
                return await call(view, project(pid))
            return endpoint
        app.add_api_route(f"/api/projects/{{pid}}/{name}", make(view), methods=["GET"], tags=["domains"], summary=text,
                          responses=ERRORS, name=f"get_{name}")

    @app.get("/api/projects/{pid}/sim/{scenario}", tags=["domains"], responses=ERRORS,
             summary="Scenario summary + downsampled recording: series, world body poses per frame, cables, renders")
    async def get_scenario(pid: str, scenario: str, hz: float = Query(30.0, gt=0, le=200),
                           cables: bool = Query(True, description="Include cable polylines per frame.")) -> dict[str, Any]:
        return await call(views.sim_detail, project(pid), scenario, hz, cables)

    @app.get("/api/projects/{pid}/diagnostics", tags=["domains"], responses=ERRORS)
    async def get_diagnostics(pid: str, stage: str | None = None, severity: Literal["error", "warning", "info"] | None = None,
                              subject: str | None = None) -> dict[str, Any]:
        return await call(views.diagnostics_view, project(pid), stage, severity, subject)

    @app.get("/api/projects/{pid}/artifacts/{path:path}", tags=["domains"], responses=ERRORS,
             summary="A file under build/ (path-traversal safe); a directory lists its files")
    async def get_artifact(pid: str, path: str) -> Any:
        ref = project(pid)
        target = api.resolve_artifact(ref.root, path)
        if target is None:
            raise ApiError("artifact_outside_build", f"{path!r} is outside build/", {"path": path})
        if target.is_dir():
            build = (ref.root / "build").resolve()
            return {"path": path, "kind": "directory", "entries": sorted(str(p.relative_to(build)) for p in target.rglob("*") if p.is_file())}
        if not target.is_file():
            raise ApiError("artifact_missing", f"build/{path} does not exist", {"path": path})
        media = {".json": "application/json", ".log": "text/plain", ".cir": "text/plain", ".csv": "text/csv",
                 ".xml": "application/xml", ".stl": "model/stl", ".glb": "model/gltf-binary", ".mp4": "video/mp4",
                 ".html": "text/html", ".npz": "application/octet-stream"}.get(target.suffix.lower())
        return FileResponse(target, media_type=media)

    # -------------------------------------------------------------- runs
    def run_created(run: Any) -> dict[str, Any]:
        return {"run_id": run.id, "status": run.status, "events_url": f"/api/runs/{run.id}/events", "status_url": f"/api/runs/{run.id}"}

    @app.post("/api/projects/{pid}/runs", tags=["runs"], status_code=202, response_model=RunCreated, responses=ERRORS,
              summary="Queue a pipeline run in a worker process (one at a time per project)")
    async def create_run(pid: str, body: RunRequest | None = None) -> dict[str, Any]:
        ref = project(pid)
        body = body or RunRequest()
        scenarios = [*(body.scenarios or []), *([body.scenario] if body.scenario else [])] or None
        try:
            run = runs.submit(ref.id, ref.root, body.stages, scenarios)
        except KeyError as error:
            raise ApiError("usage_error", str(error).strip("'\""), {"stages": body.stages}) from error
        return run_created(run)

    @app.get("/api/projects/{pid}/runs", tags=["runs"], response_model=list[RunStatus], responses=ERRORS)
    async def list_runs(pid: str) -> list[dict[str, Any]]:
        return [r.to_json() for r in runs.list(project(pid).id)]

    def get_run_or_404(rid: str) -> Any:
        run = runs.get(rid)
        if run is None:
            raise ApiError("run_not_found", f"no run {rid!r} (runs are kept in memory by the server)", {"run_id": rid})
        return run

    @app.get("/api/runs/{rid}", tags=["runs"], response_model=RunStatus, responses=ERRORS)
    async def get_run(rid: str) -> dict[str, Any]:
        return get_run_or_404(rid).to_json()

    @app.post("/api/runs/{rid}/cancel", tags=["runs"], response_model=RunStatus, responses=ERRORS)
    async def cancel_run(rid: str) -> dict[str, Any]:
        get_run_or_404(rid)
        return runs.cancel(rid).to_json()

    def event_stream(request: Request, log: sse.EventLog, after: int | None) -> StreamingResponse:
        last = request.headers.get("last-event-id")
        start = after if after is not None else int(last) if last and last.isdigit() else 0
        return StreamingResponse(sse.stream(log, start, request.is_disconnected), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{rid}/events", tags=["runs"], responses={**SSE_RESPONSE, 404: {"model": ErrorBody}},
             summary="SSE: run_queued, run_started, stage_started, log, progress, diagnostic, stage_finished, run_finished")
    async def run_events(request: Request, rid: str, after: int | None = Query(None, ge=0)) -> StreamingResponse:
        return event_stream(request, get_run_or_404(rid).events, after)

    # ------------------------------------------------------------ deploy
    @app.post("/api/projects/{pid}/deploy", tags=["deploy"], response_model=DeployResult, responses=ERRORS,
              summary="sim: queue a sim run. fleet: BLITZ deploy to physical robots, requires confirm=true")
    async def deploy(pid: str, body: DeployRequest) -> dict[str, Any]:
        ref = project(pid)
        if body.target == "sim":
            run = runs.submit(ref.id, ref.root, ["sim"], body.scenarios, kind="deploy_sim")
            return {"ok": True, "target": "sim", **run_created(run)}
        if not body.confirm:
            raise_for(api.deploy_fleet(ref.root, confirm=False))
        busy = runs.active(ref.id)
        if busy is not None:
            raise ApiError("validation_required", f"run {busy.id} is still {busy.status}; wait for it before deploying",
                           {"run_id": busy.id})
        payload = await call(api.deploy_fleet, ref.root, confirm=True, deploy_args=body.deploy_args, force=body.force)
        if not payload.get("ok") and "returncode" not in payload:
            raise_for(payload)
        return payload

    # ------------------------------------------------------------- agent
    @app.post("/api/projects/{pid}/agent", tags=["agent"], status_code=202, response_model=AgentCreated, responses=ERRORS,
              summary="Start a headless Claude Code / Codex session in the project with the R3 MCP server")
    async def start_agent(pid: str, body: AgentRequest) -> dict[str, Any]:
        ref = project(pid)
        session = await run_in_threadpool(agents.start, ref.id, ref.root, body.prompt, body.provider, body.model, body.resume_session_id)
        return {"session_id": session.id, "status": session.status, "provider": session.provider,
                "events_url": f"/api/agent/{session.id}/events", "status_url": f"/api/agent/{session.id}"}

    def get_session_or_404(sid: str) -> Any:
        session = agents.get(sid)
        if session is None:
            for ref in registry.scan().values():
                agents.restore(ref.id, ref.root, sid)
                session = agents.get(sid)
                if session is not None:
                    break
        if session is None:
            raise ApiError("agent_session_not_found", f"no agent session {sid!r}", {"session_id": sid})
        return session

    @app.get("/api/projects/{pid}/agent", tags=["agent"], response_model=list[AgentStatus], responses=ERRORS)
    async def list_agent_sessions(pid: str) -> list[dict[str, Any]]:
        ref = project(pid)
        agents.restore(ref.id, ref.root)
        return [s.to_json() for s in agents.list(ref.id)]

    @app.get("/api/agent/{sid}", tags=["agent"], response_model=AgentStatus, responses=ERRORS)
    async def get_agent(sid: str) -> dict[str, Any]:
        return get_session_or_404(sid).to_json()

    @app.get("/api/agent/{sid}/events", tags=["agent"], responses={**SSE_RESPONSE, 404: {"model": ErrorBody}},
             summary="SSE: session_started, message, tool_call, tool_result, run_started, run_finished, spec_changed, "
                     "usage, stderr, raw, session_finished")
    async def agent_events(request: Request, sid: str, after: int | None = Query(None, ge=0)) -> StreamingResponse:
        return event_stream(request, get_session_or_404(sid).events, after)

    @app.post("/api/agent/{sid}/cancel", tags=["agent"], response_model=AgentStatus, responses=ERRORS)
    async def cancel_agent(sid: str) -> dict[str, Any]:
        get_session_or_404(sid)
        return agents.cancel(sid).to_json()

    # ---------------------------------------------------------- sourcing
    def sourcing_call(function: Any, *args: Any, **kwargs: Any) -> Any:
        from .. import sourcing

        try:
            return function(*args, **kwargs)
        except sourcing.SourcingError as error:
            raise ApiError("sourcing_slot_not_found", str(error).strip("'\""), {}) from error

    @app.get("/api/projects/{pid}/sourcing", tags=["sourcing"], responses=ERRORS,
             summary="`source:` slots: requirements, pinned parts (r3.lock.yaml), last searches, provider availability")
    async def get_sourcing(pid: str) -> dict[str, Any]:
        from .. import sourcing

        ref = project(pid)
        return await call(lambda: sourcing_call(sourcing.list_slots, api.load_project(ref.root)))

    @app.get("/api/projects/{pid}/sourcing/{component}", tags=["sourcing"], responses=ERRORS,
             summary="The last stored search of a slot: every ranked candidate with parameters, provenance, offers and twin checks")
    async def get_sourcing_slot(pid: str, component: str) -> dict[str, Any]:
        from .. import sourcing

        ref = project(pid)

        def load() -> dict[str, Any]:
            loaded = api.load_project(ref.root)
            slot = sourcing.requirement_for(loaded, component).slot
            stored = sourcing.stored_search(loaded, slot)
            if stored is None:
                raise ApiError("sourcing_not_searched", f"no search stored for {slot}; POST .../sourcing/{slot}/search", {"slot": slot})
            stored["stale"] = stored["requirement_sha256"] != sourcing.requirement_for(loaded, slot).digest()
            stored["pinned"] = (loaded.lock.get("sourcing") or {}).get(slot)
            return stored

        return await call(lambda: sourcing_call(load))

    @app.post("/api/projects/{pid}/sourcing/{component}/search", tags=["sourcing"], status_code=202, response_model=SourcingJobCreated,
              responses=ERRORS, summary="Start a search job (SSE: provider_*, candidate, search_finished, job_finished)")
    async def search_sourcing(pid: str, component: str, body: SourcingSearchRequest | None = None) -> dict[str, Any]:
        ref = project(pid)
        body = body or SourcingSearchRequest()
        job = await run_in_threadpool(sourcing_jobs.start, ref.id, ref.root, component, body.providers, body.offline)
        return {"job_id": job.id, "slot": job.slot, "status": job.status, "events_url": f"/api/sourcing/jobs/{job.id}/events",
                "status_url": f"/api/sourcing/jobs/{job.id}"}

    def get_job_or_404(jid: str) -> Any:
        job = sourcing_jobs.get(jid)
        if job is None:
            raise ApiError("sourcing_job_not_found", f"no sourcing job {jid!r}", {"job_id": jid})
        return job

    @app.get("/api/sourcing/jobs/{jid}", tags=["sourcing"], responses=ERRORS)
    async def get_sourcing_job(jid: str) -> dict[str, Any]:
        return get_job_or_404(jid).to_json()

    @app.get("/api/sourcing/jobs/{jid}/events", tags=["sourcing"], responses={**SSE_RESPONSE, 404: {"model": ErrorBody}},
             summary="SSE: job_started, search_started, provider_*, ranking_started, candidate, search_finished, job_finished")
    async def sourcing_events(request: Request, jid: str, after: int | None = Query(None, ge=0)) -> StreamingResponse:
        return event_stream(request, get_job_or_404(jid).events, after)

    @app.post("/api/projects/{pid}/sourcing/{component}/choose", tags=["sourcing"], responses=ERRORS,
              summary="Pin a candidate of the last search: writes catalog/components/sourced_<id>.yaml and r3.lock.yaml")
    async def choose_sourcing(pid: str, component: str, body: SourcingChooseRequest) -> dict[str, Any]:
        from .. import sourcing

        ref = project(pid)
        if any(j.status == "running" and j.project_id == ref.id for j in sourcing_jobs.list(ref.id)):
            raise ApiError("sourcing_busy", "a search is still running for this project; wait for it before choosing", {})
        payload = await call(lambda: sourcing_call(sourcing.choose, ref.root, component, body.candidate, allow_rejected=body.allow_rejected))
        return raise_for(payload)

    @app.post("/api/projects/{pid}/sourcing/{component}/unpin", tags=["sourcing"], responses=ERRORS,
              summary="Remove a slot's pin (the authored `type`, if any, is used again)")
    async def unpin_sourcing(pid: str, component: str) -> dict[str, Any]:
        from .. import sourcing

        ref = project(pid)
        return await call(lambda: sourcing_call(sourcing.unpin, ref.root, component))

    # ---------------------------------------------------------- web app
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"], include_in_schema=False)
    async def api_not_found(rest: str) -> None:
        raise ApiError("not_found", f"no API endpoint /api/{rest}")

    @app.get("/{path:path}", include_in_schema=False)
    async def web(path: str) -> Any:
        root = dist.resolve()
        target = (root / path).resolve()
        if path and root in target.parents and target.is_file():
            return FileResponse(target)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(f"<!doctype html><title>R3</title><h1>R3 API is running</h1><p>The web app is not built: run "
                            f"<code>npm run build</code> in <code>apps/web</code> (expected {index}). API docs: "
                            f"<a href='/api/docs'>/api/docs</a>.</p>")

    return app


# ------------------------------------------------------------------ serving
#: Folders of the platform source watched by ``r3 serve --reload`` (Python files only).
RELOAD_FOLDERS = ("packages", "services", "integrations")
#: Import string of the app factory the reloading worker process calls.
APP_FACTORY = "r3_orchestrator.api.server:app_from_environment"


def source_checkout() -> Path | None:
    """The repository root when this package runs from a source checkout (``pyproject.toml``
    plus ``.git`` or the platform folders next to it), else None (an installed package)."""
    root = Path(__file__).resolve().parents[4]
    if (root / "pyproject.toml").is_file() and ((root / ".git").exists() or (root / "packages").is_dir()):
        return root
    return None


def dev_mode() -> bool:
    """``r3 serve`` reloads by default in dev: ``R3_DEV=1``, or running from a source checkout (``R3_DEV=0`` turns it off)."""
    flag = os.environ.get("R3_DEV", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return source_checkout() is not None


def default_reload_dirs() -> list[str]:
    root = source_checkout()
    if root is None:
        return []
    return [str(root / folder) for folder in RELOAD_FOLDERS if (root / folder).is_dir()]


def _env_roots() -> list[str] | None:
    return [p for p in os.environ.get("R3_PROJECTS_ROOTS", "").split(os.pathsep) if p] or None


def app_from_environment() -> FastAPI:
    """App factory for the reloading worker: ``R3_PROJECTS_ROOTS`` (os.pathsep separated) and ``R3_WEB_DIST``."""
    return create_app(_env_roots(), os.environ.get("R3_WEB_DIST") or None)


def serve(host: str = "127.0.0.1", port: int = 8321, projects_roots: list[str] | None = None, web_dist: str | None = None,
          reload: bool = False, reload_dirs: list[str] | None = None) -> None:
    """Run the API with uvicorn. ``reload`` (dev) restarts the worker whenever a Python file under
    ``reload_dirs`` (default: the checkout's packages/, services/ and integrations/) changes; the
    worker rebuilds the app with ``app_from_environment``, so the options travel as environment variables."""
    import uvicorn

    roots = projects_roots or _env_roots()
    if not reload:
        app = create_app(roots, web_dist)
        print(f"R3 API on http://{host}:{port}  (projects: {', '.join(map(str, app.state.registry.roots))})", flush=True)
        uvicorn.run(app, host=host, port=port, log_level="info")
        return
    if roots:
        os.environ["R3_PROJECTS_ROOTS"] = os.pathsep.join(str(Path(r).expanduser().resolve()) for r in roots)
    if web_dist:
        os.environ["R3_WEB_DIST"] = str(Path(web_dist).expanduser().resolve())
    dirs = reload_dirs if reload_dirs is not None else default_reload_dirs()
    print(f"R3 API on http://{host}:{port}  (dev: reloading on Python changes under {', '.join(dirs) or 'the working directory'}; "
          "--no-reload turns it off)", flush=True)
    uvicorn.run(APP_FACTORY, factory=True, host=host, port=port, log_level="info", reload=True, reload_dirs=dirs or None,
                reload_includes=["*.py"])
