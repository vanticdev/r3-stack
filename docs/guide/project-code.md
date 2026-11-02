# Public APIs for project code

Keep firmware under `backend/<language>/<module>/`, device bridges under
`simulation/`, and tests under `tests/`. These authored project files may be edited
directly. Use `r3_patch_spec` for the robot declaration and catalog tools for
component metadata. `r3_get_schema("robot")` and `r3_get_schema("device")` expose
the typed modules, bindings, scenarios and factory configuration.

## Firmware and HAL

`from r3_hal import open_hal, HalError` opens the selected backend with
`hal = open_hal(module="<software module id>")`. The twin supplies `R3_HAL=virtual`
and its endpoint. Physical execution defaults to Linux. A new vendor protocol
needs your project's physical driver as well as a virtual bridge; the project
device extension supplies the virtual side.

| API | Contract |
| --- | --- |
| `hal.clock.now()` | Clock in seconds. |
| `hal.clock.sleep(seconds)` / `sleep_until(t)` | Firmware timing on the selected clock. |
| `hal.bindings()` / `hal.config()` | Binding metadata and runtime configuration. Physical firmware may load its own project configuration. |
| `hal.gpio(chip, line, direction="in", bias="none", edge="none")` | Line with `read()`, `write(value)`, `count_edges()` and optional `wait_edge(timeout)`. Counts include last-edge time. |
| `hal.pwm(chip, channel)` | `configure(period_ns, duty_ns, enabled=True)` or `set_pulse_us(pulse_us, frequency_hz=50)`. |
| `hal.i2c(bus)` | `transfer(addr, write=b"", read_len=0)`, `write_reg(addr, reg, data)`, `read_reg(addr, reg, length)`. Bytes in/out. |
| `hal.spi(bus, cs, speed_hz=1000000, mode=0)` | `transfer(data: bytes) -> bytes`. |
| `hal.uart(device, baud)` | `write(data: bytes) -> int`, `read(max_bytes=4096, timeout=0) -> bytes`. |
| `hal.camera(port="cam0", width=640, height=480, fps=30, format="rgb888")` | Declared CSI stream; `capture(timeout=1)` waits for the next frame. |
| `hal.device_request(device, operation, payload=None, timeout=0)` | JSON request to a project virtual device; waiting advances the twin clock. |
| `hal.log(level, message, **fields)` / `hal.close()` | Logging and cleanup. |

Camera frames contain `t`, `sequence`, `width`, `height`, `format`, `data` and
`exposure_s`. `frame.array()` returns a NumPy view, with row-major `rgb888` or
`gray8` pixels. Use capture timestamps and sequence numbers to detect stale or
dropped data. Interface failures are `HalError` with a stable `.code`.
Bindings require actual electrical reachability; `expects` does not make a wire.

BLITZ declarations use `BackendModules(__file__)`, one source directory per
module, and the scaffold's `get_modules()` and `pi_name_to_process_types()` hooks.
`WeightedProcess` enum members hold `(name, weight)`;
`ProcessPlan().add(process, count=...).assign(discovered_names)` assigns demand.
`BackendModules.python(name, process=..., entrypoint=..., dependencies=...)`
declares a program under `backend/python/<name>`. Also declare it in the robot's
software schema. The twin executes this real program against virtual hardware.

## Project device lifecycle

`simulation.devices.<name>` declares `component`, `source`, `factory`, `params`,
provenance and optional `electrical_factory`. `factory(context)` returns:

- `step(dt) -> None`: register/packet logic after each native step.
- `request(operation, payload) -> dict | None`: JSON result, or no sample yet.

Context provides `component`, `params`, `t`, native MuJoCo `model` and `data`,
circuit-derived `powered`, and reachable `bindings`. Resolve native bodies with
`mujoco.mj_name2id` and sample public simulator APIs. Keep protocol timing,
calibration and noise in the project, with sourced assumptions. Domain physics
stays inside MuJoCo/ngspice/Sionna. Unpowered or unreachable devices stay unavailable.
Use absolute project imports such as `simulation.helper`; the factory file is
loaded as an isolated module. Source paths and symlinks must remain inside
this project's `simulation/` directory.

## Project electrical lifecycle

`electrical_factory(component_id, component_type, params, build_context)` returns
an `r3_sim.electrical.models.BehaviorModel`. Select it with catalog
`behavior.model: project.<device name>`; that device must declare the same component
and factory. Return a suitable existing native model or subclass its device logic.
For a measured constant-current load, `ConstantLoad(component_id, component_type,
params, build_context, model="project.<device name>")` reuses native ngspice behavior.

For a new model, `BehaviorModel` accepts the four factory arguments.
`self.params.get(name, default)` parses units and records defaults as estimates.
`self.pins` holds role-tagged pin references with graph labels, SPICE nodes and
limits. `self.require(self.pin_by_role(...), description)` checks a required pin.
Build context exposes `project`, `ambient_c`, `reference`, `node(graph_label)`
and `pins(component_id, component_type)`.

| Lifecycle method | Purpose |
| --- | --- |
| `build(netlist)` | Define native ngspice elements/subcircuits. |
| `initialize(initial)` | Initialize logic. |
| `begin_step(dt, inputs, time)` | Decode commands; update `self.controls`. |
| `resolve(solution) -> bool` | Static active-set update; true requests another solve. |
| `end_step(dt, solution, time) -> list[Event]` | Protocol/protection logic after integration. |
| `outputs(solution) -> dict` | Solved quantities, power state and telemetry. |
| `time_sources(start, end)` | Optional native-time source callbacks and corner times. |

`dt=None` means a static operating point: do not advance timers or device state.
Declare ngspice EXTERNAL controls with `self.control(netlist, name, value)`.
Put continuous cross-domain inputs in `ramped` for a first-order hold.
`Netlist.add(*lines)`, `subckt(name, ports, lines, doc="")`, `model(name, card)`
and `external(name)` use ngspice's public circuit language. Read real output with
`Solution.v(node, reference)`, `i(source)`, `get(vector)` and `reduced(vector, kind)`.
Keep physical assumptions/provenance visible; do not replace the circuit solver.

### Standalone native circuit experiments

Use `from r3_sim.electrical.bench import Bench` to exercise project electrical
models without an assembled robot. Construct `Bench([model], cards)` with native
ngspice source/load cards. `bench.operating_point({model.id: inputs})` solves the
static circuit and calls `begin_step(None, inputs, time)`: apply the supplied
controls, but do not advance timers or integrated state. A project state dictionary
alone does not update ngspice control sources; `begin_step` must copy its current
inputs into `self.controls`.

`bench.step(dt, {model.id: inputs})` runs the native transient and the model's
step lifecycle. It starts a transient deck if needed; switching from a static
solve starts that transient from the model's initialized state. Choose a timestep
and duration appropriate to the phenomenon, check native solved outputs, and
retain the original acceptance limits when fixing a test setup. Call
`bench.close()` in `finally` to release the native engine. These experiments
qualify device behavior; complete assembled twin scenarios still qualify the robot.

## Tests and scenarios

Run `tests/test_*.py` with `r3_run_project_tests`, and native scenarios with
`r3_run_scenario(id)`. These use the probed worker, including offscreen rendering;
shell sandboxes may deny graphics services. Read `tests/pytest.log`,
`tests/junit.xml` and `sim/<id>/summary.json` with `r3_get_artifact`;
use `r3_view_artifact` for images. Independent native experiments can use MuJoCo's
public model/renderer APIs inside project tests.

Bind custom requirements to actual case IDs from `metrics.cases`, retaining
their descriptions. Failures, skips, missing cases and changed sources stay
visible in release evidence. Test autonomous motion and mechanical load cases
against real native-twin recordings and the authored firmware. Keep standard checks.
