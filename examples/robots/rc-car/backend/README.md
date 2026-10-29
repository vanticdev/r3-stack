# RC car robot software (BLITZ project)

```text
backend/
├── deploy.py                    # BLITZ declaration: get_modules(), pi_name_to_process_types()
├── twin.py                      # config / image / launch commands for the digital twin
└── python/rc_car_controller/    # one module = one folder; entrypoint main.py
    ├── crsf.py                  # CRSF codec (CRC-8/DVB-S2, RC_CHANNELS_PACKED, LINK_STATISTICS)
    ├── nmea.py                  # NMEA GGA/RMC/VTG + WGS-84 local tangent plane
    ├── devices.py               # INA226 and MPU-6050 drivers, gyro bias calibration
    ├── estimator.py             # EKF [x, y, yaw, v, gyro bias] + encoder period speed
    ├── control.py               # pure pursuit, speed planner, PI+FF, battery, supervisor
    ├── settings.py              # defaults (= robot.yaml software.config), config loading
    ├── vendor.py                # r3_hal search path (_vendor/ in BLITZ bundles)
    └── main.py                  # 100 Hz loop on hal.clock.sleep_until
```

The module uses only the Python standard library and `r3_hal`. It runs unchanged
on the Pi (`R3_HAL=linux`) and against the twin (`R3_HAL=virtual`).

## Behaviour

| Function | Implementation |
| --- | --- |
| Radio | CRSF at 420000 baud on `crsf` binding; ch1 steering, ch2 throttle (centre 1500 us), ch5 arm |
| Failsafe | no valid RC frame for `radio_failsafe_timeout_s` (or fresh LQ = 0) → ESC brake until wheels stop (budget 1.5 v0/3 m/s², max 3 s) → neutral, disarmed |
| Arming | autonomy arms on a low (<1300 us) → high (>1700 us) transition of `arm_channel` with valid link, calibrated IMU and a GPS fix; never at boot with the switch already high |
| Manual | sticks map linearly to `esc_pulse_us` / `steering_pulse_us` (2012 us steering = full left); resumes after failsafe once the throttle is at neutral; no arming needed |
| Autonomous | pure pursuit (L = 1.0 + 0.5 v, 1.2–6 m) on the EKF estimate; corner/end speed planning; PI speed control with steady-state and acceleration feed-forward |
| Battery | INA226 (0x40, 2 mΩ, CAL 5120, 0.5 mA LSB); 0.5 s filtered bus voltage; throttle ceiling 1 → 0.25 between `low_voltage_limit_v` and `low_voltage_cutoff_v`; latched cutoff after 1.5 s below cutoff, at rest below cutoff + 0.2 V, or raw < cutoff − 0.4 V for 0.3 s |
| IMU | MPU-6050 (0x68) ±500 °/s, DLPF 44 Hz, 14-byte burst read; gyro-z bias averaged for `imu_calibration_s` while no encoder edges |
| Logging | `hal.log` events (`boot`, `armed`, `radio_failsafe`, `brake_start`, `low_voltage_limit`, `low_voltage_cutoff`, `lap_completed`, `mission_complete`, …) and 5 Hz `telemetry` |

Frames: local east/north (ENU) about `geodetic_origin`; yaw counter-clockwise
from east; the car starts at local (0, 0) facing `initial_heading_deg` (0 = +x).
Without a configured origin, the GPS fixes averaged during IMU calibration define it.

## Configuration

`--config PATH` > `R3_FIRMWARE_CONFIG` > `BLITZ_CONFIG_PATH` (base64 JSON from
`deploy.py`) > built-in defaults. A document is
`{"config": {...}, "bindings": {...}, "mission": {...}, "geodetic_origin": {"lat", "lon"}}`;
`hal.bindings()` (virtual HAL) overrides document bindings.

## Deploy to the Pi 5 fleet

```bash
.venv/bin/python examples/robots/rc-car/backend/deploy.py            # mDNS discovery
.venv/bin/python examples/robots/rc-car/backend/deploy.py --device rc-car.local:9999
```

`deploy.py` stages `r3_hal` into `rc_car_controller/_vendor/`, requires
aarch64 + Debian 12 (Raspberry Pi OS bookworm) and runs one controller per Pi.

## Run against the twin

```bash
python examples/robots/rc-car/backend/twin.py config --scenario autonomous_course --out build/software/firmware.json
# host process (fast iteration), from the repository root
R3_HAL=virtual R3_HAL_ENDPOINT=tcp://127.0.0.1:7600 R3_FIRMWARE_CONFIG=$PWD/build/software/firmware.json \
  PYTHONPATH=examples/robots/rc-car/backend/python/rc_car_controller:packages/hardware-interface \
  python3 -u examples/robots/rc-car/backend/python/rc_car_controller/main.py
# container (linux/arm64 debian:bookworm-slim, BLITZ device layout)
python examples/robots/rc-car/backend/twin.py image            # module folder + r3_hal
python examples/robots/rc-car/backend/twin.py image --blitz --tag r3/rc-car-controller:sim   # the real BLITZ Pi 5 bundle
#   (add --allow-stale-dependencies to reuse an older cached BLITZ dependency image offline)
docker run --rm --platform linux/arm64 --add-host host.docker.internal:host-gateway \
  -e R3_HAL=virtual -e R3_HAL_ENDPOINT=tcp://host.docker.internal:7600 \
  -v $PWD/build/software/firmware.json:/etc/r3/firmware.json:ro -e R3_FIRMWARE_CONFIG=/etc/r3/firmware.json \
  r3/rc-car-controller:sim
```

`twin.py host-command|container-command --port N --config FILE` prints the exact
commands. Firmware exit codes: 0 normal / twin closed, 3 HAL error, 4 brownout.
