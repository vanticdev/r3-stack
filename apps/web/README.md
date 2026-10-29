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

The shell has one header per project: its name, one status summary ("Not ready · 3 stages failed ·
Simulation results outdated") that expands into stage rows with their evidence, rerun actions and a
copyable "Revision details" disclosure, and Run pipeline. Below 900 px the navigation becomes a drawer
behind a one-row header, grids become single columns, tables scroll inside their containers with a
pinned first column, and inspectors become bottom sheets. Stage names match the navigation
(`lib/labels.ts`), results computed from older inputs say so in words (`StaleNotice`), and ids, codes
and hashes sit in copyable `Details`.

All project pages live under `#/p/<pid>/...`:

- **Projects:** a list of projects with one status line each; New project opens a dialog with the
  templates (optionally with a GitHub repository, behind a confirmation step).
- **Overview:** one readiness summary with the failing checks under it; requirements with the scenario
  evidence that backs them, result and freshness kept separate; the pipeline with counts linked to the
  diagnostics and environment problems next to the stages they limit.
- **3D model:**
  - CAD STL meshes, with catalog envelopes as fallback; harness tubes in wire colours;
  - a Show menu of layers (`SHOW_LAYERS` in `viewer/RobotViewer.tsx`, the extension point for new
    overlays), Move joints (including a linked Ackermann steering slider), X-ray, Explode and Fit;
  - interference regions as outlined boxes with a legend; clicking one selects both parts;
  - a searchable parts list with an Errors filter; a selected part shows its diagnostics first;
  - `?focus=` highlights diagnostic subjects, `?sel=` selects, `?int=` selects an interference.
- **Wiring:**
  - an inventory line and the worst failure, with Locate interference and View errors;
  - a severity-sorted harness list; the selected harness is framed, drawn thicker, with its closest
    approach marked; Fit harness, Show obstacles, Edit route;
  - route editing: drag `route.via` waypoints and tie points (with an XYZ mm readout), then save,
    which PATCHes `robot.yaml` with its sha and re-runs the wiring stage;
  - Wires (scoped to the selected harness), Harness BOM, Cable motion (MuJoCo cable playback) and Plots.
- **Electrical:** worst cases across all battery states (failures first, linked to their circuits;
  wire and contact current vs rating), then the selected operating point: an orthogonal power tree,
  calculated rail voltages flagged in both directions against their tolerance, and the operating points.
- **PCB:** board status and blockers; Build PCB and View build log; checks (ERC, DRC, routing, pin map)
  as a list; render, diagnostics, pin map, BOM and files each render on their own.
- **Software:** each binding from its device pin to the Pi header pin and GPIO, with the net path in an
  expander; the netlist match with its freshness; the simulation firmware log with aligned columns,
  expandable fields and jump-to-first error/warning; the firmware module record.
- **Simulation:** a compact scenario selector; checks in words with margins and timestamps that seek
  every view; grouped metrics; 3D playback, selectable chart groups and the MP4 clip, all on one shared
  playback bar.
- **Diagnostics:** grouped by stage and failure type, compact rows (cable, part, amount, time), the fix
  for the selected row, Locate in 3D, and neutral geometry except the selection.
- **Agent:** composer first; history of earlier sessions; Reply (a new session carrying the previous
  task) or New task; outcome first (lines changed, checks, errors remaining); the robot.yaml diff in a
  resizable pane once something changes.
- **Deploy:** Simulation or Physical robots, destination-specific readiness with the override shown
  up front, the bundle revision, and a typed confirmation for robots. The API's 428 is honoured.
- **Environment** (`#/environment`, workspace level): root causes first with their fixes and the stages
  they affect, agents, this machine, stage readiness and tools. The theme toggle is in the sidebar.

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
  It loads every page at 1600 px and 390 px in the dark and light themes and checks for console
  errors. It also drives the status summary and the mobile drawer, the create dialog, the 3D parts
  list, layers, joints and exploded view, the diagnostic selection, locating a wiring failure and
  route editing, cable motion, the simulation check timestamps and scrubber, the theme toggle, the
  physical-robot deploy confirmation, a pipeline run and an agent session.
  - Screenshots go to `test-results/screenshots/<page>-<width>-<theme>.png`. Set `R3_E2E_SCREENSHOTS`
    to change the directory, which is relative to `apps/web`.
  - To test a running server instead, set `R3_E2E_BASE_URL`, for example `http://127.0.0.1:8321`
    for `r3 serve` with a built `dist/`. Against a real server the run/agent test is skipped,
    because it would execute stages and use your Claude/Codex subscription.
  - The test uses Playwright's Chromium when it is installed (`npx playwright install chromium`).
    Otherwise it falls back to the locally installed Google Chrome.
- **Node loader test:** `node --test tests/loader.test.mjs`. It runs the scene loader
  (`src/lib/scene.ts`, type-stripped) against a build directory, and
  `tests/test_orchestrator_report.py` runs it too.
