# Contract requests from the `sim` area (r3_sim / r3_sim_worker)

1. **ElectricalSystem initial state.** The kernel constructs
   `ElectricalSystem(project, routes=routes, initial=scenario.initial)` and falls back to
   `ElectricalSystem(project, routes=routes)` plus `set_initial_state(initial)` / `set_initial` / `reset`
   if present. Please accept `initial: dict` (keys: `battery_soc`, optional `mode`), otherwise every
   scenario starts at the model's default SOC.
2. **ESC "no signal".** The kernel passes `{"esc": {"pulse_us": 0.0}}` (and the same for the servo) when no
   PWM pulse reaches the device (computer browned out, PWM disabled, or the binding is not wired). Please
   treat `pulse_us <= 0` as signal loss (the ESC `signal_timeout` then applies).
3. **Output keys the kernel reads** (with fallbacks): `outputs[motor]["torque"|"current"]`,
   `outputs[battery]["v_terminal"|"current"|"soc"|"cell_voltage"]`, `outputs[pi]["powered"]`,
   `outputs[servo]["voltage"]` (servo supply; used to scale stall torque and speed), `outputs["_nets"]["5V"|"VBAT"|"BEC_6V"]`,
   `outputs["_branches"][<wire>]["current"]` for the wire feeding the HAT VBAT pin (INA226 shunt current),
   and `outputs[hat]["v_in"]` (INA226 bus voltage).
4. **CableSimulator.** Called every 5 ms as `step(dt, chassis={"pos","quat","lin_vel","lin_acc","ang_vel","ang_acc"},
   moving={body: {"pos","quat"}})` with world-frame poses of every non-root MuJoCo body (wheels and knuckles).
   The kernel reads `report.min_clearance: {wire_id: (distance_m, component)}`.
5. **Spec: CRSF channel map.** Add `software.config.crsf_channels: {steering: 1, throttle: 2, arm: 5}` to
   `robot.yaml` so the firmware and the twin's pilot agree explicitly (the twin defaults to this map and
   passes it to the software in `R3_SOFTWARE_CONFIG` and the `hello` reply).
6. **Spec: geodetic origin.** Add `simulation.environment.geodetic_origin: [lat, lon, alt]`. The twin
   currently reads `scenario.initial.gps_origin` or `radio_environment.gps_origin` and defaults to
   37.4275, -122.1697, 30 m.
7. **r3_hal: `VirtualHal` exposes no `config`.** The twin returns `config` in the `hello` reply (software
   config merged with scenario mode/mission/GPS origin), but `VirtualHal` drops it. Please add
   `Hal.config() -> dict` (linux backend: read from the deployed config). Until then the runner also
   sets `R3_SOFTWARE_CONFIG` (JSON) in the program's environment.
8. **r3_hal: socket not closed.** `_Connection.close()` closes the socket but not the `makefile()` object,
   so the file descriptor stays open after `close()`. It should close `self._file` too.
9. **r3_hal: `gpio.wait_edge`** is in PROTOCOL.md and implemented by the twin, but `GpioLine` has no
   `wait_edge(timeout)` method.
10. **Scenario `low_battery`.** With `battery_soc: 0.08`, 120 s of 4 m/s driving uses about 3 % SOC, so the pack
    stays above 3.5 V/cell and a correct firmware never reaches `low_voltage_cutoff_v`. That makes
    `low_voltage_cutoff_engaged: true` fail for the right firmware. Suggest `battery_soc: 0.02` (about 3.4 V/cell OCV), or a longer
    duration.
