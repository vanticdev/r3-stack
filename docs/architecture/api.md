# R3 HTTP API (`r3 serve`)

The web app (`apps/web`) and any other HTTP client get the same data and operations
as the `r3` CLI and the MCP server. The implementation lives in
`services/orchestrator/r3_orchestrator/api/`:

| Module | Role |
| --- | --- |
| `__init__.py` | Operations shared by the CLI, MCP and HTTP (`validate`, `status`, `release`, `deploy_fleet`, ...) |
| `server.py` | FastAPI app (`create_app`) and `serve()` |
| `projects.py` | Project discovery and per-domain read models |
| `runs.py` | Pipeline runs in worker processes |
| `agents.py` | Headless Claude Code / Codex sessions |
| `events.py` | SSE event logs |
| `errors.py` | `{code, message, data}` errors |

The release gate is `r3_orchestrator/release.py`.

```bash
pip install 'r3-platform[api]'        # fastapi, uvicorn, mcp, ruamel.yaml
r3 serve [--host 127.0.0.1] [--port 8321] [--projects-root DIR ...] [--web-dist apps/web/dist]
```

- `--projects-root` (repeatable; also `$R3_PROJECTS_ROOTS`, `:`-separated) holds the
  projects. A root is either a project itself (it contains `robot.yaml`) or a directory
  whose children are projects. The default root is `examples/robots` (`$R3_EXAMPLES_DIR`).
- A project's id (`pid`) is its directory name. On a collision between roots, `-2`,
  `-3`, ... is appended.
- The server needs no display. Runs, renders and agent CLIs all run headless, as in the
  R3 worker image.
- The web app is served from `apps/web/dist`. Unknown non-`/api` paths return
  `index.html` for SPA routing. If the app is not built, `/` shows a hint page.
- The Vite dev server proxies `/api` to `r3 serve`. CORS also allows
  `http://localhost:*` and `http://127.0.0.1:*`.
- **OpenAPI:** `GET /api/openapi.json`, with interactive docs at `/api/docs`. Its
  request and response schemas (e.g. `ProjectDetail`, `ReleaseItem`, `RunStatus`,
  `AgentStatus`, `ErrorBody`) are the typed contract. Response objects may gain
  fields; documented fields do not change meaning.

## Conventions

- JSON everywhere. Units are SI (m, kg, s, A, V, rad), as in `robot.yaml` after parsing
  and in every stage hand-off file.
- **Errors** always have the body `{"code": str, "message": str, "data": {...}}`.
  `code` reuses the CLI/MCP codes:

| HTTP | `code` |
| --- | --- |
| 403 | `artifact_outside_build` |
| 404 | `not_found`, `project_not_found`, `artifact_missing`, `scenario_not_found`, `run_not_found`, `agent_session_not_found` |
| 409 | `spec_conflict`, `project_exists`, `agent_busy`, `validation_required` |
| 422 | `request_invalid` (`data.errors[]` with `loc`, `msg`), `spec_invalid` (`data.errors[]`), `patch_invalid`, `usage_error`, `create_failed`, `project_invalid` |
| 428 | `confirmation_required` |
| 500 | `internal_error` |
| 502 | `github_failed` (the scaffold exists; `data` holds it) |
| 503 | `agent_cli_missing` (`data.providers`) |

- **Artifact URLs:** files under a project's `build/` are referenced as
  `/api/projects/{pid}/artifacts/<path relative to build/>`. Views return these URLs
  (`url`, `*_url`, `meshes`, `images[]`, `plots[]`, `renders[]`). The `path` fields
  stay relative to `build/`.
- **Missing data:** a view never fails because a stage has not run. Absent inputs are
  `null`, and their names are listed in `missing`.
- **Staleness:** a result is `stale` when the inputs it was produced from differ from
  the current ones. The check compares the `robot.yaml` digest and pinned `*_revision`
  values. The git commit is ignored.
- **Floats:** JSON cannot carry NaN or infinity, so those become `null`.
- **Compression:** responses over 2 kB are gzip-compressed when the client accepts it.
  SSE is never compressed.

## Endpoints

| Method | Path | Body / query | Response |
| --- | --- | --- | --- |
| GET | `/api/doctor` | `?refresh=true` | The `r3 doctor` payload (`environment`, `tools`, `stages`), plus `checked_at` and `agents` (CLI detection, see below). Cached until refreshed, because the probes take seconds. |
| GET | `/api/agent/providers` | `?refresh=true` | `[{provider, available, path, version, detail}]` for `claude` and `codex` |
| GET | `/api/projects` | | `{projects: [ProjectSummary], roots, templates}` |
| POST | `/api/projects` | `{name, template="rc-car", github?: {private=true, description}, confirm=false}` | 201 `{ok, id, path, files[], github?}`. `github` without `confirm: true` returns 428. |
| GET | `/api/projects/{pid}` | | `ProjectDetail` |
| GET | `/api/projects/{pid}/release` | | `Release` (the gate alone) |
| GET | `/api/projects/{pid}/spec` | | `{yaml, sha256, spec, path}` |
| PATCH | `/api/projects/{pid}/spec` | `{patch, sha256, dry_run=false}` | `{ok, changed, previous_sha256, sha256, patched_sha256, preserves_comments, graph_diagnostics[], yaml, spec}`. 409 `spec_conflict` (`data.current_sha256`), 422 `spec_invalid` |
| GET | `/api/projects/{pid}/catalog` | `?kind=&query=` | `{components[], connectors[], wires[]}` |
| GET | `/api/projects/{pid}/model` | | see [Domain views](#domain-views) |
| GET | `/api/projects/{pid}/cad` | | 〃 |
| GET | `/api/projects/{pid}/wiring` | | 〃 |
| GET | `/api/projects/{pid}/electrical` | | 〃 |
| GET | `/api/projects/{pid}/pcb` | | 〃 |
| GET | `/api/projects/{pid}/software` | | 〃 |
| GET | `/api/projects/{pid}/sim` | | 〃 |
| GET | `/api/projects/{pid}/sim/{scenario}` | `?hz=30` (0–200), `?cables=true` | 〃 |
| GET | `/api/projects/{pid}/diagnostics` | `?stage=&severity=error\|warning\|info&subject=` | `{diagnostics[], counts, by_stage: {stage: {error, warning, info}}, stages[], inputs}`. Each diagnostic has `code, severity, message, subjects[], data{}, stage, hint`. |
| GET | `/api/projects/{pid}/artifacts/{path}` | | The raw file (correct media type). A directory returns `{kind: "directory", entries[]}`. Paths that escape `build/` (`..`, symlinks) return 403. |
| POST | `/api/projects/{pid}/runs` | `{stages?: [...], scenario?: str, scenarios?: [...]}` | 202 `{run_id, status, events_url, status_url}` |
| GET | `/api/projects/{pid}/runs` | | `[RunStatus]` (this server's runs, oldest first) |
| GET | `/api/runs/{rid}` | | `RunStatus` |
| POST | `/api/runs/{rid}/cancel` | | `RunStatus` (cancels a queued run, or terminates the worker process) |
| GET | `/api/runs/{rid}/events` | `?after=<seq>` or the `Last-Event-ID` header | SSE, see [Run events](#run-events) |
| POST | `/api/projects/{pid}/deploy` | `{target: "sim"\|"fleet", confirm=false, scenarios?, force=false, deploy_args?}` | `sim`: `{ok, target, run_id, events_url, status_url}` (a queued sim run). `fleet`: the `r3 deploy --target fleet` payload `{ok, command, returncode, log, stdout_tail, stderr_tail}`. 428 without `confirm`; 409 `validation_required` without a clean validation (unless `force`) or while a run is active. |
| POST | `/api/projects/{pid}/agent` | `{prompt, provider: "claude"\|"codex", model?}` | 202 `{session_id, status, provider, events_url, status_url}`. 503 `agent_cli_missing`; 409 `agent_busy` (one session per project at a time) |
| GET | `/api/projects/{pid}/agent` | | `[AgentStatus]` |
| GET | `/api/agent/{sid}` | | `AgentStatus` |
| GET | `/api/agent/{sid}/events` | `?after=` / `Last-Event-ID` | SSE, see [Agent events](#agent-events) |
| POST | `/api/agent/{sid}/cancel` | | `AgentStatus` (SIGTERM to the CLI's process group; SIGKILL after 5 s) |

Beyond the brief, the API adds:
- `GET /api/projects/{pid}/release`
- `GET /api/agent/providers`
- `GET /api/projects/{pid}/runs`
- `POST /api/runs/{rid}/cancel`
- `GET /api/projects/{pid}/agent`
- `GET /api/agent/{sid}`

### `ProjectSummary`

Fields:
- `id`, `name` (`robot.name`), `path`, `description`, `version`
- `robot_yaml_sha256` (first 16 hex digits)
- `stages`: `{stage: ok|failed|unavailable|missing}`
- `last_run`: `{status: ok|failed, finished_at, failed_stages[], stale, counts, run_id?, run_status?}` or `null`
- `onshape_url`
- `error`: set when `robot.yaml` does not load; the project is still listed.

### `ProjectDetail`

The `r3 status` payload:
- `project`, `robot`, `inputs`, `git` (`branch`, `commit`, `dirty_files`), `lock`
- `summary` (counts and scenario ids)
- `stages[]`: per stage `stage, status, ok, stale, counts, duration_s, started_at, metrics, artifacts`

On top of that:
- `id`
- `fingerprint`: the current input fingerprint
- `results`: `{stage: build/<stage>/result.json}`, each with added `status` and `stale`,
  hints on its diagnostics and `url` on its artifacts
- `report`: `build/report/summary.json` or `null`
- `report_url`: the HTML report
- `release`: see the [release gate](#release-gate-requirement-evidence-matrix)
- `links: {onshape, github}`

## Release gate (requirement evidence matrix)

`r3_orchestrator/release.py` backs `r3 status --release`, the MCP tool
`r3_release_status` and `release` in `GET /api/projects/{pid}`:

```json
{"schema": "r3/release@1", "fabrication_ready": false, "inputs": {"robot_yaml_sha256": "..."},
 "counts": {"verified": 10, "estimated": 3, "failed": 9, "stale": 0, "not_run": 6},
 "blocking": ["requirement.max_mass", "..."],
 "items": [{"id": "requirement.top_speed", "kind": "requirement", "subject": "top_speed", "target": 8.0,
            "status": "verified", "message": "100 ms average, at t=5.31s",
            "evidence": [{"stage": "sim", "check": "full_throttle.min_top_speed_mps", "passed": true, "value": 8.002,
                          "limit": 8.0, "artifact": "sim/full_throttle/summary.json",
                          "inputs": {"robot_yaml_sha256": "..."}, "estimated": false, "stale": false, "detail": "..."}]}]}
```

The gate covers three kinds of item:
- Every `requirements:` field: `requirement.<name>`. Custom `extra` requirements have no
  checker and stay `not_run`.
- Every scenario `expect` key: `scenario.<id>.<key>`.
- Every pipeline stage: `stage.<name>`. Its target is "no errors"; it is `estimated`
  when the stage reports a `*_estimated` diagnostic.

The status of an item, in precedence order:
1. `failed`: any fresh evidence fails.
2. `stale`: some evidence comes from old inputs.
3. `not_run`: there is no evidence (or the stage is unavailable).
4. `estimated`: the evidence passes but rests on estimates (catalog-envelope masses,
   fallback solvers, estimated geometry or parameters).
5. `verified`: otherwise.

`fabrication_ready` requires every item to be `verified`.

| Requirement | Evidence |
| --- | --- |
| `max_mass` | model `total_mass` (`model/graph.json`). Estimated if `mass.estimated_fraction > 0`. |
| `top_speed` | the scenario check `min_top_speed_mps`, else the best `top_speed_mps` |
| `min_runtime` | electrical `runtime`. Estimated if the vehicle mass or battery parameters are estimated. |
| `radio_range` | the scenario check `link_lost_beyond_m` |
| `failsafe_stop_distance` | the scenario checks `stop_within_m_of_link_loss` and `stop_within_m_of_fault` whose expected distance equals it (scenarios at the autonomous maximum cruise speed) |
| `failsafe_stop_distance_at_top_speed` | the same checks in scenarios that expect this distance (radio failsafe from top speed) |
| `max_wire_voltage_drop_fraction` | electrical `voltage_drop_exceeded` diagnostics. Estimated if any wire length is not routed. |
| `min_wire_clearance_moving` | wiring `wire_min_clearance_moving`, plus the scenario check `min_wire_clearance_m` |
| `max_component_temperature_c` | the hottest component in electrical `thermal` |

## Domain views

Every domain view includes `result`, the stage's `result.json` (diagnostics with hints,
artifact URLs, `status`, `stale`).

- **model**:
  - `graph`: `build/model/graph.json` (`nets`, `edges`, `endpoints`, `components`,
    `wires`, `rails`, `i2c_buses`, `interference`, `mass`, `pins`, `net_names`)
  - `assembly`: `build/model/assembly.json`, or `null` without an `assembly:` section
  - `components`: `{id: {type, pose: {xyz, rpy}, mount, label, kind, name, shape, size, mass}}`.
    Poses are compiled from the assembly when one is declared.
  - `joints`: `mechanical.joints` from `robot.yaml`
  - `derived_joints`: joints from the assembly compiler
  - `images[]`
  - `results: {assembly, model}`
- **cad**:
  - `cad_model`: `build/cad/cad_model.json` (`source`, and `parts`:
    `{id: {mass, com, inertia, bbox_min, bbox_max, mesh, mass_status}}`)
  - `meshes: {component: STL URL}`
  - `onshape: {document, part_studio?, lock}`
  - `views[]`: Onshape REST shaded views and other CAD images
- **wiring**:
  - `routes`: `build/wiring/routes.json` (`wires`, `harnesses`, `clearances`)
  - `routing_detail`
  - `bom[]`: rows of `harness_bom.csv`, with numbers parsed
  - `bom_url`
  - `clearances: {static[], moving: {wire: m}, worst_static, worst_dynamic, requirement}`
  - `slack_sim`
  - `slack_timeline`: `null` until the wiring stage persists it
  - `harnesses: {id: {index, wires[], connectors[], route: {via, tie_points, slack, keep_away}, jacket}}`
  - `images[]`

  **Editing a route:** `robot.yaml` stores harnesses as a list, and a merge patch
  replaces lists whole. So change `route.via` or `route.tie_points` on
  `spec.electrical.harnesses[index]` from `GET .../spec`, then PATCH
  `{"patch": {"electrical": {"harnesses": [...the full list...]}}, "sha256": ...}`
  and POST `/runs` with `{"stages": ["wiring"]}`.
- **electrical**:
  - `operating_points`: the full `operating_points.json`
  - `table[]`: `{id, operating_point, battery_state, kind, soc, battery_current,
    battery_voltage, motor_current, rails: {net: V}}`
  - `wires[]`: `{id, continuous_current, ampacity, continuous_ratio, burst_current,
    burst_ratio, awg, harness, ...}`
  - `connectors[]`: `{id, continuous_current, rating, continuous_ratio, burst_current,
    burst_rating, burst_ratio, ...}`
  - `power_tree: {rails[] (source, nominal, min, max, sinks), fuses, brownout, harness_voltage_drop}`
  - `runtime`, `thermal`, `images[]`
- **pcb**: `boards[]`, each with:
  - `board`, `component`, `spec`
  - `board_json`: `build/pcb/<board>/board.json`
  - `renders[]`, `erc`, `drc`
  - `connectors`: the board's pin → net map
  - `catalog_connectors: {connector: {pin number: component pin}}`, to compare against `connectors`
  - `bom`, `gerbers_url`, `files[]`, `missing`
- **software**:
  - `bindings[]`: the binding fields plus `pin_map[]` (`{pin, node, net, net_name,
    devices[], reaches[]}`) and `expects_reached[]` (`{pin, reached}`). `devices` are the
    component pins on the same net. `reaches` adds those reached through internal links
    (series resistors).
  - `modules[]`: `{name, language, runs_on, entrypoint, path, exists, files[]}`
  - `config`
  - `firmware_logs[]`: per scenario, `{scenario, firmware_logs[], software,
    software_log_url, software_log_tail}`
  - `deploy_log_url`
- **sim** (list): `scenarios[]`, each a `robot.yaml` scenario (`id, description,
  duration, initial, mission, pilot, faults, expect`) plus `last`. `last` is `null` or:
  - `passed`, `checks[]` (`{key, expected, measured, pass, detail}`), `metrics`
  - `fallbacks`, `stale`, `summary_url`
  - `plots[]`, `renders[]`, `clip_url`
  - `recording`: bool
  - `diagnostics[]`

  The list also has `overview_url`.
- **sim/{scenario}** (playback): `{scenario, last, summary, recording}`, where
  `recording` is `null` or:
  ```json
  {"hz": 30, "duration": 90.0, "channels": ["battery_i", "battery_v", "speed", "..."],
   "series": {"t": [0, 0.033, ...], "speed": [...], "battery_v": [...], "rssi": [...], "lq": [...],
              "motor_current": [...], "steer": [...], "soc": [...], "rail_5v": [...], "wire_clearance": [...], "...": []},
   "firmware_logs": [...], "events": [...],
   "frames": {"t": [...], "format": "x y z qw qx qy qz (world frame)",
              "bodies": {"chassis": [[x, y, z, qw, qx, qy, qz], ...], "knuckle_fl": [...], "wheel_fl": [...]},
              "component_bodies": {"battery": "chassis", "wheel_fl": "wheel_fl", "...": "..."},
              "cables": {"cable00s0": {"rgba": [r, g, b, a], "points": [[[x, y, z], ...per cable body], ...per frame]}}}}
  ```
  - `series` resamples every 1-D channel of the 100 Hz recording to `hz`.
  - `frames` comes from forward kinematics of `vehicle.xml` at the recorded 30 Hz
    `snapshot_qpos`, at most `hz`.
  - To place a component, compose its body pose with its pose relative to that body. For
    the chassis body that relative pose is the robot-frame pose from **model**; for a
    moving body, use the body's rest pose.
  - `cables=false` drops the cable polylines, which are the bulk of the payload.

## Run events

Runs execute in fresh worker processes. Runs of one project never overlap: they are
queued FIFO. Runs of different projects run in parallel. Results are persisted to
`build/<stage>/result.json` exactly as `r3 validate` does, so every view reflects a
finished run.

`RunStatus` fields:
- `run_id`, `project`
- `kind`: `validate` | `deploy_sim`
- `status`: `queued` | `running` | `succeeded` | `failed` | `cancelled` | `error`
- `stages`, `scenarios`, `current_stage`
- `created_at`, `started_at`, `finished_at`
- `events`: the event count
- `result`: the payload of `run_finished`

Runs live in the server's memory. After a restart, the persisted results remain.

Every SSE message has the shape `id: <seq>`, `event: <type>`,
`data: {"seq", "type", "time", "run_id", ...}`. A `: keep-alive` comment is sent every
15 s of silence. The stream replays from the start (or after `?after=` /
`Last-Event-ID`) and closes after `run_finished`.

| `type` | Payload |
| --- | --- |
| `run_queued` | `project, kind, stages, scenarios` |
| `run_started` | `project, stages, scenarios` |
| `stage_started` | `stage, index, total` |
| `log` | `stage, stream: stdout\|stderr\|logging, line` (and `level` for `logging`) |
| `progress` | `stage, message` (from stages with a `progress` callback, e.g. sim per scenario) |
| `diagnostic` | `stage, diagnostic: {code, severity, message, subjects, data, stage, hint}` |
| `stage_finished` | `stage, status, ok, stale, counts, duration_s, started_at, metrics, artifacts` |
| `run_finished` | `status: succeeded\|failed\|cancelled\|error, ok, counts?, stages?, release?: {fabrication_ready, counts, blocking}, error?: {type, message, traceback}` |

The report stage always runs last. Invalidate project queries on `stage_finished` and
`run_finished`.

## Agent sessions (Claude Code / Codex)

`POST /api/projects/{pid}/agent` runs the provider's own CLI, so it uses the user's
subscription or key. The CLI runs non-interactively in the project directory, and the
prompt is passed on stdin.

Every session writes `<project>/build/agent/<sid>/`:
- `.mcp.json`: `python -m r3_orchestrator.mcp_server` with `R3_PROJECT` and
  `PYTHONPATH` set
- `transcript.jsonl`: the raw CLI output
- `stderr.log`

The two CLIs are launched as follows:
- **Claude Code**: `claude -p --output-format stream-json --verbose --mcp-config
  <session>/.mcp.json --strict-mcp-config --permission-mode default
  --append-system-prompt <R3 loop> --allowedTools mcp__r3__<every ungated tool> Read
  Glob Grep --disallowedTools mcp__r3__deploy_fleet mcp__r3__create_github_repo
  [--model M]`
- **Codex**: `codex exec --json --skip-git-repo-check --color never -C <project>
  --sandbox workspace-write -c approval_policy="never" -c
  mcp_servers.r3.{command,args,env}=... -c
  mcp_servers.r3.disabled_tools=["create_github_repo","deploy_fleet"] [--model M] -`

Permission tiers (`mcp_server.TOOL_TIERS`) are kept intact:
- Gated tools are never pre-approved. Claude denies them explicitly; deny rules hold
  even if the user's settings default to bypassing permissions. Codex disables them for
  the session.
- The tools themselves still require `confirm=true`.
- Fleet deployment and GitHub creation stay human actions, through `/deploy` and
  `POST /projects` with `confirm`.
- Built-in file editing and shell tools are not pre-approved for Claude, so it edits
  through `r3_patch_spec`.

`AgentStatus` fields:
- `session_id`, `project`, `provider`, `model`, `prompt`
- `status`: `starting` | `running` | `completed` | `failed` | `cancelled`
- `created_at`, `finished_at`, `returncode`
- `result`: the final text
- `error`, `events`, `transcript`

### Agent events

Agent events use the same SSE framing as run events; `data` includes `session_id`.

| `type` | Payload |
| --- | --- |
| `session_started` | `project, provider, model, cwd, command[], cli_version` |
| `message` | `role: assistant\|user\|system\|thinking\|error\|output, text` (`output` is a non-JSON stdout line) |
| `tool_call` | `id, name` (without `mcp__r3__`), `server` (`r3`, or `null` for built-ins / `shell`), `input` |
| `tool_result` | `id, name, ok, content` (text, at most 20 000 characters) |
| `run_started` | `tool_call_id, tool: r3_validate\|r3_run_stage\|r3_run_scenario, stages, scenarios`: a pipeline run the agent started through MCP |
| `run_finished` | `tool_call_id, tool, ok, counts, stages, code` |
| `spec_changed` | `previous_sha256, sha256, diff` (unified diff since the last change), `cumulative_diff` (since the session started) |
| `usage` | `input_tokens, output_tokens, cost_usd?, turns?` |
| `stderr` | `line` |
| `raw` | `event`: any CLI event the normalizer does not map, verbatim |
| `session_finished` | `status, returncode, result, error, spec_changed, cumulative_diff` |

The server checks `robot.yaml` after every tool result and at exit. Show
`cumulative_diff` live and refresh the views on `spec_changed`, `run_finished` and
`session_finished`.

When a CLI is missing, `GET /api/agent/providers` (and `agents` in `/api/doctor`)
reports `available: false`, with a `detail` explaining how to install it. Starting a
session then returns 503 `agent_cli_missing`.

## Tests

```bash
.venv/bin/python -m pytest tests/test_api_server.py tests/test_api_runs.py tests/test_api_agents.py tests/test_api_release.py
```

- The tests use FastAPI's TestClient against a temporary copy of `examples/robots/rc-car`,
  with real assembly and model results plus stage hand-off files in the documented
  formats.
- Runs use fake stages (`tests/api_fake_stage.py`) in real worker processes.
- The agent tests replace `claude` and `codex` with `tests/api_fake_agent_cli.py`, so
  no subscription quota is used. One test starts the generated `.mcp.json` server over
  stdio and calls `r3_status`.
