# Simulation (`r3_sim`)

Digital-twin co-simulation kernel for R3 robots. The real robot software runs as
a separate process against virtual hardware, in lockstep with the physics.

| Module | Role |
| --- | --- |
| `vehicle/mjcf.py` | MJCF from `robot.yaml`: joint-child bodies, fixed parts lumped with composite inertia, CAD mass properties when `build/cad/cad_model.json` exists (`cad_mass_estimated` otherwise), cylinder tire contact |
| `vehicle/dynamics.py` | open differential (tendon, reflected rotor inertia), Ackermann tie-rod linkage, rolling resistance moment, aerodynamic drag |
| `vehicle/servo.py` | PWM servo: deadband, voltage-scaled stall torque and no-load rate, horn load by virtual work |
| `kernel.py` | 1 ms fixed-step loop in the documented order; seeded RNG streams per subsystem |
| `radio.py`, `crsf.py` | two-ray model with a complex reflection coefficient, Gudmundson shadowing, knife-edge obstacles, LoRa PER, ELRS receiver emitting CRSF 0x16/0x14 frames |
| `devices/` | MPU-6050, INA226 (register level), u-blox NMEA GPS (byte timing), Hall encoder edges |
| `signals.py` | binding → device reachability from the net graph (an unwired device sees nothing) |
| `hal_server.py` | `r3-hal/1` server (see `packages/hardware-interface/PROTOCOL.md`), brownout errors |
| `scenario.py`, `evaluation.py` | software subprocess, brownout reboot, watchdog restarts, `expect` evaluation, artifacts |
| `fallbacks.py` | **labeled fallbacks**, used only when `r3_sim.electrical` / `r3_wiring` are missing |
| `stage.py` | `run(project) -> StageResult` over all scenarios |

Outputs per scenario under `build/sim/<scenario>/`: `summary.json`, `recording.npz`,
`trajectory.png`, `speed.png`, `battery.png`, `rssi_vs_distance.png`, `currents.png`,
`renders/*.png`, `renders/clip.mp4`, `vehicle.xml`, `software.log`. `build/sim/overview.png`
summarizes all scenarios.

Simulation success does not imply hardware success; every summary lists its assumptions.
