# Functional inventory

Written before visual work. Sources: route path strings only from `apps/web/src/App.tsx`; `apps/web/src/api/types.ts`, `client.ts`, `connectors.ts`, `sourcing.ts`; `docs/guide/build-a-robot.md`, `owned-parts.md`, `model-bank.md`; repository values. No existing UI markup, styles, screenshots, or app were inspected. Route strings also include settings and a catch-all; these are outside the user's explicit fourteen-page deliverable list. Projects includes creation in the same image.

All mockup values are **illustrative design fixtures**, not live project results, supplier quotes, measured geometry, or verified safety evidence. The RC car uses the user's requested RTK GPS (an exact breakout remains to be selected); the guide's non-RTK GNSS is not silently substituted. API values are SI; readable displays may use mm, minutes, A and V with explicit units. Unknown values remain unknown.

## 01 — Projects
- Purpose: list local robot projects and create a project from a template.
- Main job: resume the correct robot or start a new spec.
- Data: name, description, version, last run and its age, failed/stale stages, project load errors; available templates.
- Actions: open project, search/filter list, choose template and name, create locally; optional explicit GitHub creation.
- Contract: ProjectList, ProjectSummary, CreateProjectRequest. Creation must distinguish local scaffolding from an external GitHub action.

## 02 — Model bank (`models`)
- Purpose: searchable, reusable purchased-part geometry with provenance.
- Main job: find an exact model and judge whether its geometry is suitable.
- Data: manufacturer, MPN, category, STEP source, license, checksum, acquisition date, envelope, validation and feature evidence, interface frames, GLB preview, proxy reason and search history.
- Actions: search/filter, select model, fetch STEP, validate, add source, ingest provider data.
- Contract: model-bank guide HTTP endpoints. Bounding-box agreement alone never means mounting features are verified.

## 03 — Environment (`environment`)
- Purpose: toolchain/server capability report.
- Main job: find what prevents a stage from running here.
- Data: tool available/version/detail, Python/platform/headless capability, stage readiness full/degraded/unavailable, missing prerequisites, agent providers, check timestamp.
- Actions: refresh checks, inspect failure detail and remedy.
- Contract: DoctorPayload, StageReadiness, AgentProviderInfo. Availability does not imply project release readiness.

## 04 — Overview (`p/:pid/overview`)
- Purpose: project readiness and fabrication gate.
- Main job: choose the next blocker to resolve.
- Data: robot identity/revision/fingerprint, component/harness/scenario counts, stage status and durations, current versus stale results, release evidence values/limits, blocking reasons and artifacts.
- Actions: run validation or selected stage, inspect evidence/diagnostic, open spec/report/Onshape.
- Contract: ProjectDetail, Release, ReleaseItem, Evidence. Not-configured blocks; not-applicable is different. No invented completion percentage.

## 05 — 3D model (`p/:pid/3d`)
- Purpose: inspect assembly, purchased and fabricated parts, joints and interference.
- Main job: understand fit, motion and geometry provenance.
- Data: component tree, meshes or labelled proxies, pose, mass and source, joints/limits, contacts/interference pairs and volumes, Onshape source, missing data.
- Actions: select/isolate part, change view, inspect joint, inspect interference, open CAD source, rerun relevant stage.
- Contract: ModelResponse, CadResponse, AssemblyJson, ModelGraph. Geometry and physics coordinates remain explicit.

## 06 — Wiring (`p/:pid/wiring`)
- Purpose: manufacture and validate 3D harness routes.
- Main job: resolve a clearance or bend problem and obtain a cut list.
- Data: harnesses, connector pin endpoints, wire gauge/color/signal, routed and cut lengths, slack, tie points, bend radius, static/dynamic clearance versus requirement, time of worst case, slack playback.
- Actions: select harness/wire, isolate route, scrub slack simulation, inspect connectors, export BOM/cut list, reroute.
- Contract: WiringResponse, RoutesJson, RoutingDetail, SlackSim, ConnectorsJson. Dynamic clearance must not be replaced by static clearance.

## 07 — Electrical (`p/:pid/electrical`)
- Purpose: show ngspice operating points and protection margins.
- Main job: verify power paths under demanding battery/load conditions.
- Data: rail nominal/min/max, sinks, battery SOC, continuous/burst currents and separate limits, connector ratings, AWG ampacity, voltage drop, fuses, brownout/thermal results, runtime versus requirement.
- Actions: choose operating point/battery state, inspect rail/wire/fuse, open evidence, rerun electrical.
- Contract: ElectricalResponse, OperatingPoint, CurrentRow. Show load and limit together; unknown is never zero.

## 08 — PCB (`p/:pid/pcb`)
- Purpose: inspect carrier HAT board and fabrication outputs.
- Main job: verify connector/net mapping and electrical/layout checks before export.
- Data: board outline and renders, connectors and pin/net mapping, BOM, DRC/ERC errors/warnings and source, generated files and missing outputs.
- Actions: select board/layer/net, inspect violation, open KiCad files, download Gerbers, rerun checks.
- Contract: PcbResponse, PcbBoard. A board-level DRC pass is not a project-level fabrication release.

## 09 — Parts (`p/:pid/parts`)
- Purpose: BOM, ownership, sourcing and geometry completeness.
- Main job: know what is on hand, what to buy and which candidate meets requirements.
- Data: component/quantity, owned/sourced/to-buy, manufacturer/MPN, price and currency, unknown-price count, stock/lead time/source date, candidate eligibility and twin checks, STEP/proxy status and stale pins.
- Actions: filter BOM, inspect part, search candidates, choose/unpin candidate, inspect linked model/source.
- Contract: PurchaseSummary, Slot, RankedCandidate, CatalogComponent. Owned Pi 5 4 GB, MPU-6050 and UC-717 stay in all engineering models but cost zero in the purchase total; connectors remain separate purchases. Currency totals stay separate.

## 10 — Software (`p/:pid/software`)
- Purpose: firmware modules and HAL-to-physical-net bindings.
- Main job: trace program I/O to the actual robot and inspect runtime output.
- Data: module/language/target/entrypoint/files, HAL interface/device/pins/net/reached endpoints, config, per-scenario firmware logs and deploy log.
- Actions: select module/binding, inspect mapped pins, open source, choose scenario logs, run software validation.
- Contract: SoftwareResponse, Binding. The same firmware uses virtual HAL in the twin and Linux HAL on the Pi.

## 11 — Simulation (`p/:pid/sim`)
- Purpose: execute scenario tests and inspect synchronized twin evidence.
- Main job: explain a pass/failure in time and space.
- Data: autonomous_course, full_throttle, range_failsafe, low_battery, steering_sweep, top_speed_failsafe, pi_hang; duration/status/freshness; expected and measured checks; 3D bodies/cables; time-series channels with units, events, logs and recording availability.
- Actions: choose/run scenario, play/pause/scrub, select channels, inspect failed check, open recording/report.
- Contract: SimListResponse, ScenarioResponse, PlaybackRecording. Label simulator/fallback provenance; every plotted trace serves a check.

## 12 — Diagnostics (`p/:pid/diagnostics`)
- Purpose: complete findings ledger across stages.
- Main job: identify and resolve a specific actionable failure.
- Data: severity, stable code, stage, subject, message, numeric data with units and threshold, hints, counts and input revision.
- Actions: search/filter/sort, select finding, jump to subject/evidence, copy details, rerun affected stage.
- Contract: DiagnosticsResponse, Diagnostic. Do not hide warnings or collapse different causes into a single health score.

## 13 — Agent (`p/:pid/agent`)
- Purpose: collaborate with the AI editing the robot spec and running tools.
- Main job: give an instruction, follow evidence and review the resulting changes.
- Data: sessions, provider/model, user/assistant markdown, tool calls/results, run events, spec changes/diff, status/errors and token usage when available.
- Actions: choose provider/session, send prompt, cancel running agent, expand tool output, inspect diff and evidence.
- Contract: AgentStatus, AgentEvent. Markdown supports headings, lists, code and tables. Tool completion and validation success remain separate.

## 14 — Deploy (`p/:pid/deploy`)
- Purpose: deploy the compatible bundle to an explicit simulation or physical-fleet target.
- Main job: understand destination, run preconditions and inspect deployment outcome.
- Data: target sim/fleet, selected scenarios, release blockers, command/run status/log/return code, current project revision.
- Actions: select target, deploy simulation, explicitly confirm physical deployment, inspect logs and cancel supported runs.
- Contract: DeployRequest, DeployResponse, RunInfo and values.md. Never invent live device inventory from offline metadata. Keep simulation visibly separate from physical fleet, with real fleet discovery handled by the deployment flow.
