# Lane B2 finish report

Validated implementation: `50ddacf`, branch `r3/finish-b2`, after merging
`r3/integration` through `8b35426` (including collision checks `8f18b58`).
All ten existing `robot.yaml` route blocks are unchanged from `f16e629`.
Lane A owns their final layout and the new IMU harness route.

## Electrical and PCB

| Stage | Errors | Warnings |
|---|---:|---:|
| Model | 0 | 0 |
| PCB | 0 | 0 |
| Electrical | 0 | 17 |

The battery mating pair `battery_lead.B` / `power_y.P1` is now genuine
AMASS XT90H, rated 45 A continuous / 90 A momentary, with 0.30 milliohm
contact resistance. The final full-charge stall operating point draws
60.050 A from the battery. This replaces the XT60 interface rather than
loosening its rating. The ESC branch retains its 60 A burst limit and carries
58.9 A. [AMASS drawing/datasheet](https://www.electrokit.com/upload/product/41020/41020964/XT90H.pdf).

The HAT branch uses a Littelfuse 0287005 5 A blade fuse, 17.85 milliohm,
26 A²s melting integral. Its maximum continuous load is 3.731939 A at
`servo_stall_compute@minimum`, below the 3.75 A limit after 75% fuse
derating. The 5 A rating also protects the original reported 7.03 A installed
wire ampacity. The final regenerated wiring gives `W_Y_P_HAT` 13.4424 A
installed ampacity, still 14 AWG. No conductor rating was raised.
[Littelfuse datasheet](https://www.littelfuse.com/assetdocs/littelfuse-datasheet-287-atof?assetguid=43dcdce8-8ca2-426f-8998-7e566f048d40).

The transient short regression retains its original assertions: a short at
0.100 s produces brownout at 0.101 s, fuse opening at 0.103 s (about 100.5 A),
and regulator UVLO at 0.104 s; the final protected-wire current is approximately
8 nA. The static prospective short is 243.619 A with a 0.438 ms ideal I²t
clearing estimate; that estimate is distinct from the dynamic melt/arc model.

The 17 electrical warnings describe permitted transient currents above
continuous ratings and the 4.40% burst drop in `power_y`; they do not conceal
overcurrent errors. The calculated wire temperatures remain within their
insulation ratings for the specified burst duration.

### Native board and interfaces

KiCad 10.0.7, headless `pcbnew` / `kicad-cli` and Freerouting 2.5.0,
200-pass limit. Final flow: **ERC 0 errors / 0 warnings, DRC 0 errors,
0 unconnected, schematic parity 0 errors**, 133 silkscreen warnings.
The flow took 70.5 s. Its process/window audit observed no GUI processes or
tool windows. See the committed `pcb/carrier-hat/flow-log.json`.

The HAT extends 13.5 mm south to 65 × 70 mm. Its original datum, GPIO
engagement, mounting holes, and height are preserved. PCB-native poses and
catalog mating frames were checked independently; the board reader now
respects the declared model origin instead of recentering asymmetric boards.

| Header | Ref | PCB x, y, rotation (mm, degrees) | Robot mating x, y, z (mm) |
|---|---|---|---|
| Battery | J2 | 49, 57, 180 | 119, -42.40, 105.30 |
| Receiver | J5 | 28, 66.3, 0 | 95.5, -38.17, 103.57 |
| ESC | J3 | 10.54, 64.9, 270 | 75.5, -40.94, 103.47 |
| Servo | J4 | 20.54, 64.9, 270 | 85.5, -40.94, 103.47 |
| IMU | J9 | 39, 66.3, 0 | 106.5, -38.17, 103.57 |

All cable axes face robot -y; all exits are below the USB-block x=133 mm
boundary. ESC and servo use right-angle headers. The merged connector-body
checker measures Pi surface clearances of **14.286 mm** for `power_y.P3`,
**15.612 mm** for `rx_lead.B`, and **15.612 mm** for `imu_lead.A`.
The original RX wire/Pi intersection is also cleared.

## Owned IMU and camera

Removed the HAT's MPU-6050 U5 (`U_IMU`), C26–C29 and R18/R19 pull-ups.
J9 is keyed JST-GH: **1=3.3 V, 2=GND, 3=SDA, 4=SCL**. D7/D8 are
PESD3V3U1UL ESD clamps using the exact KiCad symbol and manufacturer pinout
(1=cathode to signal, 2=anode to GND, 2.6 pF).
[Nexperia datasheet](https://assets.nexperia.com/documents/data-sheet/PESD3V3U1UL.pdf).
The Pi's 1.8 kohm and typical GY-521 4.7 kohm pull-ups yield 1.30 kohm
combined, or 2.23 mA at VOL=0.4 V. No extra HAT pull-ups are fitted; verify
the user's actual board values before assembly.

`imu` is `gy-521-mpu6050`, `owned: true`, connected by four 28 AWG wires in
`imu_lead`. Its provisional rigid mount is a 25 × 20 × 8 mm PETG pedestal
epoxy-bonded through a join to `deck.imu_seat`; two M2.5×8 screw joins secure
the board. Board origin is **(115, 40, 86) mm**, mounting roll/pitch/yaw
**(0, 0, 0)**, aligned with the chassis. Lane A finalizes placement and routing.
The same MPU-6050 driver at address **0x68** reads the external board. Firmware
supports the declared RzRyRx sensor-to-chassis mounting rotation; the twin
uses the connected IMU component's site frame without applying the inverse
rotation twice.

The purchased GY-521's provisional collision proxy has corrected PCB thickness
and downward-facing mounting-hole frames. Hole coordinates remain estimated
and require measurement. The available purchased-board STEP must be imported
for the incremental CAD rebuild; this task did not sculpt a replacement
purchased board or call the live Onshape API.

The reserved connection for the owned Arducam camera is directly to **Pi CAM0
by FPC**, not to the HAT. Camera installation remains with the layout/camera
lane; this branch adds its HAT clearance, not the camera component. Both
CAM/DISP ports have the same z=90.8 mm top (CAM0 x=112 mm, CAM1 x=101.5 mm).
The reserved ribbon corridor exits toward -y under the HAT, past y=-43 mm.
Port top to HAT underside z=100.6 mm gives 9.8 mm. Allowing 3 mm
trimmed through-hole tails leaves 6.8 mm. A 0.3 mm FPC with 1 mm rise and
3 mm bend radius reaches z=94.95 mm, leaving **2.65 mm vertical clearance**
to the lowest tails. Latch the FPC before installing the HAT. The final FPC
route belongs to the layout/camera lane.

## Straight failsafe stops

The Pi holds steering at center during braking. Encoder/IMU slip feedback
starts at 0.2% shorting duty, releases to 0.1%, and applies full brake only
below 0.5 m/s. Ground-speed propagation during the stop uses the calibrated
IMU instead of delayed GNSS velocity. The radio timeout is 90 ms.

The independent arbiter uses a 60 ms heartbeat timeout and 20 ms frames.
After forward driving it alternates **1460 / 1484 microsecond** brake pulses
for 3 s, then neutral. Both pulses remain outside the ESC's neutral band,
so modulation does not re-arm reverse. The arbiter only owns ESC PWM; the
Pi-hang scenario starts from straight driving. These are simulated ATtiny
behavior-contract results, not a flashed ATtiny firmware binary or a hardware
bench measurement. ESC pulse strength still needs bench calibration.

At full speed, the real Pi firmware stops in **8.424 m from 8.483 m/s**,
with maximum unwrapped yaw **0.474 degrees**, within the unchanged 9 m /
30 degree limits. Full throttle reaches **8.490 m/s**, exceeding 8.0 m/s.
Pi hang stops in **2.847 m from 3.995 m/s**, yaw **0.064 degrees**, with
70 ms heartbeat-to-arbiter latency, within the unchanged 3 m limit.

All seven scenarios were rerun with the real Pi controller process, real
ngspice electrical simulation and MuJoCo; electrical/cable fallbacks were
false. Six pass; steering remains blocked by lane A's provisional IMU route.

| Scenario | Result | Final measurement |
|---|---|---|
| `autonomous_course` | PASS | 2 laps; maximum cross-track error 0.433 m; no failsafe events |
| `full_throttle` | PASS | 8.490 m/s; 0 brownouts |
| `range_failsafe` | PASS | Link loss at 473.4 m; stop 2.741 m from 3.997 m/s |
| `low_battery` | PASS | Minimum cell 3.352 V; cutoff at 1.50 s; 0 brownouts |
| `steering_sweep` | FAIL — layout | IMU VCC wire/gearbox clearance -0.13 mm, required 8 mm |
| `top_speed_failsafe` | PASS | Stop 8.424 m; maximum yaw 0.474 degrees; 8.490 m/s peak |
| `pi_hang` | PASS | Stop 2.847 m; maximum yaw 0.064 degrees; arbiter latency 70 ms |

The aggregate `build/sim/result.json`, `scenarios.json`, and overview were
rebuilt from all seven final outcomes; the aggregate correctly remains failed
because of steering clearance. Individual summaries retain their authored
input fingerprints. Zero brownouts were observed in every scenario.

## Root-cause fixes found during integration

* The hand-calculation mismatch was more than branch selection: ngspice's
  finite transient operating-point fallback returned nonzero motor-inductor
  voltage and an unsettled battery RC state as DC. DC netlists now explicitly
  short inductors, open capacitors, and use alterable DC controls for source
  stepping. Transients retain the dynamic elements and external callbacks.
  Motor leads are identified by graph topology rather than exact floating-point
  current equality. Repeated operating-point regressions check both motor and
  battery equilibrium. No expected current was repinned and the 1e-6 hand-test
  tolerance is unchanged.
* Unequal routed cable spans exposed a mass error from averaging segment
  lengths without weighting. MuJoCo cable density now uses the total actual
  capsule volume, including endcaps; the compiled cable mass matches the
  manufacturing cut-wire mass. A regression covers unequal tied spans.
* The nominal Hall magnet is centered above the sensor, while the old pulse
  model put zero shaft angle on an edge. Tiny parked-wheel oscillations then
  generated false speed pulses and restarted IMU calibration. Pulse phase now
  matches the declared geometry; the existing 50% duty approximation and pulse
  counts are retained. Calibration finishes at 0.49 s with zero restarts in
  the final full-throttle run. Arming and calibration requirements are unchanged.

## Remaining layout blocker

The independent wiring audit is **76 errors, 81 warnings, 12 informational**:
38 infeasible routes, 9 unsupported spans, 3 wire-clearance errors, 19 bend-radius
errors, 3 dynamic-clearance errors, and one each of wire/connector,
wire/component, connector/component and wire/wire intersection.

The provisional IMU lead intersects the Pi by **2.0 mm**, has a **136 mm**
unsupported span against a **62 mm** limit, and has **2.3 mm** moving-motor
clearance against **8 mm**. In `steering_sweep`, `W_IMU_VCC` reaches
**-0.13 mm** clearance to the gearbox against the unchanged **8 mm** requirement.
These findings were handed to lane A, which owns the final IMU mount and routes.
Other layout findings include encoder/gearbox clearance **6.5 mm vs 8 mm**,
`power_y.P_FUSE` / splice penetration **4.6 mm**, `power_y.SN` / battery
penetration **0.8 mm**, and power-Y bundle overlap **7.7 mm**.
The harness cannot be released with these findings. No route exemptions,
obstacle omissions, ratings, or scenario limits were loosened to hide them.

## CAD rebuild handoff

New or changed lane-B geometry requiring the later incremental rebuild:
HAT outline/connectors, owned GY-521 imported board model, IMU pedestal,
deck IMU bonding interface, 5 A fuse, and XT90H battery connector geometry.
The copied CAD also has inherited pending layout changes for the chassis,
GPS/RTK foam tapes, GPS, encoder, lead post, servo post B, receiver, and RTK
radio. Recompose was run offline before model validation. No live Onshape
features or API quota were used. Purchased parts must use their source models;
the provisional proxy is not a manufacturing model.

## Tests and commits

Full Python suite, `python -m pytest -q -p no:randomly tests`, on the final
committed implementation: **731 passed in 1672.20 s (27 min 52 s)**.
Web typecheck passes; Vitest **68 tests pass** across 7 files.
Focused checks passed for native PCB/connectors (31), firmware/traction/deploy
and B2 electrical behavior (35), electrical models/cable mass (7), and sensor /
firmware devices (16). Both integration-reported electrical tests pass in the
final full suite, as do the cable-mass and Hall-phase regressions.

| Commit | Cohesive change |
|---|---|
| `bf15554` | Coordinate 5 A HAT fuse and XT90H battery connector |
| `7a7b069` | Preserve input provenance during offline CAD recomposition |
| `f746ef4` | South-edge HAT interfaces and owned GY-521 joins/harness |
| `1322575` | Straight-stop software slip control and arbiter modulation |
| `85545fd` | Firmware defaults and external IMU frame alignment |
| `1727b30` | Exact ESD symbols and verified final native PCB routing |
| `9aa9367` | Merge updated integration and connector collision checks |
| `cf2eb36` | True DC equilibria and topological motor-lead identification |
| `794010f` | Correct cable mass across unequal MuJoCo spans |
| `50ddacf` | Hall pulse phase aligned with the nominal magnet |

Deployment build version is 3.3.1. Build outputs remain untracked; this report
records the validated measurements without committing caches or local paths.
