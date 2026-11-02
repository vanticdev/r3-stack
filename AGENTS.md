# AGENTS.md

## Repository overview

BLITZ is a multi-process robotics stack. The Python deployment tooling discovers
target systems, builds architecture-specific bundles, syncs them to the fleet,
and assigns typed process plans. The watchdog runs those processes on each
target. The v1 platform folders reserve space for robot design, integration,
simulation, and a single web application; they contain documentation only.

## Project layout

- `apps/web/`: the single web interface for the robotics platform (Vite + React +
  three.js; talks only to the `r3 serve` HTTP API).
- `services/`: planned orchestration and simulation worker entrypoints.
- `packages/`: planned robot model, component catalog, wiring, simulation, and
  hardware interface libraries.
- `integrations/`: planned adapters for external CAD, electronics, and GitHub tools.
- `examples/robots/`: reserved for versioned example robot projects.
- `docs/architecture/`: platform boundaries and repository layout.
- `docs/deployment/`: existing deployment and watchdog documentation.
- `backend/`: deployment, bundling, compilation, networking, and module APIs.
- `watchdog/`: target-side discovery, monitoring, process management, and routes.
- `proto/`: protobuf source definitions.
- `scripts/`: setup, deployment, runtime, and installer scripts.
- `__tests__/`: repository-level Python tests.

See [repository layout](docs/architecture/repository-layout.md) for ownership
and the distinction between existing functionality and reserved folders.

## Working rules

- Read [values.md](values.md) before making design or behavior changes. Use its
  principles to guide decisions and identify regressions; it records intended
  behavior and explicitly notes known implementation gaps.
- Preserve the public deployment hooks in `backend/deploy.py`:
  `get_modules()` and `pi_name_to_process_types()`.
- All module source folders and assets belong under
  `backend/<language>/<module-folder>` (or the configured installer backend root).
  One module owns one complete source folder; never declare multiple modules
  for different files in the same folder.
  Prefer `BackendModules(__file__)` declarations by module name over manually
  constructing backend source paths in deployment examples and new code.
  Shared source belongs in the language's `common/` folder. Do not expose sibling
  Python modules as import paths; native dependencies use compiled exports.
- Prefer typed Python APIs such as `ProcessPlan`, `WeightedProcess`, and
  `SupportedModules` over ad hoc dictionaries or new config formats.
- Keep deployment behavior compatible with both local Python execution and the
  existing installer/Gradle integration unless a change explicitly replaces it.
- Build the future product in the reserved v1 folders. Keep a single web app in
  `apps/web/`; the standalone marketing site has been removed. Shared platform
  packages are distinct from deployable robot modules under `backend/`.
- Automatically update `backend/deployment/.blitz-build-version` when making
  substantive non-test changes to backend, deployment, compilation, watchdog,
  runtime, installer, or protocol behavior. Do not wait for a separate request.
  Bump the version once per cohesive change: patch for compatible fixes, minor
  for compatible features, and major for breaking API or bundle changes.
  Test-only, documentation-only, formatting-only, and website-only changes do
  not require a deployment version bump. Preserve any version bump already
  made for the same change.
- Do not hand-edit generated protobuf files in `watchdog/generated/`; update the
  definitions in `proto/` and run `make generate`.
- Keep platform-specific build logic inside the existing compilation adapters.
- Never commit secrets, local system identities, virtual environments, build
  artifacts, caches, or machine-specific paths.
- Preserve unrelated work in the repository and keep changes scoped to the task.

## Automatic commits

- Automatically create a local Git commit when a completed task makes a
  substantial, cohesive change, unless the user asks to leave it uncommitted.
  Do not wait for a separate commit request.
- Substantial changes include features, meaningful bug fixes or refactors,
  repository restructuring, and coordinated documentation changes. Judge by
  impact and completeness rather than a fixed file or line count. Isolated
  typo, formatting, or minor documentation edits do not require a commit.
- Run the applicable validation first and review the diff and staged files.
  Commit only changes belonging to the task, including required version bumps;
  preserve unrelated user edits and staged work. Do not commit incomplete work
  or changes with unresolved validation failures; report any validation blocker.
- Use a concise commit message describing the completed change, and report the
  commit hash in the final response. Automatic commits do not authorize pushing,
  amending existing commits, or rewriting history.

## Validation

- Run targeted Python tests while iterating:
  `.venv/bin/python -m pytest path/to/test_file.py`.
- Run `make test` when changing shared deployment or watchdog behavior.
- Run `make generate` after changing protobuf definitions.
- Web app (`apps/web/`, Node >= 22.18), from `apps/web/` after `npm ci`:
  - `npm run typecheck`: strict TypeScript over the app, the mock layer and the e2e tests.
  - `npm test`: vitest unit tests for the data transforms.
  - `npm run build`: production bundle in `apps/web/dist`, which `r3 serve` serves.
  - `npm run test:e2e`: Playwright headless-Chromium smoke test. It loads every page
    against the build/-artifact mock API, asserts there are no console errors, and saves
    screenshots to `apps/web/test-results/screenshots/`. Set `R3_E2E_BASE_URL` to test a
    running `r3 serve`.
  - `node --test apps/web/tests/loader.test.mjs`: also run by
    `tests/test_orchestrator_report.py`.

## Style

- Follow existing Python and shell conventions in the surrounding files.
- Add type annotations to new Python interfaces and keep functions focused.
- Prefer clear domain names over abbreviations, and document behavior that is
  not obvious from the code.

## Autonomous integration loop: persistence (direct user instruction)

For the autonomous R3 integration loop, keep working until the requested result
is achieved or a genuine user-only blocker applies. Latest user override: if an
approach requires two hours or more of implementation work, abandon that approach
immediately and find a workaround. Agent runtime is excluded. Major roadblocks
must not prevent a working prototype: prefer native meshes and native placements
and physics when exact exports are unavailable; keep unperformed qualification
visible. STEP is not intrinsically required for rendering or simulation.
Verify reported backoffs against actual response evidence and the transport
implementation; do useful independent work while a confirmed restriction is active.
The user authorizes editing onshape-native source when a generic platform fix
needs it. Preserve unrelated plugin work and keep the loop's CAD, independence,
security, validation and cleanup rules. Preserve and steer the same project and
agent conversation on failures. Do not fabricate CAD, weaken numeric limits or
present deferred checks as passed. Prioritize a working model before refinement.

For integration-loop failures, retain the project and provider conversation.
Diagnose and fix the generic cause, then steer the same agent to retry. Do not
wipe its resources or start a fresh run merely because an attempt failed.
Record follow-ups and code-version boundaries so the final evidence remains honest.
