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

## Radio: Sionna RT propagation (branch `r3/sionna`)

11. **Spec: `radio_environment.model`.** `robot.yaml` still says `model: two_ray_ground_log_normal`. The twin
    now ray-traces with Sionna RT by default and ignores this key. Please change it to `model: sionna_rt`.
    Optional keys the twin reads: `propagation_backend` (`sionna_rt` | `analytic`), `ground_material`
    (ITU-R P.2040 name, default `medium_dry_ground` for `terrain: flat-grass`), `rt_grid_m` (0.25),
    `rt_max_depth` (3), `rt_samples_per_source` (200000), `tx_antenna_pattern` (`hw_dipole`), `polarization` (`V`).
    `ground_permittivity: 15` is now ignored unless `ground_conductivity` is also given, because the ITU material
    defines both; the twin records this as an assumption.
12. **Spec: obstacle materials.** `cone_a` has no `material`; the twin assumes ITU `concrete` and labels it
    as estimated. A traffic cone is PVC. Please add `material: plastic` (the twin maps it to eps_r 3.0, sigma 0.004 S/m),
    or another ITU-R P.2040 name.
13. **CAD contract: `rf.antenna_position`.** The twin uses `build/cad/cad_model.json` → `{"rf": {"antenna_position":
    [x, y, z]}}` (robot frame, m) for the car antenna when present. Otherwise it uses the receiver pose plus the catalog
    `antenna_height_above_mount`. Please have the CAD stage write it, for example from a mate connector on the antenna tip.
14. **Firmware does not receive the scenario mission (blocks `range_failsafe`).** The twin's `hello` reply
    sends a flat config document (`{..., "mode", "mission", "gps_origin", "crsf_channels"}`), the same content as
    `R3_SOFTWARE_CONFIG`. `rc_car_controller.settings.load_settings` reads `mission` (and `geodetic_origin`) only
    from a structured `{"config": ..., "mission": ...}` document, so the firmware boots with `mission: False` and
    `range_failsafe` never drives (max 2 m from the TX; link never lost). Fix it on one side: the firmware reads
    `mission`/`gps_origin` from a flat HAL document, or the twin sends the structured form. The twin's own reference
    controller (`tests/sim_test_controller.py`) reads the flat form.

## From lane B (branch `r3/finish-b`): two evaluation changes made to unblock the failsafe scenarios

Both are small and in `r3_sim`; please review them when merging (lane C owns evaluation/scenarios):

1. `arbiter_neutral_within_s` (`evaluation.py`): the HAT arbiter now brakes before neutral (QuicRun brake: reverse stick
   after forward; `brake_us` for `brake_hold`, then `neutral_us`, both recorded on the `arbiter_failsafe` event). The
   check now requires the ESC to follow the arbiter's own output (brake, then neutral) after the switch instead of
   neutral only; the latency limit is unchanged.
2. Link loss for `link_lost_beyond_m` / `stop_within_m_of_link_loss` (`scenario.py`): at the edge of range the ELRS
   receiver keeps catching single packets (LQ 3-12 % at ~518 m), so its own 1 s loss timeout never fires and both
   checks reported "link never lost". When the receiver declares no loss, the first RC-frame gap longer than the
   software's `radio_failsafe_timeout_s` while the car moves is scored as the loss (`source: software_failsafe_gap`);
   it can only be earlier than a receiver-declared loss, so neither check is relaxed. range_failsafe on my branch:
   loss at 515.8 m (>= 300 m), stop 2.12 m (<= 3 m) from 3.9 m/s.
3. Pooled ngspice instances (`electrical/ngspice.py`) now drop the previous circuit's `time_sources`/`edges` on
   reset: a switched-ESC test left its PWM gate function behind and the next circuit's duty source followed it
   (the order-dependent `test_electrical_stage` failures on `r3/integration`).
