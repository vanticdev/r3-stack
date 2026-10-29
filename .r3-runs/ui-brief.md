# R3 web app + API: fully integrated UI (user request: "fully integrate everything with the UI")

"Lovable for robotics": one web app where a human, or an AI agent (Claude Code or Codex
through their own CLIs/subscriptions), describes a robot and runs the pipeline, and then
sees, edits and diagnoses every domain in one place. Same data, same operations as the
CLI and MCP server. GitHub remains the source of truth.

## Backend: `services/orchestrator/r3_orchestrator/api/` (agent A)
FastAPI + uvicorn (install into the shared venv; add them to the `pyproject` `api` extra
and to the worker image requirements). It must run headless in the worker image.
`r3 serve [--host --port --projects-root]` serves the API and the built web app (static
files from `apps/web/dist`).

Endpoints (JSON; every error carries `{code, message, data}`):
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/doctor` | the `r3 doctor` payload |
| GET | `/api/projects` | projects under the roots (default `examples/robots/*`), with name, path, last run status |
| POST | `/api/projects` | `{name, template}` → scaffold (r3_github); `{github: {private}, confirm: true}` also creates the remote |
| GET | `/api/projects/{pid}` | spec summary, fingerprint, lock, stage results (`build/*/result.json`), report summary, the requirement evidence matrix (below) |
| GET / PATCH | `/api/projects/{pid}/spec` | GET: `{yaml, sha256, spec}`. PATCH: `{patch (JSON merge), sha256}` → schema-validated, 409 `spec_conflict` on a stale sha (reuse the MCP `r3_patch_spec` logic and keep comments) |
| GET | `/api/projects/{pid}/catalog` | catalog components, connectors, wires |
| GET | `/api/projects/{pid}/model` | `build/model/graph.json` + `assembly.json` + compiled poses + joints |
| GET | `/api/projects/{pid}/cad` | `cad_model.json` + mesh URLs + Onshape URLs + views |
| GET | `/api/projects/{pid}/wiring` | `routes.json` + routing detail + BOM + clearances + slack-sim timeline |
| GET | `/api/projects/{pid}/electrical` | operating points + per-wire/connector currents vs ratings + power tree |
| GET | `/api/projects/{pid}/pcb` | `board.json` + render URLs + ERC/DRC + BOM + gerber zip URL |
| GET | `/api/projects/{pid}/software` | bindings (pin → net → device), firmware modules, last firmware logs |
| GET | `/api/projects/{pid}/sim` | scenario list with last results |
| GET | `/api/projects/{pid}/sim/{scenario}` | summary + downsampled recording as JSON (`?hz=30`): time series and per-frame `qpos` per body (world poses) for 3D playback + render/clip URLs |
| GET | `/api/projects/{pid}/diagnostics` | all diagnostics from every stage, with stage, severity, subjects and data |
| GET | `/api/projects/{pid}/artifacts/{path}` | files under `build/` (path-traversal safe) |
| POST | `/api/projects/{pid}/runs` | `{stages?, scenario?}` → `{run_id}`; jobs run in a worker process, one at a time per project |
| GET | `/api/runs/{rid}` | status |
| GET | `/api/runs/{rid}/events` | SSE stream: `stage_started`, `log`, `progress`, `diagnostic`, `stage_finished`, `run_finished` |
| POST | `/api/projects/{pid}/deploy` | `{target: sim\|fleet, confirm}`; fleet requires `confirm: true`, as the CLI does |
| POST | `/api/projects/{pid}/agent` | `{prompt, provider: claude\|codex, model?}` → `{session_id}`. Runs `claude -p --output-format stream-json` or `codex exec --json` headlessly in the project dir, with the R3 MCP server configured (`.mcp.json`) so the agent edits through R3's tools. Detect CLI availability and report clearly when one is missing. |
| GET | `/api/agent/{sid}/events` | SSE stream of the agent transcript (messages, tool calls, results), plus the runs it triggers |
| POST | `/api/agent/{sid}/cancel` | cancel the session |

**Release gate / requirement evidence matrix** (review item: "verified / estimated /
failed / not_run"). For every requirement in `robot.yaml` and every scenario expectation,
compute its status from the latest results, together with the evidence: stage, check,
value, artifact and input fingerprint. Evidence produced from stale inputs (fingerprint ≠
current) is `stale`. `fabrication_ready` = all verified and none estimated/stale. Put this
in `r3_orchestrator/release.py`, shared by the CLI (`r3 status --release`), MCP and the API.

Tests: FastAPI TestClient covering every endpoint, against a copy of the rc-car fixture.
The SSE run stream is tested with a fake stage.

## Frontend: `apps/web/` (agent B)
Vite + React + TypeScript + three.js (`@react-three/fiber`, `@react-three/drei`), plus a
charting library (uPlot or Recharts). Build with `npm run build` → `apps/web/dist`. Dev
server proxies `/api` to `r3 serve`. Node 24 is in the worker image; locally use the
installed node.

Pages:
- **Projects:** list, plus create from template.
- **Project workspace:**
  - **Overview:** the requirement evidence matrix (release gate), stage status chips,
    Run pipeline, Onshape/GitHub links, doctor warnings.
  - **3D:** robot from the CAD STL meshes (fallback: catalog envelopes) posed from the
    compiled assembly. Interfaces and joins are toggleable overlays, with harness routes
    as tubes colored by wire color. A joint slider (steering, wheel spin) uses the derived
    joints. Clicking a part shows its catalog data, mass source and diagnostics. Exploded
    view along the joins.
  - **Wiring:** harness list, BOM, and per-wire current vs ampacity, clearance and bend
    radius. In the 3D view, the selected harness route can be edited by dragging route
    waypoints/tie points; saving PATCHes `robot.yaml` route hints (with the sha) and
    re-runs the wiring stage. Slack/vibration playback from the cable timeline.
  - **Electrical:** power tree diagram; operating-point table (idle, cruise, full
    throttle, stall); bars of current vs rating for wires and connectors; rail voltages;
    runtime.
  - **PCB:** top/bottom renders, ERC/DRC results, connector pin→net map vs catalog, BOM,
    gerber download.
  - **Software:** binding map (software handle → Pi pin → net → device pin), firmware
    modules, a firmware log viewer.
  - **Simulation:** scenario cards with pass/fail and run buttons. A synchronized
    timeline: 3D playback of the vehicle from recorded body poses (with cables when
    present), alongside speed, battery V/I/SOC, rails, RSSI/LQ, motor current, steering and
    wire clearance charts, a scrubber, the MP4 clip and renders.
  - **Diagnostics:** grouped by stage, severity and subject, with hints. Clicking a
    diagnostic focuses its subjects in 3D (highlight).
  - **Agent:** a chat panel. Pick Claude or Codex, type "make the battery lead shorter" or
    "design a robot that…", and watch the streamed transcript, tool calls, triggered runs
    and the diff of `robot.yaml` live. Pages refresh when runs finish.
  - **Deploy:** sim/fleet with a confirmation dialog.
  - **Settings:** doctor.
- **Live updates:** subscribe to SSE run and agent events, and invalidate queries when
  they finish.
- **Quality bar:** dark and light themes, clean engineering look, fast loading, no layout
  jank. A typed API client generated from, or matching, the backend's OpenAPI.
- **Tests:** vitest unit tests for data transforms, plus a Playwright (headless Chromium)
  smoke test: start `r3 serve` on the rc-car fixture, load every page, assert no console
  errors, and save screenshots to `build/report/ui/*.png` as review evidence.
