# Build a robot from nothing with R3

This guide is for an agent or a person starting from an empty folder. You describe the robot once in
`robot.yaml`. R3 then derives the assembly, CAD, printed parts, wiring, electrical checks and firmware
deployment, and it runs your real program against a digital twin. Every stage reports diagnostics with
stable codes and numbers. Iterate until `r3 status --release` says `fabrication_ready: true`.

The MCP tool `r3_guide` returns this file. The worked reference robot is `examples/robots/rc-car`, which you
can read for patterns but should not copy wholesale.

## 0. Policies you must follow

- **robot.yaml is the single source of truth.** Edit it with `r3_patch_spec` (pass the `sha256` you read)
  or by hand. Never edit anything under `build/`, which is generated.
- **Fix the design, not the check.** Never weaken a requirement, threshold or check to make it pass.
  Change a requirement only when physics makes it impossible, and then say so in a YAML comment.
- **Provenance on every number.** In catalog entries, use `status: datasheet | vendor | measured |
  estimated | derived` with a `source`. Label estimates as estimates. The release gate accepts an
  estimate only if it is labelled, sourced, and passes with at least 20 % margin.
- **CAD uses standard Onshape features only:** sketches, extrude, revolve, hole, fillet, chamfer,
  patterns, mirror, boolean, mate connectors and assemblies with standard mates. FeatureScript, Feature
  Studios and custom features are forbidden. R3 enforces this, so do not work around it.
- **Use each tool the way its own UI would.** Use Onshape through its API, KiCad through `pcbnew` and
  `kicad-cli`, and MuJoCo and ngspice through their libraries. Never hand-write a tool's internal file
  format.
- Purchased parts are never sculpted. Search `r3 models search` (MCP `r3_models_search`) first. On a miss, search public Onshape versions, official vendor STEP, then permitted open model libraries; add the find with `r3 models add` / `r3_models_add`, including provenance and checksum. Only a documented exact-model miss permits a labelled datasheet proxy (`cad_purchased_proxy`). Validate bounding boxes, holes/faces, interface frames and datasheet mass before fabrication. See `docs/guide/model-bank.md`.
- **Everything runs headless.** No GUI is needed. `r3 doctor` tells you which stages can run here.
- **Real simulators only.** The twin uses MuJoCo (rigid bodies, cables, camera rendering), ngspice
  (circuits) and Sionna RT (radio). Do not write a replacement solver.
- **Ask a human before any external action.** Fleet deployment (`deploy_fleet`) and creating a GitHub
  repository (`create_github_repo`) need `confirm=true` and an explicit human request. `r3 create` never
  touches GitHub.

## 1. Create the project

```bash
r3 create my-car --template blank --dir ~/robots     # MCP: r3_create_project(name="my-car", parent="~/robots", template="blank")
cd ~/robots/my-car && r3 validate                    # every stage says what it still needs (not_configured)
```

The project layout:

| Path | Contents |
| --- | --- |
| `robot.yaml` | The robot. |
| `catalog/components/*.yaml` | Your own parts. |
| `backend/python/<module>/` | Firmware. |
| `r3.lock.yaml` | Pinned revisions and sourced parts. |
| `build/` | Generated output. |

Stage results mean the following:

- `ok`: the stage passed.
- `failed`: the stage found errors.
- `not_configured`: the spec has nothing for this stage yet. The hint says what to add. The release gate
  counts such a stage as not run.
- `not_applicable`: there is nothing to build, for example `pcb` with no custom board. The stage passes.

## 2. The spec (`robot.yaml`, schema `r3/robot@1`)

Units are SI floats or strings with units (`25 mm`, `30 deg`, `5000 mAh`). The robot frame is +x forward,
+y left, +z up, with the origin on the ground.

```yaml
schema: r3/robot@1
robot: {name: my-car, version: 0.1.0}
requirements: {max_mass: 2.5 kg, top_speed: 8 m/s, min_runtime: 10 min, failsafe_stop_distance: 3 m}
components:            # id -> catalog type; only the assembly root has a pose
  frame: {type: my-frame-petg, pose: {xyz: [0, 0, 0.03]}}
  pi: {type: raspberry-pi-5-4gb}
assembly: {root: frame, joins: [...]}          # section 4
electrical: {harnesses: [...], direct_mates: [...]}   # section 7
software: {modules: [...], bindings: {...}, config: {...}}   # section 9
simulation: {environment: {...}, scenarios: [...]}  # section 10
fabrication: {printer: bambu-x1c}              # section 6
```

## 3. Parts: the catalog and sourcing

- **Find parts.** Use `r3_list_catalog(kind=..., query=...)`. The kinds are computer, battery, motor,
  esc, servo, regulator, sensor, camera, cable, radio, wheel, gearbox, structure, and also
  connector/wire/material/printer. Use `r3_get_catalog_component(id)` to see an entry in full: its
  interfaces, pins, connectors, behaviour model and provenance.
- **Off-the-shelf parts with datasheet provenance:**
  - Computers: Raspberry Pi 5 in 1/2/4/8/16 GB. There is no 5 GB model, so confirm the RAM with the
    owner.
  - Camera: Arducam B0445 IMX296 (board UC-717), with 22-22 and 15-22 CSI flat cables.
  - Drivetrain: the Traxxas Slash 2WD driveline. This is the Magnum 272 transmission, Titan 12T 550
    motor, half-shafts with a 12 mm hex, 5x11x4 bearings, SCT wheels, front axles and M4 nuts.
  - Electronics: Savox SC-0251MG servo, Hobbywing QuicRun WP 1060 ESC (6 V / 3 A BEC), Pololu
    D36V50F5 5 V / 5.5 A buck for the Pi (no custom PCB needed), SparkFun MAX-M10S GNSS, Gens Ace 2S
    5000 mAh XT60 LiPo, and the GY-521 MPU-6050 breakout.
  - Parts from the reference robot: an ELRS receiver, a u-blox M10 module and fasteners.
  - Many drivetrain dimensions are **estimated**, because Traxxas publishes no drawings. The entries say
    so. Measure the real parts and override them in `catalog/`.
- **Parts that are not in the catalog.**
  1. Declare the slot by requirements instead of by `type`:
     `gps: {source: {requires: {kind: sensor, protocol: nmea-0183, supply_voltage: 3.3 V}, prefer: {price: low}}}`
  2. Search with `r3_source_search(component="gps")` (CLI: `r3 source gps`). This ranks real candidates
     with twin checks. The `catalog` provider offers the bought parts R3 already models (with their
     datasheet provenance). Choosing one of those pins the existing entry; nothing is generated. The
     other providers (Raspberry Pi, ExpressLRS, Nexar, DigiKey, Mouser, agent research) build a new
     entry.
  3. Pin a candidate with `r3_source_choose(component="gps", candidate="1")` (CLI:
     `r3 source gps --choose 1`). This writes `catalog/components/sourced_*.yaml` and `r3.lock.yaml`.
- **Your own parts.** Write them with `r3_put_catalog_component(entry)`, which validates the schema and
  writes `catalog/components/<id>.yaml`. Use this for every printed part, and for bought parts you add
  yourself (with provenance).

## 4. Joins and interfaces (puzzle-piece assembly)

Only the root object has a pose. Every other pose is derived by joining **interfaces** that match:

- hole patterns with hole patterns;
- shafts with bores;
- headers with sockets;
- connector bodies with each other;
- adhesive pads and face mounts.

Each interface has an origin on the part surface, `z` as its **outward** normal (or the axis out of a
shaft or bore), and `x` as the clocking reference.

```yaml
assembly:
  root: frame
  joins:
    - {a: frame.pi_1, b: pi.h1, fasteners: {type: M2.5x5}}           # one hole per seat; the other 3 close loops
    - {a: frame.camera, b: camera.mount, clock: 0 deg, fasteners: {type: M2x5}}   # symmetric pattern: give clock
    - {a: camera.csi, b: csi_cable.end_a}                               # flat cable rides on the camera connector
    - {a: frame.gearbox, b: gearbox.mount, clock: 0 deg, fasteners: {type: M3x10}}
    - {a: gearbox.motor_mount, b: motor.face, clock: 0 deg, fasteners: {type: M3x8}}
    - {a: gearbox.pinion, b: motor.shaft, kind: drive_flat}
    - {a: gearbox.output_l, b: halfshaft_l.output, kind: revolute, joint: spin_rl,
       verify: {kind: drive_flat, rotating_bore: true}}
    - {a: carrier_l.bearing, b: bearing_l.outer, fit: interference}     # bearing pressed into a printed carrier
    - {a: bearing_l.inner, b: halfshaft_l.bearing_seat, kind: revolute}
    - {a: halfshaft_l.hex, b: wheel_rl.hub, kind: drive_flat}           # 12 mm hex drive
    - {a: halfshaft_l.nut_thread, b: nut_rl.seat, fasteners: {type: M4x5, clamps: [wheel_rl]}}
```

The kinds of join are `fasten` (the default), `revolute`, `slider`, `ball` and `drive_flat`. Moving joins
create `mechanical.joints`. You then add `mechanical.transmissions`, for example:

```yaml
mechanical:
  transmissions:
    - {id: drive, type: differential, actuator: motor, joints: [spin_rl, spin_rr], ratio: 10.17}
    - {id: steer, type: ackermann_steering, actuator: servo, joints: [steer_fl, steer_fr]}
```

Run `r3 validate --stage assembly`. Read `build/model/assembly_graph.png` and fix these codes:

- `assembly_misfit`
- `hole_pattern_mismatch`
- `clock_ambiguous`
- `object_interference`
- `seat_not_in_contact`
- `fit_out_of_range`

The full reference is `docs/architecture/assembly-model.md`.

## 5. Printed parts and CAD

A printed part is a catalog entry like this:

```yaml
id: my-frame-petg
kind: structure
mechanical:
  shape: custom
  size: [...]                   # bounding box [x, y, z]
  mass: 0.12
  material: PETG
  density: 1270
  fabricated: true
  solids:
    - {kind: box, name: plate, position: [0, 0, 2 mm], size: [300 mm, 120 mm, 4 mm]}
    - {kind: box, name: camera wall, position: [146 mm, 0, 29 mm], size: [8 mm, 50 mm, 50 mm]}
interfaces:                     # holes are CUT from the interfaces the joins use
  pi_1: {kind: hole_pattern, origin: [-49 mm, -24.5 mm, 4 mm], z: [0, 0, 1], params: {holes: [[0, 0, 2.5 mm, tapped, 4 mm]]}}
  camera: {kind: hole_pattern, origin: [150 mm, 0, 29 mm], z: [1, 0, 0], x: [0, 1, 0],
           params: {holes: [[15 mm, 15 mm, 2 mm, tapped, 8 mm], [-15 mm, 15 mm, 2 mm, tapped, 8 mm], [15 mm, -15 mm, 2 mm, tapped, 8 mm], [-15 mm, -15 mm, 2 mm, tapped, 8 mm]]}}
```

- **Solids.** The primitives are box, cylinder, prism (a 2D profile), revolve and sphere, each with
  `op: add|remove`. Hole and bore sizes in printed parts follow the FDM print-fit table
  (`print_fits.yaml`), not metal ISO fits.
- **CAD stage.** `r3 cad sync`, or `r3 validate --stage cad`, builds every object as its own Part
  Studio in Onshape and the robot as an assembly with standard mates. The transport is Onshape API keys:
  set `ONSHAPE_ACCESS_KEY` and `ONSHAPE_SECRET_KEY`. Without a transport the stage warns
  `cad_geometry_estimated` and the other stages use catalog geometry. Images come from Onshape's REST
  shaded views (`build/cad/views/*.png`).

## 6. 3D printing (`fabrication` stage)

The fabrication stage checks every part that is `fabricated: true` and made of an FDM material. The
materials are PETG, PLA+, PLA, ASA and PA-CF (`r3_list_catalog(kind="material")`). The stage does the
following:

- **Fit on the printer.** It checks the part against the printer's bed: `fabrication.printer`, for
  example `bambu-x1c` (256 mm cube) or `prusa-mk4s`; see `kind="printer"`.
- **Orientation.** It suggests a print orientation.
- **Printability.** It checks:
  - overhangs beyond `max_overhang_deg`;
  - minimum wall thickness;
  - tapped holes in plastic, where it recommends a heat-set insert size;
  - nut traps and screw-head counterbores.
- **Strength.** It runs scikit-fem linear elasticity under bump and crash load cases
  (`fabrication.load_cases`, in g) and reports a safety factor against `min_safety_factor`. Strength is
  checked across layers when the load does.
- **Export.** It writes `build/fabrication/<part>/<part>.stl` and `.3mf` in print orientation, plus
  `plan.json`.

Per-part overrides go in `fabrication.parts.<id>: {material, orientation, print: false}`. The codes
(`print_bed_exceeded`, `print_overhang`, `print_wall_thin`, `printed_tapped_hole`, `nut_trap_too_shallow`,
`printed_hole_clearance`, `print_strength_margin_low`, ...) are listed in `packages/fabrication/README.md`.
Material and print-fit lookup matches whole words: write `PETG`, `PLA+`, `ASA`, `PA6-CF` or `PAHT-CF` in
`mechanical.material`.

## 7. Wiring and connectors

- **Harnesses.** Real wires are harnesses. Each harness has connectors (`mates: <component>.<connector>`),
  wires (`from`/`to` pins, `awg`, `color`, `signal`) and route hints (`via`, `tie_points`, `slack`,
  `keep_away`). A part with flying leads has `integral` connectors, so its lead is a harness. The Pi
  header is reached with a `socket-2x20-2.54` harness connector.
- **Direct mates.** Use `electrical.direct_mates` (`{a: camera.J1, b: csi_cable.A}`) for parts that plug
  straight into each other: CSI flat cables and HATs on headers.
- **Wiring stage.** `r3 wiring route` routes every harness in 3D around the CAD and simulates the slack
  as MuJoCo elastic cables. It checks clearance to moving parts, bend radii, cut lengths and the
  manufacturing release.
- **Connectors stage.** It places every pin and checks the mated pairs.

## 8. Electrical

`r3 validate --stage model,electrical` covers the following:

- pin roles (one driver per rail, TX to RX);
- voltages and logic levels;
- I2C addresses;
- binding reachability;
- static worst-case operating points solved in ngspice: wire drops, fuse and connector ratings, brownout
  margins, and thermal behaviour.

To power a Pi 5 from a 2S pack without a custom PCB, run battery → Pololu D36V50F5 (`VIN`) → `VOUT` →
Pi J8 pins 2/4 (5 V) and 6 (GND). Servos run from the ESC's BEC lead.

## 9. Firmware with `r3_hal`

A module lives in `backend/python/<name>/main.py`, is declared in `software.modules`, and uses only
`r3_hal`. The same program runs on the Pi (`R3_HAL=linux`) and in the twin (`R3_HAL=virtual`). Bindings
in `robot.yaml` name the pins and devices:

```yaml
software:
  modules: [{name: controller, language: python, runs_on: pi, entrypoint: main.py}]
  bindings:
    esc_pwm:      {interface: pwm, computer: pi, pins: [pi.J8.12], device: pwmchip0, channel: 2, frequency: 50 Hz, expects: [esc.J_SIG.1]}
    i2c:          {interface: i2c, computer: pi, pins: [pi.J8.3, pi.J8.5], device: /dev/i2c-1, expects: [imu.J1.4, imu.J1.3]}
    gps:          {interface: uart, computer: pi, pins: [pi.J8.8, pi.J8.10], device: /dev/ttyAMA0, baud: 38400, expects: [gps.J1.2, gps.J1.3]}
    front_camera: {interface: csi, computer: pi, pins: [pi.CAM0.21, pi.CAM0.20], device: cam0, expects: [camera.J1.21, camera.J1.20]}
  config: {control_rate_hz: 100}
```

```python
from r3_hal import open_hal

hal = open_hal("controller")
b, clock = hal.bindings(), hal.clock          # bindings come from robot.yaml in the twin
esc = hal.pwm(b["esc_pwm"]["device"], b["esc_pwm"]["channel"])
cam = hal.camera("cam0", width=640, height=480, fps=30)   # libcamera/picamera2 on the Pi, MuJoCo render in the twin
while True:
    frame = cam.capture()                     # blocks until the next frame; frame.t = exposure time
    image = frame.array()                     # (480, 640, 3) uint8 RGB
    esc.set_pulse_us(1500)
    clock.sleep_until(clock.now() + 0.005)    # never time.sleep: the twin runs in lockstep
```

- **Camera in the twin.** The twin renders the camera with MuJoCo's offscreen renderer at the catalog
  field of view and the compiled camera pose. Frames are deterministic and carry timestamps and sequence
  numbers.
- **What the camera sees.** Configure the scene in `simulation.environment.visual`, for example a
  ground texture, a lane-marked track and cones:

  ```yaml
  visual: {ground: grass, track: {from: mission, width: 0.9}, cone_spacing: 3}
  ```

- **Protocol.** See `packages/hardware-interface/PROTOCOL.md`.
- **On the Pi.** Install `python3-picamera2` and add `dtoverlay=imx296,cam0` to `/boot/firmware/config.txt`.

## 10. Simulation scenarios

```yaml
simulation:
  environment: {ground_friction: 0.9, radio_transmitter: {position: [-2 m, 0, 1.2 m], power_dbm: 20},
                visual: {ground: grass, track: {from: mission}, cone_spacing: 3}}
  scenarios:
    - id: autonomous_course
      duration: 90 s
      initial: {battery_soc: 0.95}
      pilot: {arm_at: 1.0 s}                                   # scripted transmitter (CRSF via the receiver)
      mission: {type: waypoints, laps: 2, speed: 4.0, waypoints: [[5, 0], [35, 0], [35, 15], [5, 15]]}
      expect: {laps_completed: 2, max_cross_track_error_m: 1.5, brownouts: 0}
```

- **Expectations:**
  - `laps_completed`
  - `max_cross_track_error_m`
  - `failsafe_events`
  - `min_battery_voltage_v`
  - `brownouts`
  - `min_top_speed_mps`
  - `link_lost_beyond_m`
  - `stop_within_m_of_link_loss`
  - `stop_within_m_of_fault`
  - `arbiter_neutral_within_s`
  - `min_cell_voltage_v`
  - `low_voltage_cutoff_engaged`
  - `min_wire_clearance_m`
- **Faults:** `software_freeze` and `transmitter_off`.
- **Running.** `r3 sim run --scenario <id>`, or MCP `r3_run_scenario`. Results go to
  `build/sim/<id>/summary.json` with plots and renders.

## 11. The release gate

`r3 status --release` (MCP: `r3_release_status`) lists every requirement, scenario expectation and stage
with its evidence. The possible statuses are `verified`, `estimated`, `failed`, `stale` and `not_run`.
`fabrication_ready` is true only when everything is verified from current inputs. A `not_configured`
stage blocks the release.

## 12. Tools

| MCP tool | CLI | Purpose |
| --- | --- | --- |
| `r3_guide` | (this file) | How to build a robot |
| `r3_create_project` | `r3 create <name> --template blank` | Local project scaffold (never GitHub) |
| `r3_status` | `r3 status` | Stages, revisions, lock |
| `r3_get_spec` / `r3_patch_spec` | edit `robot.yaml` | Read / merge-patch the spec (optimistic `sha256`) |
| `r3_list_catalog` / `r3_get_catalog_component` | — | Find parts, read full entries |
| `r3_put_catalog_component` | write `catalog/components/*.yaml` | Your printed / extra parts (validated) |
| `r3_source_slots` / `r3_source_search` / `r3_source_choose` | `r3 source [<slot>] [--choose N]` | Source real parts by requirements |
| `r3_validate` / `r3_run_stage` | `r3 validate [--stage a,b]` | Run stages (assembly, model, cad, fabrication, pcb, wiring, connectors, electrical, sim, report) |
| `r3_get_diagnostics` / `r3_diagnose` | `r3 diagnose` | Errors grouped by subject with hints |
| `r3_run_scenario` | `r3 sim run --scenario <id>` | One twin scenario |
| `r3_get_artifact` | read `build/...` | JSON, text, images |
| `r3_release_status` | `r3 status --release` | Release gate |
| `r3_doctor` | `r3 doctor` | Which stages can run headless here |
| `deploy_fleet` (gated) | `r3 deploy --target fleet --yes` | Physical robots: only on explicit human request |
| `create_github_repo` (gated) | `r3 create --github` | Only on explicit human request |

Typical loop: `r3_get_spec` → `r3_patch_spec` → `r3_validate` → `r3_diagnose` → fix → repeat. Grow the
robot in this order:

1. frame and computer;
2. power;
3. drivetrain and steering;
4. sensors and camera;
5. harnesses;
6. firmware;
7. scenarios.

## Parts you already own

Use `owned: true` or `owned: {note: "Already on hand"}` on a component to keep it
in the model and exclude it from purchasing and sourcing. See [owned parts](owned-parts.md)
for examples, purchase totals and the `source:` conflict rule.
