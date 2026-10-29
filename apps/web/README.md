# R3 web app

The single R3 web interface: one place to describe a robot, run the pipeline, and see, edit and
diagnose every domain. It is a Vite + React + TypeScript (strict) app. It uses three.js through
`@react-three/fiber` and `@react-three/drei`, TanStack Query, React Router (hash routes) and uPlot.
It talks only to the R3 HTTP API (`r3 serve`, [docs/architecture/api.md](../../docs/architecture/api.md)),
so the web app, CLI, MCP server and agents all share the same data and operations.

## Commands

Run these from `apps/web/` (Node >= 22.18; the worker image has Node 24):

```bash
npm ci                 # install the locked dependencies
npm run dev            # dev server on :5173; /api goes to r3 serve, or to the mock when it is down
npm run dev:mock       # dev server that always uses the mock API
npm run build          # type-check + production build -> apps/web/dist (served by `r3 serve`)
npm run preview        # serve dist/ with the same /api routing as the dev server
npm run typecheck      # tsc -b over the app, the mock layer and the e2e tests
npm test               # vitest unit tests (src/**/*.test.ts)
npm run test:e2e       # Playwright smoke test, headless Chromium (builds first)
npm run check          # typecheck + unit + e2e
```

To run against the real backend, start `r3 serve` (default `http://127.0.0.1:8321`; set `R3_API_URL`
to use another address), then run `npm run dev`. Or run `npm run build` and open `r3 serve` itself,
which serves `apps/web/dist`.

```bash
.venv/bin/python -m r3_orchestrator serve --projects-root examples/robots     # repository root
```

## Real API or mock

The dev and preview servers route `/api` through `mock/backend.ts`:

- **`R3_API=auto` (default):** proxy to `r3 serve` when its port accepts connections; otherwise
  answer from the mock layer. SSE streams pass through unbuffered.
- **`R3_API=mock`:** always use the mock.
- **`R3_API=proxy`:** always proxy.

The mock (`mock/api.ts`) implements the documented contract. It returns **real data**, read on each
request from each project's `build/` artifacts under `examples/robots/*`:

| Artifact | Endpoints |
| --- | --- |
| `cad/cad_model.json`, `cad/meshes/*.stl` | `/cad`, mesh URLs |
| `model/graph.json` | `/model`, `/software` (binding pin maps), `/electrical` (rails) |
| `wiring/routes.json`, `routing_detail.json`, `slack_sim.json`, `harness_bom.csv` | `/wiring` |
| `electrical/operating_points.json` | `/electrical` |
| `pcb/<board>/board.json`, renders, BOM | `/pcb` |
| `sim/<scenario>/summary.json`, `recording.npz`, renders, `clip.mp4` | `/sim`, `/sim/{scenario}` |
| `<stage>/result.json` | `/projects/{pid}`, `/diagnostics` |

`scripts/convert_recording.py` converts `recording.npz` into the contract's playback JSON:
- the series are resampled to `?hz`;
- `frames.bodies` holds per-frame world poses of every MuJoCo body, computed with `mj_kinematics`
  on the scenario's `vehicle.xml`;
- `frames.component_bodies` and the cable polylines are included.

The mock runs the converter in the shared venv and caches the result in `.mock-cache/`. The
converter is found through `R3_PYTHON`, `.venv`, or the main checkout's `.venv` when you work in a
git worktree.

Mutations never touch the repository:
- Spec PATCHes are applied in memory. They keep the YAML formatting, so the diffs stay minimal.
- Runs replay the existing `result.json` files as SSE events.
- The agent is a scripted, clearly labeled session.
- Fleet deploys are refused.

Every mock response carries `X-R3-Mock: 1`, and the UI then shows a "Mock API" banner. The release
gate in the mock approximates `r3_orchestrator/release.py`. Only `r3 serve` is authoritative.

If the artifacts are missing, generate them from the repository root:

```bash
export PYTHONPATH=packages/robot-model:packages/component-catalog:packages/simulation:packages/hardware-interface:packages/wiring:services/simulation-worker:integrations/onshape:integrations/kicad:services/orchestrator
.venv/bin/python -m r3_orchestrator validate -C examples/robots/rc-car --stage model --stage wiring --stage electrical
.venv/bin/python -m r3_sim_worker examples/robots/rc-car --scenario full_throttle
.venv/bin/python -m r3_sim_worker examples/robots/rc-car --scenario steering_sweep   # cable slack playback
```

## Pages

All project pages live under `#/p/<pid>/...`:

- **Projects:** the project list, plus create from a template (optionally with a GitHub repository,
  behind a confirmation dialog).
- **Overview:** the release gate (requirement evidence matrix with stage, check, value, artifact and
  input fingerprint), stage chips, Run pipeline with a live log, Onshape/GitHub/report links, and
  doctor warnings.
- **3D:**
  - CAD STL meshes, with catalog envelopes as fallback;
  - harness tubes in wire colours;
  - connector/assembly interface and join overlays;
  - joint axes and sliders (including a linked Ackermann steering slider) and an exploded view;
  - click to inspect: catalog data, mass source and diagnostics;
  - `?focus=` highlights diagnostic subjects.
- **Wiring:**
  - harness list, BOM, and per-wire current vs ampacity, clearance and tightest bend;
  - 3D route editing: drag `route.via` waypoints and tie points, then save, which PATCHes
    `robot.yaml` with its sha and re-runs the wiring stage;
  - MuJoCo cable slack playback.
- **Electrical:** power tree, operating-point table per battery state, rail voltages, wire and
  contact current vs rating bars, and runtime.
- **PCB:** top/bottom renders, ERC/DRC, connector pin → net against the catalog, BOM, and a gerber
  download.
- **Software:** binding map (handle → Pi pin → net → device pin, through series links), firmware
  modules, and a firmware log viewer.
- **Simulation:**
  - scenario cards with run buttons;
  - 3D playback from the recorded body poses and cables;
  - speed, battery, rails, motor, steering, RSSI/LQ and clearance charts;
  - one shared scrubber and the MP4 clip.
- **Diagnostics:** grouped by stage, severity or subject, with hints. Clicking a diagnostic
  highlights its subjects in 3D.
- **Agent:** run Claude Code or Codex. Shows the streamed transcript, tool calls, triggered runs and
  the live `robot.yaml` diff.
- **Deploy:** sim or fleet. Fleet needs a typed confirmation, and the API's 428 is honoured.
- **Settings:** `r3 doctor`, agent CLIs and the theme.

Live updates: run and agent SSE streams invalidate the project's queries when stages, runs and
sessions finish.

## Layout

```
src/api/        typed client (types.ts mirrors the OpenAPI models), TanStack Query hooks, SSE parser
src/lib/        pure transforms: poses/joints/playback (transforms.ts), scene model (scene.ts),
                domain tables (domain.ts), release-gate formatting, units, route-hint patches
src/viewer/     the three.js robot viewer, route editor, inspector
src/pages/      one file per page;  src/components/  layout, charts (uPlot), shared UI
mock/           dev/preview /api router (backend.ts) and the build/-artifact mock (api.ts)
scripts/        convert_recording.py (recording.npz -> playback JSON)
e2e/            Playwright smoke test;  tests/  Node loader test used by tests/test_orchestrator_report.py
```

## Tests

- **Unit tests:** `npm test`. vitest covers the pose and joint transforms, playback interpolation
  and rest poses, Ackermann steering, the exploded view, SSE parsing, the run-event reducer, binding
  pin maps, PCB pin maps, release-gate precedence and formatting, wiring rows, route-hint patches,
  and the diff and CSV helpers.
- **E2E:** `npm run test:e2e`. It builds the app and serves it with `vite preview` on the mock API.
  It then loads every page and checks for console errors. It also drives the 3D inspector, overlays,
  joints and exploded view, the diagnostic highlight, route editing, the slack playback, the
  simulation scrubber, a pipeline run, an agent session, the light theme and the fleet deploy
  confirmation.
  - Screenshots go to `test-results/screenshots/*.png`.
  - To test a running server instead, set `R3_E2E_BASE_URL`, for example `http://127.0.0.1:8321`
    for `r3 serve` with a built `dist/`. Against a real server the run/agent test is skipped,
    because it would execute stages and use your Claude/Codex subscription.
  - The test uses Playwright's Chromium when it is installed (`npx playwright install chromium`).
    Otherwise it falls back to the locally installed Google Chrome.
- **Node loader test:** `node --test tests/loader.test.mjs`. It runs the scene loader
  (`src/lib/scene.ts`, type-stripped) against a build directory, and
  `tests/test_orchestrator_report.py` runs it too.
