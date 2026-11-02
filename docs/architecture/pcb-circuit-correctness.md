# PCB correctness audit and implementation

Audit date: 2026-11-01. R3 base: daa196073db38819f1d9f5b9e4d24f5569d66f41.
SPIKE reference: 77e75895cf76f834c166af22db66766a38898075.

## Scope and observed evidence

The current robot projects do not provide a failing native PCB artifact to
reproduce the reported appearance: the inspected My_rover and Rover2 PCB result
files say not_applicable (no custom boards). Neither KiCad nor libngspice is
installed in the inspected R3 environment. Consequently this audit does not
claim a before/after native board image, successful native ERC/DRC, or qualified
nonlinear circuit execution. The changes are tested contracts and numerical
building blocks; manufacturing acceptance remains unverified.

The prior 3-D harness-routing changes concern physical cables. PCB traces use
KiCad/Freerouting, not the harness voxel solver. They must remain separate.

## Current architecture

1. `robot.yaml` declares `pcb.boards`. A board folder contains Python design
   intent (`r3_kicad.intent.BoardIntent`) with official symbols/footprints,
   pin-to-net maps, explicit footprint placement, net classes, power copper,
   zones and mounting constraints. This is not a general PCB auto-placer.
2. `r3_kicad.flow.Flow` writes a schematic through kicad-sch-api, normalizes it
   with KiCad, exports the XML netlist and compares it with the intended nets.
3. `pcb.build_plan` joins the KiCad netlist to placement/copper intent.
   `kicad_scripts/board_build.py` runs in KiCad's Python, loads footprints,
   assigns actual pad nets, places/flips/rotates footprints, and adds copper.
4. `freerouting.py` exports DSN, runs the Java router and imports SES. The flow
   retries routing, adds/fills zones and performs final schematic-parity DRC.
   Multi-layer/via support in this product is bounded to its two-copper-layer
   board setup; it is not an arbitrary stackup implementation.
5. `stage.py` exports fabrication data, KiCad 3-D PNGs and copper SVGs, reads
   the board back and writes `build/pcb/<board>/board.json`. It checks catalog
   pin maps, regulator sizing and approximate conductor/pour ratings.
6. `api.projects.pcb_view` exposes artifacts and result state to `PcbPage.tsx`.
   That page displays KiCad-produced images; it is not a separate trace renderer.
7. `r3_sim.electrical.ElectricalSystem` builds ngspice subcircuits from declared
   behavior models, harness resistances and explicit internal links. `BoardPcb`
   models declared regulators/loads; it does not import the schematic or extract
   all physical trace RLC. The electrical stage computes robot operating points.

Frames: KiCad uses board-local millimetres, x right/y down, translated by page
origin. `BoardIntent.to_frame/from_frame` convert to component metres with y up.
`board_dump.py` reads absolute pad positions from KiCad, so footprint transforms
are owned by KiCad. Connector checks currently compare pad centroids, not every
3-D pin pose. Bounding boxes must never prove conductive contact.

## Confirmed defects and implemented corrections

| Finding | Evidence before change | Correction |
| --- | --- | --- |
| Unplaced footprints admitted | Removing every placement from the coupon still returned `validate() == []`; builder leaves missing placements at defaults | Placement coverage, finite values, board-origin bounds, sides and basic via/track geometry are validated before generation |
| Incorrect deliberate copper endpoints | Track resolver located a named pad without checking its net | Intent and native builder both reject missing/wrong-net endpoints |
| Infeasible anchor positions | `place()` selected the least-bad rotation without rejecting residual | Missing anchor pads and residual above 0.01 mm fail the board build |
| DRC was recorded rather than gated | `Flow.drc()` returned counts even with errors/unconnected items | Failed invocation, absent report, violations, unconnected items and parity prevent flow success |
| Routing selected on open-count alone | Fewer open nets could win despite copper DRC errors | Routing attempts/rounds use copper-error count before open count; final DRC remains mandatory |
| Stale check report risk | Existing ERC/DRC JSON could survive a failed invocation | Delete generated report before invocation, require successful execution and a fresh report |
| Wrong image chosen | Broad top/bottom regex selected sorted `copper_top.svg` before `render_top.png` | Explicit filename selection and separate 3-D/Copper controls; no cross-view fallback |
| False pin-map matches | Missing catalog entry or arbitrary name suffix was accepted | Match the backend's exact pin/net contract; unexpected pins fail |
| Historical evidence looked current | Old board.json remained viewable after newer failed builds | Store input fingerprint, expose evidence_current and suppress current-pass presentation for failed/stale/unbound results |
| Missing copper geometry was silent | Board readback only handled PCB_TRACK and PCB_VIA | Unsupported copper objects are reported and block reliance on derived readback checks |
| Unparseable resistor value could evade check | `_value("4k7")` returned NaN and NaN comparison did not fail | Parse embedded decimal engineering values and fail nonfinite value checks |
| Board assignments not independently compared | Schematic intent comparison and visual pin labels did not independently inspect all saved pads | Compare saved pad/net identities to the exported netlist, preserving repeated physical pads for one pin |

These corrections prevent unqualified outputs from looking successful. They do
not optimize all component placements, prove package/courtyard non-overlap without
KiCad, or establish actual manufactured electrical connectivity by themselves.

## SPIKE assessment

Reference repository: https://github.com/wayri/SPIKE

Relevant source boundaries inspected:

- `python/spike_core/contracts.py`: neutral board, analysis and result contracts
  with units, issues, model status and provenance.
- `python/spike_core/kicad_importer.py`: native geometry/source identity ingestion.
- `python/spike_core/topology_circuit.py`: explicit model handoff, unsupported
  device rejection; a visual graph is not itself an executable circuit.
- `python/spike_core/native_mna.py`: linear modified nodal analysis, dense/sparse
  backend selection, DC/AC and fixed-step backward Euler, residual/conditioning
  checks and explicit model limitations.
- `src/spikes/dc_solver.cpp`: sparse LU/QR and iterative solver paths with residual
  gates; distinct from the Python linear reference.
- `src/spikes/transient_solver.cpp` and `transient_session.cpp`: separate native
  transient ownership. This audit does not qualify these C++ engines.
- `docs/SOLVER_STATUS.md`: implemented, experimental and unqualified capabilities
  are distinguished rather than equated with registered adapters or UI plots.

Executed directly against SPIKE's Python linear engine: 10-V equal-resistor
divider gave 5 V; a 1-kohm/1-uF RC network at its corner gave magnitude
0.7071067811865476. These are two analytical reference checks, not qualification
of SPIKE's complete solver suite. No native SPIKE C++ engine was built here.

Directly reusable ideas: explicit models, validation/result contracts, separate
execution status and model scope, residuals and provenance. Adaptation required:
mapping R3 catalog identities and run/artifact APIs to a circuit contract. A
wholesale desktop/native solver replacement, multiphysics merger or assumption
that SPIKE supplies a PCB auto-placer is not justified. SPIKE-owned material is
Apache-2.0 licensed, with separately licensed dependencies. No SPIKE source was
copied into R3 in this change.

## Circuit analysis added

`r3_kicad.circuit` owns explicit linear lumped R/C/L and independent voltage/current
sources, with SI parameters and node 0 as ground. The stamp equation is
`G*x + C*dx/dt = b`. DC solves G, AC solves G+j*w*C, and transients use fixed-step
backward Euler from a DC operating point. Sparse LU executes solves, with matrix
conditioning and backward-residual gates. Unsupported models, floating/singular
systems, nonfinite values and admission-budget violations return blocked results.
Source discontinuities become time points; event samples use the left limit.

The shared `api.analyze_circuit`, HTTP `POST /api/projects/{pid}/circuit` and MCP
`r3_analyze_circuit` expose the same operation. Each run saves the explicit request,
full request hash, unique run identity, input fingerprint, time, numeric evidence
and limitations under build/circuit. It never silently imports a board's schematic
or infers a semiconductor model from a footprint.

Example MCP circuit payload:

```json
{"mode":"dc","elements":[
  {"id":"V1","kind":"V","positive":"in","negative":"0","value":10},
  {"id":"R1","kind":"R","positive":"in","negative":"out","value":1000},
  {"id":"R2","kind":"R","positive":"out","negative":"0","value":1000}
]}
```

This is not nonlinear-device simulation, PCB parasitic extraction, transmission
line analysis, full-wave EM, detailed thermal analysis, or an adaptive integrator.
The existing ngspice behavior-model path remains separate. A completed linear
analysis does not establish that a PCB implements that circuit.

## Diagnostics and debugging

`r3_model.work_units.debug_work_units` is a shared deterministic grouping view.
Only explicit cause_id metadata establishes a causal relation. Other shared
subjects are labeled related_context with cause_status not_established. A finding
can appear in several contextual groups; exact duplicate evidence is retained
with occurrence counts. Each group preserves technical evidence, severity,
stages, subjects, recommended actions and verification criteria.

Stage JSON stores work_units; stored_diagnostics and diagnose expose them to
agents/HTTP. The UI now reveals diagnostic data rather than hiding the technical
payload. Native flow errors retain step/type/message/traceback; DRC summaries
preserve full violations in structured data rather than only truncated hints.
This is not a durable incident database: no automatic verified-resolution or
reopen lifecycle is claimed. Agent completion is not evidence of a fix.

## Verification and remaining gates

New tests cover missing placements, wrong-net track endpoints, saved-pad netlist
mismatches, stale/failed DRC, numerical parsing, explicit-cause vs contextual
grouping, divider/power balance, RC AC response, timestep-refined RC transients,
unsupported/singular models, and project artifact provenance. Frontend tests cover
render selection and exact connector pin matching.

Recorded validation for this change:

- Combined PCB, circuit, wiring, electrical-model, agent, MCP and HTTP API
  regression run: 89 passed, 23 skipped. Socket-dependent HAL tests were run
  outside the restricted sandbox. Skipped checks are not successes.
- After adding an admission-limit regression for an overflowing timestep ratio,
  the circuit analysis module's six tests passed.
- Web unit tests: 75 passed; TypeScript check and production build passed.
- Isolated Playwright PCB regression: 1 passed against its temporary mock server.
  It tests view selection and evidence presentation, not native board geometry.
- Full repository `make test` with fail-fast: 5 passed, then the existing native
  Codex configuration test failed because the installed CLI rejects
  `app-server --print-startup-config`. The full suite is not green or completed.
- Broader browser run: 6 passed, 8 failed, 1 interrupted and 12 not run. Existing
  fixture assumptions conflicted with populated projects and a missing default
  robot. User projects were preserved; checks were not weakened to hide this.

No native KiCad board export, rendering or ERC/DRC, libngspice comparison,
manufacturing validation or SPIKE C++ solver qualification was performed.

Next native acceptance gate: install the declared KiCad/Freerouting toolchain and
libngspice in a controlled environment, build the passive filter coupon and a
larger declared board, inspect both PNG/Copper views, require fresh ERC/DRC/parity
reports with zero unconnected items, and compare explicit circuit models against
ngspice. Missing tools produce skipped/blocked checks, never a fabricated pass.

Follow-up scope: complete arbitrary-outline/arc/pad-shape readback, placement
optimization based on real courtyards and circuit intent, PCB-netlist-to-model
binding with explicit model coverage, nonlinear model qualification, persistent
issue lifecycle, and broader physical benchmarks. Until those gates pass, this
work must not be described as a qualified general-purpose PCB design environment.
