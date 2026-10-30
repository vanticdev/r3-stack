# Build a robot from nothing

R3 describes a robot in `robot.yaml` and derives its assembly, CAD, wiring, electronics,
software bindings, simulation and release evidence from that specification. A robot may
be a stationary instrument, arm, drone, legged mechanism or mobile platform. Start with
an empty project and add only the components and capabilities your robot needs.

## 1. Create the project

```sh
r3 create sensor-rig --template blank
cd sensor-rig
r3 status
```

The default template is also `blank`. Creating a local project does not create or push
a GitHub repository. The scaffold includes `robot.yaml`, a local `catalog/`, CAD and PCB
folders, a `backend/` for deployable software, agent instructions and a validation workflow.
No reference robot or previous build directory is required.

An Agent-tab Codex session has a network-enabled shell, live web search, `gh`,
normal git and the R3 MCP tools. The user message is passed unchanged. Use
`r3_get_schema(domain="robot"|"component"|"device"|"model")` for the complete
typed authoring reference, including field descriptions and nested definitions
(also `connector` and `wire` for project catalog additions).
Use these tools and this guide; reading platform source is not required.

When the user explicitly requests GitHub publication, initialize a repository
inside the project (`git init -b main`) before staging files, then use `gh repo
create <owner>/<name> --private --source . --remote origin --push`. Verify
`git rev-parse --show-toplevel` names this project, especially when it lives
inside a checkout of R3. Follow the scaffold's `.gitignore`; never stage `build/`
or another repository. The session permits networking and writes to its own
`.git`; other workspace protections remain in effect. Creating the local
scaffold alone does not authorize publication or physical deployment.

For the web interface, build `apps/web` and run `r3 serve --projects-root <parent-directory>`.
An empty project root displays an empty project list. Create a project before adding parts.
The MCP equivalent is `r3_create_project(name="sensor-rig", template="blank")`.

## 2. Describe the task and choose a base

Write down the robot's purpose, workspace, loads, power source, environmental constraints
and measurable success conditions. Requirements belong in `requirements`; dimensions and
poses belong in components and their catalog entries. Select `robot.mobility: stationary`
for a fixed instrument or arm. Mobile robots must declare their actual joints and drives.

When no human is available, make routine engineering choices independently. Choose
conservative, clearly labelled assumptions for unspecified screw lengths, printer build
volume, nozzle, material and process settings. Validate the design against those settings
and include an assumptions list in the final report. Unknown physical dimensions and
unconfirmed purchased-part interfaces remain measurement blockers; never label them verified.
Keep work inside the project and R3 platform sources; do not consult other local robot
projects, old robot git history, worktrees or other agents' session files.

A minimal specification can contain one fabricated base:

```yaml
robot:
  name: sensor-rig
  mobility: stationary
components:
  base:
    type: instrument-base
    pose: {xyz: [0, 0, 0]}
assembly:
  root: base
  joins: []
```

Create `catalog/components/instrument-base.yaml` (or use `r3_put_catalog_component`):

```yaml
components:
  - id: instrument-base
    name: Instrument base
    kind: structure
    mechanical:
      shape: box
      size: [120 mm, 120 mm, 6 mm]
      mass: 0.11
      material: PETG
      density: 1270
      fabricated: true
      provenance: {status: estimated, notes: Initial solid base; replace mass with CAD evidence}
```

This is a geometric starting point. Add holes, load-bearing supports and connection
interfaces for the parts you actually select. Never treat a pose or surface contact as
physical retention.

## 3. Find or source components

Use `r3_list_catalog` and `r3_get_catalog_component` to inspect purchased parts, connectors,
fasteners and materials. Entries retain manufacturer dimensions, model provenance and
explicitly labelled estimates. A part's model, electrical behavior, interfaces and pin
functions must match the exact SKU you will use.

For a part absent from the catalog, declare a requirement-based source slot:

```yaml
components:
  computer:
    source:
      requires: {kind: computer, ram: ">= 4 GB"}
```

Search with `r3 source computer` or `r3_source_search(component="computer")`. Choose a
candidate with `r3 source computer --choose <candidate>` or `r3_source_choose`. The chosen
entry and provenance are written to the local catalog and lock file. Inspect estimated
values and measure any uncertain mechanical interfaces before fabrication.

Use `owned: true` on parts already on hand. They remain in the model but are excluded
from purchasing and sourcing; see [owned parts](owned-parts.md).

For public research, use the session's live web search and shell downloads
(`curl --fail --location URL -o project-file`). Find official datasheets and exact
vendor CAD, not a visually similar SKU. Record the source URL, license, checksum,
dimensions, mass and which values are estimated in the project catalog. Add a real
download with `r3_models_add(name, category, url, license, catalog_component)`;
for an archive also supply `format="stp.zip"` or `"stp.rar"` and its exact `member`.
For a public Onshape model, supply its immutable `/documents/D/v/V/e/E` URL as
`onshape` instead of `url`. A connected browser transport works for local
development as well as API keys. It must be a public document; keep the source
unchanged and insert the supplied version.

`r3_models_search(query)` searches the shared bank plus project records;
`r3_models_fetch(model_id)` downloads a pinned source and reports geometry or a
specific acquisition error. In a project session, added records live under
`catalog/modelbank/` and override the read-only platform bank. These human-editable
R3 records travel with the project. Use the `model` schema when recording reviewed
datasheet specs, model placement and interface frames. Never invent a passing
validation status: measured geometry must support the review. Unverified or
mismatched models remain explicit in CAD diagnostics.

## 4. Join the assembly

Treat assembly as a geometric puzzle: pair compatible connection sites, declare each join, and let the compiler derive poses and check the fit.

Only the root needs an authored pose. Other poses follow joins between catalog interfaces:
hole patterns, shafts and bores, headers and sockets, face mounts, or adhesive pads. An
interface's `origin` lies on the physical seating surface, `z` points outward and `x`
defines clocking. Use actual catalog interface names when writing `assembly.joins`.

The join kinds are `fasten`, `revolute`, `slider`, `ball` and `drive_flat`. Declare screw
type, length, clamped parts, mating threads or nut, grip and locking method as applicable.
A moving join creates a joint. Connect an actuator to it with a `direct` transmission,
or another transmission model appropriate to the declared mechanism. Keep the transmission
path continuous through physically verified connections.

Run `r3 validate --stage assembly,model`. Read the assembly graph and structured diagnostics
under `build/model/`. Resolve `assembly_misfit`, `hole_pattern_mismatch`, `clock_ambiguous`,
`object_interference`, `seat_not_in_contact` and `fit_out_of_range` using their measured
values. See [the assembly model](../architecture/assembly-model.md).

## 5. Build CAD and qualify fabricated parts

Fabricated parts can use boxes, cylinders, prisms, revolutions and spheres, with additive
and subtractive operations. Holes and fits must agree with the interfaces used by the
assembly. Printed fits use the configured process/material tables.

Purchased parts use downloaded manufacturer or library models with checksum, license,
measurement validation and interface frames. Do not sculpt replacements for bought parts.
If no real model exists, an explicitly labelled datasheet proxy remains estimated evidence.

`r3 cad sync` or `r3 validate --stage cad` builds standard editable Onshape features and
assemblies through the official API transport. No FeatureScript is used. Without a CAD
transport, diagnostics identify catalog geometry as estimated. Server rendering uses the
REST shaded-view endpoints.

Create or update your own printed-part type with `r3_put_catalog_component(entry)`.
The `component` schema describes `mechanical.solids` and `mechanical.features`:
additive/subtractive boxes, cylinders, prisms and revolutions, holes and other
supported standard operations. Define connection interfaces at real seating
faces and hole axes; the assembly compiler derives placement from `assembly.joins`.
The same tool adds or overrides a purchased component's electrical and software
metadata. It writes only `catalog/components/` in this project. New connector and
wire types can be authored in the project's `catalog/connectors.yaml` and
`catalog/wires.yaml`, using the `connector` and `wire` schemas and existing
catalog entries as typed examples.

Before building, set `cad.folder` to the user-approved provider folder and
`cad.document_name` to the project name through `r3_patch_spec`. Call
`r3_cad_plan` for a quota estimate before `r3_run_stage("cad")`. The offline plan
does not create or verify geometry. The CAD stage makes the real standard Onshape
features and editable assemblies, then reads native body details, mass, meshes,
fits and mates. Read `cad/cad_model.json` and `cad/report.json` through
`r3_get_artifact`; list `cad/` to find exact artifact names. Use `r3_view_artifact`
on a file under `cad/views/` to see the actual server-rendered image. Read the
numeric diagnostics, edit the design through the tools, and run CAD again; pinned
documents update incrementally. Never edit generated meshes or use an offline
preview as the real model.

Run `r3 validate --stage fabrication` for printable parts. Review wall thickness, overhangs,
loads, strength margins, material orientation and manufacturing artifacts. Tests and
simulation must verify real retention under gravity, vibration and impact where applicable.

## 6. Add power, electronics and wiring

Declare a supply compatible with every connected device, including transient demands,
voltage limits, protection and regulator margins. Model the actual board behavior and pin
maps in the catalog. Do not infer electrical connections from component proximity.

Each harness declares connectors, wires, optional splices and routing constraints. Connector
`mates` fields name exact component or harness endpoints. Wire `from` and `to` references
name connector pins or declared splices. Choose catalog wire gauges and connector types
with compatible current and termination ratings. Direct board-to-board mates are explicit.

Run `r3 validate --stage connectors,wiring,electrical`. Inspect pin maps, conductor exits,
route witnesses, bend radii, dynamic clearances, supported spans, cut lengths, ampacity,
voltage drop, fuse coordination, thermal results and runtime assumptions. The manufacturing
package comes from the routed geometry; a rendered picture alone is not release evidence.

For a custom PCB, add its component to `pcb.boards` and author a `BoardIntent` in the board
source folder. Use official KiCad symbols and footprints. Declare converter designs only
when that board contains them. The headless flow captures the schematic, runs ERC, builds
the board with `pcbnew`, routes with Freerouting, fills zones, runs DRC/parity and exports
fabrication files. `board.json` reports that board's actual connectors, geometry and checks.

## 7. Add firmware and bindings

Deployable source belongs under `backend/<language>/<module-folder>`. One module owns one
complete source folder. Use the scaffolded BLITZ hooks and typed module/process APIs.

Add `software.modules` and bindings between computer pins and the devices actually reached
through the electrical graph. Binding `expects` fields describe intended reachability;
they cannot substitute for a wire. Pin functions, device paths, channels and bus addresses
must agree with the chosen computer and peripherals.

Use `r3_hal` for hardware access. The same firmware can run against physical hardware or
the twin. Simulation time advances through the HAL clock; avoid wall-clock sleeps in control
loops. Configuration belongs in the project, rather than hardcoded component IDs or robot
dimensions in platform code. Firmware can run as a host process or in a BLITZ-shaped container.

## 8. Define and run scenarios

Start with a short observation scenario, then add task-specific motion, loads, environmental
conditions and fault cases:

```yaml
simulation:
  timestep: 1 ms
  seed: 11
  scenarios:
    - id: observe
      duration: 2 s
      initial: {battery_soc: 0.9}
      expect: {brownouts: 0}
```

Use `r3 sim run --scenario observe` or `r3_run_scenario`. Mandatory mechanical load cases
are generated for the declared robot; unsupported robot-type-specific cases emit explicit
diagnostics. Inspect `build/sim/<scenario>/summary.json`, recordings, logs and plots.
MuJoCo, ngspice, native elastic cables, radio propagation and the real firmware advance
in the coordinated twin. Estimated models and fallbacks remain visible in the evidence.

Choose expectations that measure your task: position or joint motion, sensor behavior,
retention, clearance, supply limits, fault response or other supported quantities. Add
requirements as the project grows; do not weaken checks to fit a failing design.

### Project-owned devices and tests

When a device is absent from the platform, implement its bridge in this project's
`simulation/` directory. Do not edit R3 source. Declare it with `r3_patch_spec`:

```yaml
simulation:
  devices:
    instrument:
      component: fixture
      source: simulation/instrument.py
      factory: create_device
      params: {}
```

`factory(context)` returns an object with `step(dt)` and
`request(operation, payload)` methods. `step` runs after each native physics and
circuit step. `request` returns a JSON object, or `None` until data is ready.
`context` provides `t` (simulation seconds), `component`, authored `params`, the
real MuJoCo `model` and `data`, `powered` (from the circuit), and `bindings` (only
electrically reachable interfaces for that component and computer). Use MuJoCo's
public APIs for physical observations and vendor SDK semantics for register or
packet logic. Keep state and clock timing inside the device object. A source path
or symlink that escapes this project's `simulation/` directory is rejected.

For example, a synthetic fixture position readout can use native rigid-body state:

```python
import mujoco

class Instrument:
    def __init__(self, context):
        self.context, self.ready = context, False

    def step(self, dt):
        self.ready = self.context.powered

    def request(self, operation, payload):
        if operation != "position":
            raise ValueError("unknown operation")
        if not self.ready:
            return None
        c = self.context
        body = mujoco.mj_name2id(c.model, mujoco.mjtObj.mjOBJ_BODY, c.component)
        return {"t": c.t, "xyz": c.data.xpos[body].tolist()}

def create_device(context):
    return Instrument(context)
```

Firmware calls `hal.device_request("instrument", "position", timeout=0.1)`.
Waiting advances the same simulation clock as normal HAL sleeps. Missing devices,
unpowered circuits, unreachable software pins and timeouts remain errors. This
generic extension is a virtual-device API: physical operation requires your
project's real vendor-driver implementation. Never substitute simulated data on
physical hardware.

An electrical device may also declare `electrical_factory` in the same device
entry, and use catalog `behavior.model: project.instrument`. That factory has
signature `(component_id, component_type, params, build_context)` and returns an
`r3_sim.electrical.models.BehaviorModel`. For a measured constant-current load,
reuse `r3_sim.electrical.models.ConstantLoad(..., model="project.instrument")`
with the catalog's sourced `current` parameter; for complex circuits integrate
ngspice through that public model API. Do not implement another domain solver.
Device logic cannot replace the native rigid-body, circuit or cable solver, or
change standard checks. Calibration limits and approximation assumptions stay
visible in the report. Device models default to `provenance.status: estimated`;
datasheet or measured provenance requires a nonempty source/calibration reference.
Unqualified behavior stays estimated in release evidence.

Write Python design tests under `tests/test_*.py`. They can assert properties of
the authored spec, run their own native-simulator experiments, exercise the device
factory, or inspect the twin's `build/sim/<scenario>/recording.npz` and summary.
Test real physical quantities and the actual firmware's behavior, not a
hand-authored success flag. Run them with `r3_run_project_tests`,
`r3_run_stage("tests")`, or `r3 validate --stage tests`. Full validation runs this
stage after simulation. Logs and JUnit results are in `build/tests/`; an empty or
entirely skipped suite cannot pass. Standard pipeline checks remain in force.
Firmware, device-source and test edits invalidate older release evidence.

## 9. Iterate to release evidence

The usual loop is `r3_get_spec` → `r3_patch_spec` → `r3_validate` → `r3_diagnose` → fix.
CLI equivalents are editing `robot.yaml`, `r3 validate` and `r3 diagnose`. Build a small
working assembly first, then add power, actuators, sensors, harnesses, firmware and scenarios.

`r3 status --release` (MCP: `r3_release_status`) lists requirements, scenario expectations
and stages with `verified`, `estimated`, `failed`, `stale` or `not_run` evidence.
`fabrication_ready` requires current verified evidence for every applicable item. Review
assumptions and unresolved diagnostics before manufacturing or deployment. Physical
fleet deployment and GitHub publication are separate actions requiring an explicit request.

Custom numeric requirements can bind to a metric that a pipeline stage actually produces:
`requirements.extra.payload_limit: {stage: model, metric: total_mass, op: "<=", value: 2.0}`.
The metric path is relative to that stage's `result.json` metrics, values use SI, and
supported comparisons are `<`, `<=`, `==`, `>=` and `>`. Choose a metric the stage documents
and reports; an absent metric stays `not_run`. Failed, stale or estimated stage evidence
cannot become verified custom evidence. Free-text requirements remain visible and pending
until they have an automatic check; never replace one with a hand-authored passing flag.
