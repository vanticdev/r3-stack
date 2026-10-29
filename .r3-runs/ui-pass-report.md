# UI pass report (branch `r3/ui-pass`)

Scope: `apps/web/` only (no backend, deployment or protocol change, so no `.blitz-build-version` bump).

## Validation
- `npm run typecheck`: clean.
- `npx vitest run`: 3 files, 51 tests pass (new `src/lib/presentation.test.ts`: labels, project status,
  release grouping, environment root causes, rail tolerances, orthogonal power-tree layout).
- `node --test tests/loader.test.mjs`: 4/4 pass.
- Playwright, mock API (`npx playwright test`): 20/20 pass.
- Playwright, live `r3 serve` (`R3_E2E_BASE_URL=http://127.0.0.1:8331`, `--projects-root examples/robots`,
  built `dist/`): 19 pass, 1 skipped by design (the run/agent test would execute stages and spend the
  agent subscription). Every test asserts no console errors, no failed requests and no HTTP 5xx.
- Screenshots: `apps/web/test-results/screenshots/<page>-{1600,390}-{dark,light}.png` (12 screens × 4 =
  48, mock) plus interaction shots; the same set from the live server is in `test-results/screenshots/live/`.
  Non-3D pages are captured full length (the app scrolls inside `.content`; the test lets the document
  grow for the capture).

## Shared pieces (kept stable for the `r3/sourcing` and `r3/connectors` merges)
- `PROJECT_NAV` (exported) and the routes in `App.tsx` stay the place to add a page.
- `SHOW_LAYERS` in `viewer/RobotViewer.tsx` is the extension point for 3D overlays (connector pins add
  one entry plus a `ViewOptions` boolean). Existing `ViewOptions` keys are unchanged. The viewer has only
  new optional props: `toolbarStart`, `markers`, `fitRequest`, `onInterference`, `selectedInterference`.
  `RouteEditState.onActiveChange` is new and optional.
- New UI primitives in `components/ui.tsx`: `Details` (copyable rows), `CopyButton`, `StaleNotice`,
  `CheckRow`, `Popover`, `Tabs`, `Dialog`. Existing exports (`Kpi`, `Chip`, `StageChip`, `EvidenceChip`,
  `ConfirmDialog`, …) are kept.
- Pure helpers: `lib/labels.ts` (stage names = navigation names, part/op labels, diagnostic types,
  headlines, facts), `lib/status.ts` (one project status), `lib/environment.ts` (root causes),
  `lib/release.ts` (labels, results, grouping), `lib/domain.ts` (`drawPowerTree`, `railLimits`, `railFlag`).

## C. Real backend
- Already done before this pass (per the task): typed client aligned to the live API; proxy `res.on("close")`.
- Verified against live `r3 serve`: every page renders real data with no console errors (above).
- Mock diffs found by comparing every endpoint's live and mock JSON shapes, and fixed in `mock/api.ts`:
  scenario expectation `subject` is the scenario id (the mock used `scenario.key`, which broke units on
  live data, so the UI now derives the key from the item id); binding `frequency`/`baud`/`channel` are
  normalized like the parsed spec (the mock sent `"50 Hz"`, which rendered "50 Hz Hz").
- Remaining known shape differences: the mock adds `recording.frames.rest`, and its doctor reports
  mock-specific tools. Neither changes what the UI shows. TS types were not regenerated from OpenAPI in
  this pass, because the task says the client alignment is done.
- The live e2e uses the real `examples/robots/rc-car` project through `r3 serve`, not a TestClient fixture project.

## Checklist: 60 findings

Legend: **done**, **changed** (implemented differently, reason given), **disputed** (not done as written, reason given).

### A. Shell (applies to every screen)
| # | Finding | Status |
|---|---|---|
| A1 | Responsive shell: drawer < 900 px, one-row header, single column, tables scroll with pinned first column, bottom-sheet inspectors | done |
| A2 | Header = identity + one status summary, expandable stage rows linking to evidence; hash in copyable "Revision details"; stage names match navigation | done (nav "3D" renamed "3D model") |
| A3 | Freshness explicit everywhere ("out of date … Rerun X") | done (`StaleNotice`, "Outdated · inputs changed" tags; no hover-only clock) |
| A4 | Engineering language; codes/ids/hashes in expandable details; no pitch sentences or KPI decoration | done |
| A5 | One primary action per screen; theme toggle in one place; one "Environment" destination | done (Run pipeline is primary only on Overview; theme only in the sidebar) |
| A6 | Missing data never hides available data; no fake passes | done (PCB sections independent; empty pin map = "No pins available") |

### Projects
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: collapse sidebar, one column, New project beside the title | done |
| 2 | Status pills → "Results out of date · Previous run: N errors · M warnings", failed domains grouped, View errors | done |
| 3 | Full-width content-sized rows; creation in a dialog | done |
| 4 | "RC car" / "Empty project" with descriptions; GitHub owner and visibility; "Create project" | changed: the API does not expose the GitHub owner, so the dialog says "the account signed in to `gh` on the server" |
| 5 | One name plus metadata, one-line description, no pitch or cube, status second | done |

### Overview
| # | Finding | Status |
|---|---|---|
| 1 | Mobile drawer, compact header, full-width evidence | done |
| 2 | One summary "Not ready for fabrication: 5 failed · 4 outdated · …", failing checks below | done |
| 3 | Human evidence ("Wire voltage drop: 4.19%, required ≤ 3.00%"), ids/hashes/JSON in details | done |
| 4 | Scenario evidence grouped under its requirement; result separate from freshness ("Previous result … · rerun required") | done (identical evidence collapses to "Measured in scenario …") |
| 5 | One pipeline list with spelled-out counts linked to diagnostics, capability problems beside the affected stage, one primary Run, links as metadata | done (a problem affecting most stages is stated once above the list) |

### 3D
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: full-width canvas, drawer, inspector as bottom sheet | done |
| 2 | Interferences toggle with count, outline style, legend; selecting highlights both parts and opens its diagnostics | done; on the live data the CAD stage checks interference on the exact B-rep, so there are no envelope regions and the layer is hidden |
| 3 | Searchable parts list with Errors filter; selected part shows diagnostics first; remove the contradictory mesh stats | done |
| 4 | "Show" menu, separate Move joints, X-ray/Explode/Fit as view controls | done |
| 5 | Displayed model's revision as Current / Out of date; stage results collapsed | done ("Model current · built …" badge, header summary) |

### Wiring
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: drawer, single-column summary, harness selector before the viewer, scrolling tables | done |
| 2 | Inventory line plus worst failure with Locate interference / View errors; label stale results | done |
| 3 | Frame selected harness, fade the rest, thicker route, min-clearance point, Fit harness / Show obstacles / Edit route visible, other layers in a menu, XYZ mm in edit mode | done |
| 4 | Selection list sorted by severity, details in an inspector, "1 tie" | done |
| 5 | Persistent scope selector, global sections labelled, "Cable motion" | done |

### Electrical
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: drawer, one summary, stacked content, pinned id column | done |
| 2 | Separate "Worst cases across all battery states" and "Selected operating point", selectors together, condition in each heading | done |
| 3 | Lead with the failures ("Pi supply reaches 0 V at motor stall · minimum charge", "ESC BEC exceeds its 2 A rating by 380 mA") linked to circuits; passes secondary | done ("Show circuit" selects that operating point) |
| 4 | Orthogonal directional power tree, rail junctions, labels clear of wires, short names with details on select, control beside it | done (tidy-tree layout, back-feed in a side lane) |
| 5 | Nominal vs tolerance ("not specified"), flag both directions, "Calculated rail voltages", pins in row details | done; changed detail: unstated tolerances are checked against an explicitly labelled ±5 % assumption, so 3.47 V on 3V3 is flagged "high" |

### PCB
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: drawer, full width | done |
| 2 | Each artifact independently; mark only the missing ones | done (only exercised with board.json present; the current builds all contain it) |
| 3 | "Board data unavailable for …", last-run outcome, Build PCB, View build log, toolchain in an expander | done (View build log opens the stage's own `kicad-cli.log`) |
| 4 | Explicit PCB status by the board name, relevant blockers | done |
| 5 | Checks list Passed/Failed/Not run/No pins available, freshness, mismatches emphasized, quiet checkmarks | done |

### Software
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: drawer, one-row header, expandable binding rows | done |
| 2 | `esc.J_SIG.1 · SIG`, one route per Pi pin, path in an expander, header pin + GPIO | done |
| 3 | "Netlist match" with checked revision and freshness; plain interface text | done |
| 4 | "Simulation firmware log" with scenario/run, filter toolbar, aligned columns, collapsed fields, warning/error counts with jump-to-first | done |
| 5 | Module record + expandable "9 source files" with real links; drop subtitle | changed: no endpoint serves `backend/` sources, so files link to GitHub when the project has a repository; otherwise they are listed by name with that explanation |

### Simulation
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: drawer, full-width scenario selector, single column | done |
| 2 | Compact "3D recording unavailable; time-series available", hide camera controls, charts up | done |
| 3 | "Previous run: 2 checks passed · inputs changed", run time, revision, "Rerun full throttle" | done |
| 4 | "Top speed: 8 m/s · required ≥ 8 m/s", 99.6 %, "not recorded", failures and margins first, grouped by subsystem | done |
| 5 | Compact selector, shared playback bar beside the charts, check timestamps seek every view, pointer scrubbing, selectable chart groups | done (sticky playback bar) |

### Diagnostics
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: full-width list, Issues/3D toggle, no fixed 560 px column | done |
| 2 | Subgroups by failure type ("Cable interference · 5"), compact rows (cable, part, amount, time), explanation and fix only for the selected row | done (large groups show 6 rows + "Show all") |
| 3 | Neutral geometry except the selection, location marked, "Locate in 3D" per finding, selection context above the viewport | done |
| 4 | Human headlines first, ids in copyable details | done |
| 5 | "Wiring results are out of date" with rerun; hash and stage list in run details | done |

### Agent
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: menu, full-width chat/changes tabs, sticky composer | done |
| 2 | One wide composer "What should change?"; changes pane after the first edit, resizable split | done |
| 3 | Keep history, Reply vs New task, hide starters after the first message | changed: the API cannot resume a session, so Reply starts a new session carrying the previous task and its result. History comes from `GET /projects/{pid}/agent` (sessions kept by the running server) |
| 4 | "Describe a robot change. The agent edits robot.yaml and runs checks."; outcome first; metadata in details | done |
| 5 | At most two quiet examples, or suggestions from real diagnostics | done (two suggestions from the most specific current errors) |

### Deploy
| # | Finding | Status |
|---|---|---|
| 1 | Mobile: drawer, stacked targets, visible action below the summary | done |
| 2 | Wrapped text, regular weight, radio indicators, "Simulation" / "Physical robots", tech in an expander | done |
| 3 | List discovered robots; repeat destinations and bundle revision in the confirmation ("Deploy to 3 robots") | partly disputed: there is no discovery endpoint (BLITZ discovers targets only during `r3 deploy --target fleet`), so no robot list or count can be shown beforehand. The destination ("every robot BLITZ discovers on the network (Pi role)") and the bundle revision are named in the confirmation |
| 4 | Destination-specific readiness, linked blocking checks, failures separate from unrun, override shown up front | done |
| 5 | Identity plus one readiness summary; Run pipeline secondary; deploy primary | done |

### Settings + shell
| # | Finding | Status |
|---|---|---|
| 1 | Mobile drawer, compact header, collapsed status, one column | done |
| 2 | Root cause first ("Build service unavailable — stages cannot execute") with fix, stages grouped, "Check environment again" | done (re-check uses `?refresh=true`) |
| 3 | Expandable summary with explicit labels linking to evidence; hash in revision details; consistent domain names | done |
| 4 | One workspace-level "Environment"; theme in one place | done (`/settings` and `p/:pid/settings` redirect) |
| 5 | Real agent names, availability and setup action | done ("Claude Code", "Codex") |
