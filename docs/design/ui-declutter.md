# Calm default screens

Each screen leads with its task; evidence stays in native keyboard-accessible disclosures.
Workspace tools and build/test navigation expand on request. Blocking status, stale
results, unavailable tools and deployment safeguards stay visible. No API, deployment
version or viewer rendering implementation changed.

## Default questions

- **Projects:** What am I working on? Name, state, last activity.
- **Model bank:** Which model do I need? Search and two matches; full catalog and add form expand.
- **Environment:** What prevents work here? Cause headlines; fixes and tool inventory expand.
- **Overview:** Is this robot OK, and what next? Readiness, three blockers, model.
- **3D model:** What does it look like? Full-width model; inspector closed.
- **Wiring:** Where do the wires go? Model and error count; harnesses and tables expand.
- **Electrical:** Does power work? Limit summary and circuit; measurements expand.
- **PCB:** Is the board ready? Board view or honest empty state; checks and BOM expand.
- **Parts:** What must I buy? Purchase summary; shopping list and providers expand.
- **Software:** Are devices connected? Binding summary; binding map, modules and logs expand.
- **Simulation:** What happened? Playback and verdict; scenarios, checks and metrics expand.
- **Diagnostics:** What needs fixing? Error groups by meaning; warnings/info use a filter.
- **Agent:** What should change? Conversation and outcome; history, suggestions and diffs open on request.
- **Deploy:** Where will this run? Destination and readiness; bundle and explanation expand.

## Measurement

1440 × 900, light theme, populated copied `rc-cool-car-23`, real isolated API.
Counts include the navigation and header. Words are whitespace-separated visible
DOM text within the viewport; controls are visible links, buttons, inputs, selects,
textareas, summaries and tabs. Hidden disclosure content and off-screen text are
excluded. Canvas geometry labels are not DOM text. The script also records text-node
counts, uncaught page errors and screenshots. This measures initial density, not usability.

The final counter explicitly excludes text nodes directly under closed `details`:
Chromium can report rectangles for them. Baseline disclosures were inspected and
have element-wrapped children, so that correction does not change baseline counts.

| Page | Words before → after | Reduction | Controls before → after |
| --- | ---: | ---: | ---: |
| Projects | 69 → 20 | 71.0% | 9 → 7 |
| Model bank | 192 → 72 | 62.5% | 29 → 11 |
| Environment | 204 → 61 | 70.1% | 6 → 11 |
| Overview | 454 → 73 | 83.9% | 57 → 23 |
| 3D model | 272 → 51 | 81.2% | 42 → 17 |
| Wiring | 166 → 67 | 59.6% | 36 → 22 |
| Electrical | 422 → 150 | 64.5% | 25 → 21 |
| PCB | 56 → 36 | 35.7% | 18 → 11 |
| Parts | 142 → 43 | 69.7% | 18 → 13 |
| Software | 318 → 54 | 83.0% | 36 → 15 |
| Simulation | 331 → 109 | 67.1% | 57 → 26 |
| Diagnostics | 305 → 102 | 66.6% | 57 → 29 |
| Agent | 105 → 39 | 62.9% | 26 → 18 |
| Deploy | 114 → 47 | 58.8% | 22 → 16 |

Total visible words: **3150 → 924 (70.7% fewer)**.
Eleven pages exceed 60%. Wiring (59.6%) and Deploy (58.8%) nearly meet it;
the already-empty PCB screen drops 35.7%. These misses are not reported as passes.
Environment has more disclosure controls but much less text. Disclosures intentionally
trade a few controls for access to the complete evidence.

## Evidence and verification

External evidence: `~/.t3/userdata/ui-declutter/`. `before/` and `after/` contain
14 PNGs each and `counts.json`; `after-dark/` contains the 14 dark-theme captures.
`capture.mjs` is the final measurement driver; `capture-initial.mjs` preserves the
initial counter. Mesh loading is not a density improvement or performance qualification.
All captured routes recorded zero uncaught page errors. The source robot was copied
with `cp -R`; its original files and existing servers were not changed.

- `npm run typecheck`: passed.
- `npm test`: 81 tests passed.
- `npm run build`: passed.
- `node --test apps/web/tests/loader.test.mjs`: 4 passed.
- Self-contained browser tests (agent prompt/resume, description diff, empty projects,
  Markdown at desktop/mobile in both themes, native preview, pulled CAD): 14 passed.
- `declutter.spec.ts` against the copied project: 2 passed, including disclosure,
  navigation, severity filtering and inspector interactions, with both-theme coverage.
- Full legacy smoke/cable suite was attempted: this checkout has no default `robot`
  build artifacts; the expected `steering_sweep` recording is absent. Those fixture-dependent
  tests are not claimed as passing. Existing assertions are retained, with clicks added
  for newly hidden content. No robot pipeline, agent work or deployment was started.

Merge base: `r3/ui-hillclimb` plus `r3/viewer-perf`. Only page composition, shared
presentation components, CSS, corresponding browser tests and this document change.
