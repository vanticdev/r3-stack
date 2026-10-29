# R3 pipeline and cross-package contracts (v1)

Goal: describe a robot once in `robot.yaml`, build every domain from it, run the
real robot software against a multi-domain digital twin, and get structured,
actionable diagnostics, so that an AI agent (or a human) can iterate to a robot
that works on the first physical build. Passing does not guarantee physical
success; every stage records its assumptions.

## Language and packaging

All platform packages are Python 3.11+ (one root `pyproject.toml`, `r3-platform`).
Each reserved folder holds one importable package:

| Folder | Package | Owns |
| --- | --- | --- |
| `packages/robot-model` | `r3_model` | spec schema, units, derived graph, diagnostics, project layout |
| `packages/component-catalog` | `r3_catalog` | component/connector/wire metadata with provenance |
| `packages/wiring` | `r3_wiring` | 3D cable routing, harness checks, physical slack simulation |
| `packages/simulation` | `r3_sim` | co-simulation kernel, MuJoCo vehicle, electrical network, radio, sensors, twin HAL server |
| `packages/hardware-interface` | `r3_hal` | software-facing HAL (linux + virtual backends), `PROTOCOL.md` |
| `integrations/onshape` | `r3_onshape` | FeatureScript generation, Onshape sync, mass properties, mesh export |
| `integrations/kicad` | `r3_kicad` | KiCad schematic/PCB generation, ERC/DRC, netlist + board import |
| `integrations/github` | `r3_github` | project repository creation, workflow templates, status publishing |
| `services/orchestrator` | `r3_orchestrator` | `r3` CLI, MCP server, stage scheduling, reports |
| `services/simulation-worker` | `r3_sim_worker` | runs scenario jobs with pinned inputs |

Run with the root venv: `.venv/bin/python -m pytest tests/...`. Tests live in `tests/`.

## Stage contract

Each stage is a function `run(project: r3_model.project.Project) -> StageResult`
exposed as `<package>.stage.run`. It writes artifacts under `build/<stage>/`, adds
`Diagnostic`s with stable `code`s, records `assumptions`, and copies
`project.fingerprint()` into `inputs`. Order and data hand-off:

| # | Stage | Reads | Writes |
| --- | --- | --- | --- |
| 1 | `model` | robot.yaml, catalog | `build/model/graph.json`; binding/connector-mate/pin-voltage/logic-level/I²C-address checks |
| 2 | `cad` | spec, `cad/` | `build/cad/cad_model.json`, `build/cad/meshes/<component>.stl`, screenshots |
| 3 | `pcb` | spec, `pcb/<board>/` | `build/pcb/<board>/board.json`, KiCad files, gerbers, ERC/DRC reports |
| 4 | `wiring` | graph, cad_model, board.json | `build/wiring/routes.json`, harness BOM, cut lengths |
| 5 | `electrical` | graph, routes, catalog behaviors | `build/electrical/operating_points.json` (static worst cases) |
| 6 | `software` | `backend/`, spec | firmware bundle via BLITZ (`cicd/`), container image for simulation |
| 7 | `sim` | everything above | `build/sim/<scenario>/summary.json`, `recording.npz`, plots, renders |
| 8 | `report` | all results | `build/report/index.html`, `build/report/summary.json` |

Stages fall back to catalog estimates when an upstream artifact is missing, and
say so with a `*_estimated` warning diagnostic.

### `build/cad/cad_model.json`

```json
{"source": {"provider": "onshape", "document_id": "...", "workspace_id": "...", "element_id": "...", "microversion": "..."},
 "parts": {"<component id>": {"part_name": "...", "mass": 0.0, "com": [x, y, z], "inertia": [ixx, iyy, izz, ixy, ixz, iyz],
                              "bbox_min": [..], "bbox_max": [..], "material": "...", "density": 0.0, "mesh": "meshes/<id>.stl",
                              "mass_status": "cad"}}}
```

Robot frame, SI units; inertia about the part COM in robot-frame axes.

### `build/pcb/<board>/board.json`

```json
{"board": "carrier-hat", "component": "hat", "outline": {"width": 0.065, "height": 0.0565},
 "nets": {"VBAT": ["J_BATT.1", "U1.VIN", "R1.1"]}, "connectors": {"J_BATT": {"1": "VBAT", "2": "GND"}},
 "traces": [{"net": "VBAT", "width": 0.002, "length": 0.03, "layer": "F.Cu", "max_current": 4.1}],
 "erc": {"errors": 0, "warnings": 0}, "drc": {"errors": 0}, "files": {"kicad_pcb": "...", "gerbers": "..."}}
```

Connector pin→net maps must equal the catalog entry of the component (checked).

### `build/wiring/routes.json`

```json
{"wires": {"<wire id>": {"harness": "...", "awg": 12, "path": [[x, y, z], ...], "routed_length": 0.0, "cut_length": 0.0}},
 "harnesses": {"<id>": {"bundle_path": [[x, y, z], ...], "length": 0.0, "tie_points": [[..]]}},
 "clearances": [{"wire": "...", "component": "...", "min_distance": 0.0}]}
```

## Co-simulation contract (`r3_sim`)

Fixed-step master clock, `simulation.timestep` (1 ms). Per step, in order:

1. Twin HAL server applies pending software outputs (PWM duty, GPIO, UART TX).
2. Device models convert them into electrical/mechanical inputs (ESC pulse, servo pulse).
3. `ElectricalSystem.step(dt, inputs) -> outputs` solves the DC network (battery,
   wires, connectors, fuse, ESC, motor, regulators, loads) and returns motor
   torque, rail voltages, branch currents, brownouts and temperatures.
4. MuJoCo vehicle steps with motor torque (through the transmission) and servo torque.
5. The routed harnesses are MuJoCo elastic cables (`composite type="cable"`,
   `mujoco.elasticity.cable`) in the same world as the vehicle, stepped by step 4.
   Clearance to moving parts is read from MuJoCo contact data every step.
6. Radio link model updates RSSI/LQ/packet delivery; receiver model emits CRSF frames.
7. Sensor models (IMU, GPS, encoder, INA226) sample the physical state.
8. Time advances; software wakes when its `sleep_until` deadline is reached.

Execution may overlap without changing this order or any result: MuJoCo's
`mj_step1` (kinematics, collision) runs while step 3 is solved, and within a
`sleep_until` advance `mj_step2` of one step runs while step 3 of the next is
solved (its inputs are final then). `R3_SIM_OVERLAP=0` runs strictly serially;
both produce bit-identical recordings.

Electrical interface, keyed by component id (generic, not RC-car specific):

```python
system = ElectricalSystem(project, routes=None)          # routes.json optional
outputs = system.step(dt, {"esc": {"pulse_us": 1600.0}, "motor": {"omega": 310.0},
                           "servo": {"pulse_us": 1500.0, "horn_torque": 0.2, "horn_speed": 0.5},
                           "pi": {"activity": 0.6}})
outputs["motor"]["torque"]; outputs["battery"]["v_terminal"]; outputs["pi"]["powered"]
outputs["_nets"]["5V"]; outputs["_branches"]["W_Y_P_IN"]["current"]
```

Cable interface (`r3_wiring.mujoco_cables`, `r3_wiring.cable_sim`):

```python
plan = plan_cables(project, routes)            # bundles, spans, part collision geoms
emit_mjcf(mjcf_root, plan, body_element, body_origin, body_name)   # into any MuJoCo world
monitor = CableMonitor(model, data, plan, root_body)
monitor.update()            # after each mj_step: clearance from MuJoCo contact data
monitor.wire_clearance()    # {wire_id: (signed surface distance, moving component)}
monitor.exact_distances()   # the same from mj_geomDistance (cross-check)

# Standalone shaker (wiring stage): chassis-only MuJoCo world plus cables.
cables = CableSimulator(project, routes)
report = cables.step(dt, chassis={"pos", "quat", "lin_acc", "ang_vel", "ang_acc"},
                     moving={"knuckle_fl": {"pos", "quat"}, ...})  # or joints={"steer_fl": 0.2}
report.min_clearance  # {wire_id: (distance, component)}
```

In the co-simulation, `build_vehicle_model(project, ..., routes=routes)` adds the
cables to the vehicle MJCF and `Vehicle.cables` is the `CableMonitor`.

## Software contract

Robot software lives in the project's BLITZ layout,
`backend/python/<module>/`, and uses only `r3_hal`. The same module runs on the
Pi (`R3_HAL=linux`) and against the twin (`R3_HAL=virtual`). In simulation the
module runs inside the BLITZ-built `linux/arm64` Debian bookworm container
(Pi OS userspace), connecting to the twin over TCP; a host-process mode exists
for fast iteration. See `packages/hardware-interface/PROTOCOL.md`.

## Diagnostics

Codes are stable snake_case identifiers. Each carries `subjects` (ids) and
`data` with measured value, limit and units, e.g.
`{"code": "wire_overcurrent", "subjects": ["W_Y_P_IN"], "data": {"current": 41.2, "ampacity": 35, "awg": 12}}`.
