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

For the web interface, build `apps/web` and run `r3 serve --projects-root <parent-directory>`.
An empty project root displays an empty project list. Create a project before adding parts.
The MCP equivalent is `r3_create_project(name="sensor-rig", template="blank")`.

## 2. Describe the task and choose a base

Write down the robot's purpose, workspace, loads, power source, environmental constraints
and measurable success conditions. Requirements belong in `requirements`; dimensions and
poses belong in components and their catalog entries. Select `robot.mobility: stationary`
for a fixed instrument or arm. Mobile robots must declare their actual joints and drives.

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

## 4. Join the assembly

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

## 9. Iterate to release evidence

The usual loop is `r3_get_spec` → `r3_patch_spec` → `r3_validate` → `r3_diagnose` → fix.
CLI equivalents are editing `robot.yaml`, `r3 validate` and `r3 diagnose`. Build a small
working assembly first, then add power, actuators, sensors, harnesses, firmware and scenarios.

`r3 status --release` (MCP: `r3_release_status`) lists requirements, scenario expectations
and stages with `verified`, `estimated`, `failed`, `stale` or `not_run` evidence.
`fabrication_ready` requires current verified evidence for every applicable item. Review
assumptions and unresolved diagnostics before manufacturing or deployment. Physical
fleet deployment and GitHub publication are separate actions requiring an explicit request.
