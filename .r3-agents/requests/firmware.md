# Firmware area (`r3/firmware`): contract requests and findings

## 1. Twin ↔ firmware conventions (please match in `r3_sim`)

The firmware assumes the following; the twin's sensor/radio models should produce it.

- **Config delivery.** Today the firmware reads `R3_FIRMWARE_CONFIG` (JSON file:
  `{"config", "bindings", "mission", "geodetic_origin"}`; produce it with
  `examples/robots/rc-car/backend/twin.py config --scenario <id>`).
  *Request:* add an optional `config` object to the `hello` reply in `PROTOCOL.md`
  and expose it as `VirtualHal.config()` (additive), so the container does not need
  a bind-mounted file. The firmware will read it via `getattr(hal, "config", None)`.
- **World frame.** Local ENU about `geodetic_origin` (x east, y north); mission
  waypoints are in this frame. The GPS model should convert world (x, y) to
  lat/lon with the same origin (`nmea.LocalTangentPlane`, WGS-84 radii). Without
  an origin the firmware averages boot fixes and assumes the car starts at (0, 0).
- **GPS:** NMEA GGA + RMC + VTG at 10 Hz on `/dev/ttyAMA2`, valid checksums;
  RMC/VTG course in degrees true (clockwise from north).
- **CRSF:** RC_CHANNELS_PACKED at the packet rate on `/dev/ttyAMA0`;
  ch1 steering (2012 us = full left, i.e. same direction as the servo pulse),
  ch2 throttle (1500 neutral, 2012 full forward), ch5 arm (>1700 us). On link loss
  the receiver stops sending RC frames (ELRS behaviour); LINK_STATISTICS with
  LQ = 0, when sent, triggers failsafe immediately.
  The scenario `pilot: {arm_at}` should drive ch5 **low before** `arm_at` and high after;
  the firmware never arms on a switch that is already high at boot.
  Manual scenarios need no arming (the throttle must start at neutral).
- **Encoder:** rising edges on `gpiochip0` line 17, 4 per wheel revolution.
- **IMU:** MPU-6050 register model at 0x68 (WHO_AM_I 0x68, PWR_MGMT_1, CONFIG,
  SMPLRT_DIV, GYRO_CONFIG, ACCEL_CONFIG writes; 14-byte burst at 0x3B, big-endian).
  The car must be stationary for the first 0.5 s (bias calibration).
- **INA226:** 0x40, IDs 0xFE = 0x5449 / 0xFF = 0x2260; the firmware resets it, writes
  config 0x4527 and CAL 5120 (2 mΩ, 0.5 mA LSB) and reads 0x01–0x04.

## 2. BLITZ gaps (not modified; worked around)

- **External Python source packages.** A BLITZ bundle contains exactly the module
  folder plus `common/`; there is no typed way to declare a dependency on a
  repository package such as `r3_hal`. Workaround: `deploy.py:stage_hal()` copies
  `r3_hal` into `rc_car_controller/_vendor/` (git-ignored) and `vendor.py` adds it to
  `sys.path`. *Request:* a typed declaration (for example
  `PythonModule(..., vendored_sources=(path,))` or a `ModuleOutputs` source entry).
- **Working-directory dependence.** `SystemBuilder` reads `requirements.txt` from
  `Path.cwd()`, contrary to values.md §7. Workaround: `deploy.py` changes the working
  directory to the project root before `run_cli`. Added `examples/robots/rc-car/requirements.txt`
  (empty: standard library only).
- **No runtime images.** BLITZ builds bundles, not runnable images (values.md §4).
  The simulation container is `r3_hal.container`, which installs either a
  module-folder bundle or the real BLITZ bundle (`twin.py image --blitz`, via
  `CodeBundler` with a Pi 5 `SystemId`: glibc 2.36, aarch64, Debian 12, CPython 3.11)
  into a BLITZ-shaped `/opt/blitz` tree. The `--blitz` path reads `CodeBundler` /
  `SystemBuilder` directly; a public "build bundle for profile X" API would be cleaner.

## 3. Design findings for the RC car (to verify in the twin)

- **`range_failsafe` stop distance is marginal.** Distance from link loss to stop =
  detection (timeout 0.25 s + up to two 10 ms ticks) x v + v² / (2 a_brake). With
  rear-wheel braking only, a_brake ≈ μ g x rear-axle load share ≈ 0.9 x 9.81 x 0.5 ≈ 4.4 m/s²
  (estimate). At 4 m/s (the firmware caps the mission's 5 m/s at
  `max_cruise_speed_mps` = 4): 1.04 + 1.82 = 2.9 m (in-process test double: 2.86 m),
  against the 3 m limit. At 5 m/s: 1.30 + 2.84 = 4.1 m, which fails. If the receiver keeps
  emitting stale channels for its catalog `failsafe_timeout` of 1.0 s, the result is
  4 m + 1.8 m ≈ 5.8 m, which also fails. Mitigations already in the firmware: LQ = 0 detection,
  and a 2 m/s speed cap when uplink LQ < 70 %. Options for the design: shorter
  receiver failsafe, or a lower cruise speed at range.
- **INA226 only sees the HAT branch.** The shunt is on the HAT (after the fuse), so
  the ESC current, which dominates sag, is not measured. Sag compensation therefore
  relies on filtering and hold times plus throttle limiting, not on V + I·R_int. A
  shunt on the battery lead (before the Y split) would allow true open-circuit
  voltage estimation.
- **GPS cold start.** The catalog gives `cold_start: 30 s`. Autonomy waits for a fix
  (`require_gps_fix_to_arm`). If the twin models the cold start, `arm_at: 1.0 s`
  scenarios start ~30 s late, and `autonomous_course` (2 x 90 m in 90 s) gets tight.
- **Brake after standstill.** A single-channel hall encoder cannot confirm a stop sooner
  than one edge interval, so the brake pulse persists for ~0.3 s after the wheels stop.
  On the QuicRun forward/brake/reverse ESC this cannot engage reverse, because reverse
  needs a return to neutral first.

## 4. Small catalog parameter requests

- `esc-brushed-60a.behavior.params.reverse_mode: forward_brake_reverse_double_click`
  (documents the assumption above).
- `r3-carrier-hat.behavior.params.ina226.max_current: 10 A` (the fuse rating used for
  the INA226 calibration).

## 3. Traction control round (`r3/firmware-traction`)

- **sim — cross-track window artefact.** `evaluation.py` starts the
  `max_cross_track_error_m` window when the car enters the 2 m capture radius of
  waypoint 1, so the first sample is ~2.0 m from the course by construction
  (`autonomous_course` reports 1.991 m at t = 2.46 s, (3.03, −0.26)). *Request:*
  start the window when course progress first becomes positive (waypoint 1 passed).
- **sim — full stick is 97.6 %.** The pilot maps stick 1.0 to 2000 us; CRSF full
  deflection is 2012 us. With the firmware's 2 % centre deadband 2000 us gives
  throttle 0.976, which caps `full_throttle` at 8.045 m/s (motor limited). *Request:*
  map ±1.0 to 988/2012 us (CRSF limits).
- **sim/design — GPS-limited cross-track.** `autonomous_course` truth cross-track on
  the course is 1.89 m (lap 1), 1.07 m (lap 2); the firmware's own estimate stays at
  0.30 m rms, while its position error vs truth reaches 1.91 m (GPS Gauss-Markov
  bias, CEP 1.5 m). Identical with yaw assist off (1.874 m). The design needs RTK or
  a better receiver to meet 1.5 m; the sim's `gps_accuracy_insufficient` already says so.
- **design — single-wheel encoder on an open differential.** The hall encoder rides
  on `spin_rl`; the unmeasured right rear wheel can spin faster (twin: carrier
  6.45 m/s vs left wheel 5.80 m/s at t = 2.4 s). Slip control sees only the left
  wheel. *Suggestion:* sense the spur gear / carrier (or both wheels).
- **robot.yaml (optional).** `software.config.traction` with the measured plant
  constants (`vehicle_mass_kg`, `rear_axle_load_fraction`, motor Kv/R, ratio) and
  `motor_current_limit_a`; firmware defaults are catalog estimates.
