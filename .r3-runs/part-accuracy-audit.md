# RC-car part accuracy audit

Generated from the fresh model, wiring and connectors stages on `r3/part-accuracy`; baseline is immutable `13dc2ff`. SI values in JSON; dimensions below in mm.

This report does not certify unknown hardware. No parts outside Pi 5, GY-521 and camera provenance were corrected. Full source and caliper details: [part-accuracy guide](../docs/guide/part-accuracy.md).

## Per-pair build results

| Pair | Result | Physical counts | Worst lateral, mm | Insertion / required, mm | Diagnostics |
|---|---|---|---:|---:|---|
| battery.J1 ↔ battery_lead.A | unverifiable | battery.J1: unavailable, battery_lead.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| battery_lead.B ↔ power_y.P1 | unverifiable | battery_lead.B: unavailable, power_y.P1: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| hat.J_BATT ↔ power_y.P3 | unverifiable | hat.J_BATT: 0, power_y.P3: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| fuse.J1 ↔ power_y.F1 | unverifiable | fuse.J1: unavailable, power_y.F1: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| fuse.J2 ↔ power_y.F2 | unverifiable | fuse.J2: unavailable, power_y.F2: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| esc.J_PWR ↔ esc_power.A | unverifiable | esc.J_PWR: unavailable, esc_power.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| power_y.P2 ↔ esc_power.B | failed | power_y.P2: 2, esc_power.B: 2 | 0.000000 | 7.250000 / 6.300000 | connector_body_interference, connector_unverifiable |
| esc.J_MOT ↔ esc_motor.A | unverifiable | esc.J_MOT: unavailable, esc_motor.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| motor.J_POS ↔ esc_motor.BP | unverifiable | motor.J_POS: 0, esc_motor.BP: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| motor.J_NEG ↔ esc_motor.BN | unverifiable | motor.J_NEG: 0, esc_motor.BN: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| esc.J_SIG ↔ esc_signal.A | unverifiable | esc.J_SIG: unavailable, esc_signal.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| hat.J_ESC ↔ esc_signal.B | unverifiable | hat.J_ESC: 3, esc_signal.B: unavailable | — | — / — | connector_unverifiable |
| servo.J1 ↔ servo_lead.A | unverifiable | servo.J1: unavailable, servo_lead.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| hat.J_SERVO ↔ servo_lead.B | unverifiable | hat.J_SERVO: 3, servo_lead.B: unavailable | — | — / — | connector_unverifiable |
| receiver.J1 ↔ rx_lead.A | unverifiable | receiver.J1: unavailable, rx_lead.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| hat.J_RX ↔ rx_lead.B | unverifiable | hat.J_RX: 4, rx_lead.B: unavailable | — | — / — | connector_unverifiable |
| gps.J1 ↔ gps_lead.A | unverifiable | gps.J1: unavailable, gps_lead.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| hat.J_GPS ↔ gps_lead.B | unverifiable | hat.J_GPS: 4, gps_lead.B: unavailable | — | — / — | connector_unverifiable |
| gps.J2 ↔ rtk_radio_lead.A | unverifiable | gps.J2: unavailable, rtk_radio_lead.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| rtk_radio.J1 ↔ rtk_radio_lead.B | unverifiable | rtk_radio.J1: unavailable, rtk_radio_lead.B: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| encoder.J1 ↔ encoder_lead.A | unverifiable | encoder.J1: unavailable, encoder_lead.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| hat.J_ENC ↔ encoder_lead.B | unverifiable | hat.J_ENC: 3, encoder_lead.B: unavailable | — | — / — | connector_unverifiable |
| hat.J_IMU ↔ imu_lead.A | unverifiable | hat.J_IMU: 4, imu_lead.A: unavailable | — | — / — | connector_unverifiable |
| imu.J1 ↔ imu_lead.B | passed | imu.J1: 8 | 0.000000 | — / — | none |
| pi.J8 ↔ hat.J_GPIO | unverifiable | pi.J8: 40, hat.J_GPIO: 40 | 0.000003 | 6.085000 / 5.500000 | connector_unverifiable |

Pair totals: **1 passed, 1 failed, 23 unverifiable** (25 pairs). Previously 1 passed, 4 failed, 20 unverifiable.

The Pi/HAT has 40 contacts on each side and clears 5.5 mm insertion; open source surfaces prevent a complete housing-volume certification. The GY-521 has eight measured pads and all four occupied pads pass against the new routed wire ends. No old wire endpoints were reused for the final result.

### Corrected model suppliers and empty diagnostics

`hat.J_BATT` is `xt30pw-male`, explicitly `embedded: false`. The connector is
supplied separately by that type's `xt_male_pcb_ra` model, which the catalog marks
`source: proxy`. `motor.J_POS` and `motor.J_NEG` are `bullet-3.5-male`, with the
schema default `embedded: false`; their separate `bullet_male` model is also a
proxy. The motor body export does not supply resolved bullet contacts.

The previous three `connector_empty` errors were lookup/detail artefacts. The
stage now searches the component **plus the placed separate connector model**
for non-embedded connectors. Sourced contact geometry can verify a mate;
procedural proxy contacts cannot certify the purchased hardware. A simplified
model with surfaces but no resolvable contacts, or a missing/proxy supplier,
produces `connector_unverifiable`. A sourced model genuinely empty at the
specified location still produces blocking `connector_empty`. Embedded connectors
still require geometry in their owner mesh. No numeric threshold changed.

### XT60 housing collision: confirmed in the supplied models

The sole blocking pair is `power_y.P2` / `esc_power.B`. Its contact insertion is
**7.25 mm ≥ 6.30 mm**; that check passes. The independent housing test performs
solid intersection, not an insertion-depth comparison or envelope overlap.

Isolating only the two closed housing solids from the sourced KiCad STEP models
reproduces **7.467788 mm³** at their robot-frame placement. A second Boolean in
connector-local millimetres gives **8.3600 mm³**, concentrated at a central key/rib:
X = −0.5…+0.5 mm, Y = 2.0…3.3 mm, Z = 1.5…8.7 mm in the female connector frame.
Independent planar intersections at Z=2, 4 and 6 mm each give **1.3000 mm²** of
housing material overlap; at Z=8 mm they give **0.3000 mm²**. This confirms solid
material intersection beyond numerical face contact. The difference in Boolean
volume reflects tessellation/coordinate numerical sensitivity; both are far
above the unchanged 0.001 mm³ threshold. The ordinary nested shell walls do not
account for this volume. A regression fixture with properly nested hollow
housings returns zero overlap.

This establishes a real overlap **in the supplied models and placement**, not a
caliper-certified defect in the physical XT60 parts. Other open surfaces keep
the whole-pair collision completeness unverifiable. No XT60 geometry or layout
was changed; the blocking collision remains visible.

## Every purchased component with connectors

| Component | Connector(s) | Evidence / result |
|---|---|---|
| servo (servo-digital-20kg) | J1 | servo.J1: unverifiable |
| motor (motor-540-brushed) | J_POS, J_NEG | motor.J_POS: unverifiable; motor.J_NEG: unverifiable |
| encoder (hall-encoder-a1120) | J1 | encoder.J1: unverifiable |
| esc (esc-brushed-60a) | J_PWR, J_MOT, J_SIG | esc.J_PWR: unverifiable; esc.J_MOT: unverifiable; esc.J_SIG: unverifiable |
| battery (lipo-2s-5000-25c) | J1 | battery.J1: unverifiable |
| fuse (fuse-atc-5a) | J1, J2 | fuse.J1: unverifiable; fuse.J2: unverifiable |
| pi (raspberry-pi-5-4gb) | J8, CAM0, CAM1, PCIE, FAN, UART, BAT, POE, USB2_0, USB2_1, USB3_0, USB3_1, ETH, HDMI0, HDMI1, USB_C | pi.J8: unverifiable; pi.CAM0: not mated; no pair check; pi.CAM1: not mated; no pair check; pi.PCIE: not mated; no pair check; pi.FAN: not mated; no pair check; pi.UART: not mated; no pair check; pi.BAT: not mated; no pair check; pi.POE: not mated; no pair check; pi.USB2_0: not mated; no pair check; pi.USB2_1: not mated; no pair check; pi.USB3_0: not mated; no pair check; pi.USB3_1: not mated; no pair check; pi.ETH: not mated; no pair check; pi.HDMI0: not mated; no pair check; pi.HDMI1: not mated; no pair check; pi.USB_C: not mated; no pair check |
| imu (gy-521-mpu6050) | J1 | imu.J1: passed |
| receiver (sourced-expresslrs-radiomaster-rm-rp1) | J1 | receiver.J1: unverifiable |
| gps (gnss-rtk-zed-f9p) | J1, J2 | gps.J1: unverifiable; gps.J2: unverifiable |
| rtk_radio (telemetry-sik-915-100mw) | J1 | rtk_radio.J1: unverifiable |

Missing source/contact geometry is explicitly reported for receiver, GPS, RTK/SiK radio, servo, ESC, battery, fuse, encoder and proxy-only XT90/JST/servo/bullet harness halves. CAM0/CAM1 and camera FPCs are catalogued, but this project does not enumerate FPC cable mates in `mated_pairs()`; no fabricated passing pair is added.

## Raspberry Pi 5 per-connector evidence

All face measurements below use the official RP-010083-CA-1 STEP, not KiCad board footprints. Angle error is relative to the observed opening direction. A small face error does not imply numbered contacts have been independently verified.

| Connector | Pins | Face error, mm | Direction error, ° | Contact evidence / error |
|---|---:|---:|---:|---|
| J8 | 40 | 0.000000 | 0.000000 | 40 resolved centres; max 0.000002 mm |
| CAM0 | 22 | 0.000000 | 0.000000 | 22 resolved centres; max 0.000153 mm |
| CAM1 | 22 | 0.000000 | 0.000000 | 22 resolved centres; max 0.000152 mm |
| PCIE | 16 | 0.000000 | 0.000000 | 16 resolved centres; max 0.000153 mm |
| FAN | 4 | 0.000000 | 0.000000 | 4 resolved centres; max 0.012755 mm |
| UART | 3 | 0.000000 | 0.000000 | 3 resolved centres; max 0.012758 mm |
| BAT | 2 | 0.000000 | 0.000000 | 2 resolved centres; max 0.012758 mm |
| POE | 4 | 0.000000 | 0.000000 | 4 resolved centres; max 0.000002 mm; CT numbering estimated |
| USB2_0 | 4 | 0.000000 | 0.000000 | Spring datum projected from STEP; loaded contact datum / numbering estimated |
| USB2_1 | 4 | 0.000000 | 0.000000 | Spring datum projected from STEP; loaded contact datum / numbering estimated |
| USB3_0 | 9 | 0.000001 | 0.000000 | Spring datum projected from STEP; loaded contact datum / numbering estimated |
| USB3_1 | 9 | 0.000001 | 0.000000 | Spring datum projected from STEP; loaded contact datum / numbering estimated |
| ETH | 8 | 0.000003 | 0.000000 | Spring datum projected from STEP; loaded contact datum / numbering estimated |
| HDMI0 | 19 | 0.000000 | 0.000000 | Spring datum projected from STEP; loaded contact datum / numbering estimated |
| HDMI1 | 19 | 0.000000 | 0.000000 | Spring datum projected from STEP; loaded contact datum / numbering estimated |
| USB_C | 16 | 0.000000 | 0.000000 | Mating contacts absent from STEP; 16 standard positions estimated |

J8 connector origin: (-10,24.5,3.880) → (-10,24.5,3.876) mm. Mechanical join Z: 4.100 → 3.876 mm; HAT follows by -0.224 mm. Mounting-hole pattern: exactly 58 × 49 mm, Ø2.7 clearance for M2.5. The mechanical drawing supports the pin-1 dimension chain but does not independently dimension all 40 posts.

## Stage diagnostics before / after

The follow-up reran `run_stage(Project.load(path), name)` for model, wiring and
connectors after recomposition. Relative to the previous part-accuracy handoff:

| Stage | Before follow-up: errors / warnings | After follow-up: errors / warnings |
|---|---:|---:|
| model | 0 / 0 | 0 / 0 |
| wiring | 82 / 55 | 82 / 55 |
| connectors | 4 / 48 | 1 / 51 |

Only three connector empty errors change to three unverifiable warnings. The
full branch comparison to original integration `13dc2ff` follows:

| Stage | Severity / code | Before | After |
|---|---|---:|---:|
| model | errors / warnings | 0 / 0 | 0 / 0 |
| wiring | error: cable_unsupported_span | 9 | 11 |
| wiring | error: connector_intersects_component | 1 | 1 |
| wiring | error: route_infeasible | 38 | 40 |
| wiring | error: wire_bend_radius_violation | 19 | 21 |
| wiring | error: wire_clearance_violation | 3 | 3 |
| wiring | error: wire_dynamic_clearance_violation | 2 | 3 |
| wiring | error: wire_intersects_component | 1 | 1 |
| wiring | error: wire_intersects_connector | 1 | 1 |
| wiring | error: wire_intersects_wire | 1 | 1 |
| wiring | warning: edge_protection_required | 2 | 2 |
| wiring | warning: excess_slack_loop | 69 | 45 |
| wiring | warning: wire_bend_radius_violation | 10 | 8 |
| connectors | error: connector_body_interference | 0 | 1 |
| connectors | error: connector_empty | 0 | 0 |
| connectors | warning: component_model_dimension_mismatch | 1 | 1 |
| connectors | warning: component_model_unavailable | 1 | 1 |
| connectors | warning: connector_unverifiable | 0 | 40 |
| connectors | warning: wire_display_merged | 5 | 9 |

Changed-code explanations:

- `route_infeasible` 38→40, unsupported spans 9→11, bend errors 19→21: the HAT moved with the measured J8 datum; the IMU termination changed from packed pigtail conductors to four distinct pads on the 2.54 mm row. Fresh routing changes endpoint tangents, support spans and obstacle paths. These failures remain blocking; the routing/layout lane must resolve them.
- Bend warnings 10→8 and slack warnings 69→45: fresh routes change the curvature/slack samples; some bends now meet the error threshold instead. No threshold changed.
- Dynamic-clearance errors 2→3: moving wheel/gearbox clearance in the rerouted servo, encoder and RTK leads. Full diagnostic messages below preserve the affected wires and measured values.
- The XT60 connector error and 40 unverifiable warnings are additional physical geometry checks. Frame alignment checks still produce no errors; no wire-endpoint errors remain after fresh routing.
- Display-overlap warnings 5→9 reflect changed paths. They concern the rendered conductor separation; actual clearance diagnostics are listed independently.
- Existing unavailable servo model and Pi PCB-thickness warnings remain (one each).

## Recomposition comparison and mechanical handoff

Identical command in each source snapshot, using the same recorded Onshape CAD
inputs: `python -m r3_onshape.recompose examples/robots/rc-car`.
All three record source microversion `da62aa95171993d1aa890647` and recorded
generator digest `6a04f840408f5a07`.

| Source | Existing CAD errors | New versus base |
|---|---:|---:|
| Original integration base `13dc2ff` | 0 | 0 |
| Integration `c8802ea` | 0 | 0 |
| `r3/part-accuracy` | 17 | 17 |

The later integration commit `8aa8b3e` changes only the generality policy in
`.r3-agents/COMMON.md`; its code and design inputs are identical to `c8802ea`.

All 17 errors below are introduced by this branch's part data changes; none were
present on either integration baseline. The follow-up checker correction does
not alter the 17. No layout, router, standoff or `rc_car.yaml` edit was made.

| # | New code | Subjects | Measurement / message |
|---:|---|---|---|
| 1 | `fastener_head_not_seated` | fastener:imu_screws_1, imu | imu_screws_1: head/bearing face contact on imu is 0.00 mm^2 (< 30 % of 11.00 mm^2) |
| 2 | `thread_geometry_missing` | fastener:imu_screws_1, fastener:imu_screws_1, imu_mount | imu_screws_1: no coaxial thread bore in imu_mount or shank on fastener:imu_screws_1 |
| 3 | `fastener_head_not_seated` | fastener:imu_screws_2, imu | imu_screws_2: head/bearing face contact on imu is 0.00 mm^2 (< 30 % of 11.00 mm^2) |
| 4 | `thread_geometry_missing` | fastener:imu_screws_2, fastener:imu_screws_2, imu_mount | imu_screws_2: no coaxial thread bore in imu_mount or shank on fastener:imu_screws_2 |
| 5 | `fastener_joint_gap` | fastener:hat_1, hat, pi_standoff_1 | hat_1: hat and pi_standoff_1 are not in face contact around the fastener (0.000 mm^2) |
| 6 | `fastener_joint_gap` | fastener:hat_2, hat, pi_standoff_2 | hat_2: hat and pi_standoff_2 are not in face contact around the fastener (0.000 mm^2) |
| 7 | `fastener_joint_gap` | fastener:hat_3, hat, pi_standoff_3 | hat_3: hat and pi_standoff_3 are not in face contact around the fastener (0.000 mm^2) |
| 8 | `fastener_joint_gap` | fastener:hat_4, hat, pi_standoff_4 | hat_4: hat and pi_standoff_4 are not in face contact around the fastener (0.000 mm^2) |
| 9 | `connector_misaligned` | pi, hat | gpio_header: header collides with the socket |
| 10 | `cad_interference` | fastener:imu_screws_1, imu | fastener:imu_screws_1 and imu intersect by 22.625 mm^3 |
| 11 | `cad_interference` | fastener:imu_screws_2, imu | fastener:imu_screws_2 and imu intersect by 22.625 mm^3 |
| 12 | `cad_interference` | hat, pi | hat and pi intersect by 5.365 mm^3 |
| 13 | `cad_interference` | hat, pi_standoff_1 | hat and pi_standoff_1 intersect by 3.520 mm^3 |
| 14 | `cad_interference` | hat, pi_standoff_2 | hat and pi_standoff_2 intersect by 3.520 mm^3 |
| 15 | `cad_interference` | hat, pi_standoff_3 | hat and pi_standoff_3 intersect by 3.520 mm^3 |
| 16 | `cad_interference` | hat, pi_standoff_4 | hat and pi_standoff_4 intersect by 3.520 mm^3 |
| 17 | `part_not_retained` | cap_hat_1, cap_hat_2, cap_hat_3, cap_hat_4, fastener:hat_1, fastener:hat_2, fastener:hat_3, fastener:hat_4, fastener:imu_screws_1, fastener:imu_screws_2, hat, imu | 12 parts are not held to chassis by verified attachments: ['cap_hat_1', 'cap_hat_2', 'cap_hat_3', 'cap_hat_4', 'fastener:hat_1', 'fastener:hat_2', 'fastener:hat_3', 'fastener:hat_4', 'fastener:imu_screws_1', 'fastener:imu_screws_2', 'hat', 'imu'] |

The HAT follows the corrected mechanical J8 datum down **0.224 mm**. This causes
four `fastener_joint_gap` errors, `connector_misaligned` on `gpio_header`, and
five HAT/Pi/standoff `cad_interference` errors (10 diagnostics). The GY-521 model
replacement and changed mounting pattern cause two head-seat, two thread-bore,
and two screw/board-interference errors (6 diagnostics). Its nominal hole spacing
is **15.5 mm**, but the sourced STEP has **16.35 mm**; the 15.5 mm value remains
estimated. These six cannot honestly be attributed to hole pitch alone: board
thickness, pad datum and actual model holes changed too. The final
`part_not_retained` diagnostic aggregates 12 affected objects from both groups.

Assembly compilation separately reports four `assembly_misfit` errors at 0.224 mm
against a 0.050 mm limit and one `hole_pattern_mismatch` at 0.358 mm against a
0.250 mm allowance. They are not extra entries in the 17 recomposition errors.
The two unchanged CAD cleanliness tests expose these defects. The mechanical lane
owns their correction; no skips, xfails or relaxed limits were introduced.

## Generality audit

Read the generality policy added on integration at `8aa8b3e`. The pair checker
and embedded-post verifier have no robot/component IDs or component-kind gate.
Removed the solder type-name prefix dispatch and the assumed pin name `"1"`.
Solder dispatch now uses pad geometry, a harness owner and catalog gender `none`;
post verification accepts arbitrary pin identifiers. Removed Pi/HAT-specific
stage assumption text. No car/vehicle concepts determine mesh-check behavior.

The final synthetic suite has **22 passing cases**, including a custom actuator
with nonnumeric contacts and a custom sensor with arbitrarily named solder
termination types. It requires no rc-car model for those geometry checks.
The five release-gate tests continue to use the existing project-copy helper.

## Validation

Full command: `python -m pytest -q -p no:randomly tests`.

| Source | Passed | Failed | Warnings | Seconds |
|---|---:|---:|---:|---:|
| Original integration base `13dc2ff` | 734 | 0 | 0 | 1783.00 |
| Integration snapshot `c8802ea` (same code/tests as policy-only `8aa8b3e`) | 778 | 0 | 0 | 1746.56 |
| Final `r3/part-accuracy` follow-up, including generality tests | 784 | 2 | 8 | 1413.85 |

The branches contain different added test suites; totals are not a test-for-test
comparison. The final run collected 786 tests. Its only failures are:

- `tests/test_cad_phaseb.py::test_rc_car_compiles_with_every_object_joined`
- `tests/test_cad_phaseb.py::test_exact_interference_and_seats_are_clean`

Both expose the mechanical integration defects listed above. Their assertions
remain unchanged; the lookup/generality correction introduces no additional
failing test. Targeted mesh regressions: **22 passed**, 8 warnings, 17.46 s.
The eight warnings come from trimesh's volume/centroid calculation on empty
Boolean results, not additional R3 diagnostics.

Fresh model/wiring/connectors stages and the final connector-only rerun confirm
the per-pair and diagnostic counts above. Final source review confirms no
robot/component ID or component-kind gate in either mesh-check module. No
protected layout, router, connection-site or deployment-version file changed.

Earlier evidence remains valid: model-bank bbox validation succeeded with the
GY-521 status kept **unverified**; fresh-CAD/stale-catalog checks reject stale
contact evidence. The original implementation added Shapely to the models extra
alongside SciPy, rtree and manifold3d. This follow-up adds no dependencies.

## Changed wiring messages

### route_infeasible

- error: power_y: tangent cannot be met at (196, -37, 40) mm: required 16.0 mm, best 14.2 mm (power_y, W_Y_N_IN, standoff_fr)
- error: power_y: tangent cannot be met at (185, -30, 54) mm: required 17.7 mm, best 2.9 mm (power_y, W_Y_N_ESC, W_Y_N_HAT, battery, standoff_fr)
- error: power_y: tangent cannot be met at (177, -26, 39) mm: required 17.7 mm, best 9.3 mm (power_y, W_Y_N_ESC, W_Y_N_HAT, battery, fuse, chassis)
- error: power_y: tangent cannot be met at (176, -38, 39) mm: required 16.0 mm, best 12.6 mm (power_y, W_Y_P_ESC, fuse, chassis)
- error: power_y: tangent cannot be met at (123, -67, 77) mm: required 14.8 mm, best 5.4 mm (power_y, W_Y_N_HAT, W_Y_P_HAT)
- error: power_y: tangent cannot be met at (119, -71, 105) mm: required 14.8 mm, best 8.8 mm (power_y, W_Y_N_HAT, W_Y_P_HAT)
- error: servo_lead: curvature cannot be met at (171, -44, 70) mm: required 8.2 mm, best 7.1 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio, rtk_radio)
- error: servo_lead: curvature cannot be met at (161, -36, 71) mm: required 8.2 mm, best 5.7 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio, rtk_radio)
- error: servo_lead: curvature cannot be met at (145, -40, 68) mm: required 8.2 mm, best 5.7 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio)
- error: servo_lead: tangent cannot be met at (149, -43, 67) mm: required 8.2 mm, best 1.5 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio)
- error: servo_lead: static clearance cannot be met at (167, -41, 70) mm: required 1.2 mm, best -1.8 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio, rtk_radio)
- error: servo_lead: hardware clearance cannot be met at (167, -41, 70) mm: required 1.2 mm, best -1.8 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio, rtk_radio)
- error: servo_lead: static clearance cannot be met at (150, -41, 68) mm: required 1.2 mm, best -0.6 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio, rtk_radio)
- error: servo_lead: hardware clearance cannot be met at (149, -43, 67) mm: required 1.2 mm, best -2.9 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, tape_rtk_radio)
- error: servo_lead: tangent cannot be met at (98, -43, 76) mm: required 8.2 mm, best 1.9 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, lead_post)
- error: servo_lead: curvature cannot be met at (86, -82, 96) mm: required 8.2 mm, best 5.8 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- error: servo_lead: curvature cannot be met at (95, -45, 68) mm: required 8.2 mm, best 0.0 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, deck, lead_post)
- error: servo_lead: tangent cannot be met at (85, -70, 103) mm: required 8.2 mm, best 4.5 mm (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- error: rx_lead: curvature cannot be met at (161, 44, 82) mm: required 4.7 mm, best 0.0 mm (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX, deck)
- error: rx_lead: tangent cannot be met at (175, 39, 81) mm: required 4.7 mm, best 2.6 mm (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX, deck, receiver, tape_rx)
- error: rx_lead: tangent cannot be met at (146, 36, 89) mm: required 4.7 mm, best 3.2 mm (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX, pi, deck)
- error: rx_lead: static clearance cannot be met at (152, 45, 80) mm: required 1.2 mm, best -0.2 mm (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX, deck)
- error: rx_lead: tangent cannot be met at (82, -42, 83) mm: required 4.7 mm, best 3.0 mm (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX, cap_so_rr, deck, standoff_rr)
- error: rtk_radio_lead: tangent cannot be met at (30, -8, 94) mm: required 4.7 mm, best 2.8 mm (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2, gearbox, gps, tape_gps)
- error: rtk_radio_lead: tangent cannot be met at (42, -9, 89) mm: required 4.7 mm, best 0.9 mm (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2, deck)
- error: rtk_radio_lead: curvature cannot be met at (92, -34, 89) mm: required 4.7 mm, best 0.0 mm (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2, pi, hat, deck)
- error: imu_lead: tangent cannot be met at (106, -53, 103) mm: required 4.7 mm, best 0.6 mm (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, lead_post, hat)
- error: imu_lead: curvature cannot be met at (108, -43, 82) mm: required 4.7 mm, best 0.0 mm (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, deck, lead_post)
- error: imu_lead: curvature cannot be met at (90, -13, 95) mm: required 4.7 mm, best 0.0 mm (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, pi, hat)
- error: imu_lead: static clearance cannot be met at (108, -43, 82) mm: required 1.2 mm, best 0.6 mm (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, deck, lead_post)
- error: imu_lead: hardware clearance cannot be met at (108, -43, 82) mm: required 1.2 mm, best 0.6 mm (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, deck, lead_post)
- error: imu_lead: static clearance cannot be met at (89, -13, 89) mm: required 1.2 mm, best -2.2 mm (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, pi, hat, deck)

### cable_unsupported_span

- error: power_y: 174 mm between connector and connector with no support (limit 99 mm) (power_y, W_Y_P_HAT)
- error: power_y: 152 mm between power_y.S5 and connector with no support (limit 99 mm) (power_y, W_Y_N_HAT)
- error: servo_lead: 185 mm between connector and servo_lead.S3 with no support (limit 78 mm) (servo_lead, W_SRV_GND)
- error: servo_lead: 92 mm between grommet and connector with no support (limit 78 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- error: esc_signal: 128 mm between esc_signal.S2 and esc_signal.S3 with no support (limit 74 mm) (esc_signal, W_ESC_BEC, W_ESC_GND, W_ESC_SIG)
- error: encoder_lead: 146 mm between connector and connector with no support (limit 65 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC)
- error: gps_lead: 64 mm between connector and connector with no support (limit 62 mm) (gps_lead, W_GPS_GND)
- error: imu_lead: 148 mm between imu_lead.S1 and connector with no support (limit 62 mm) (imu_lead, W_IMU_SCL, W_IMU_SDA)
- error: rtk_radio_lead: 109 mm between rtk_radio_lead.S1 and rtk_radio_lead.S2 with no support (limit 62 mm) (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_TX2)
- error: rtk_radio_lead: 109 mm between rtk_radio_lead.S2 and rtk_radio_lead.S1 with no support (limit 62 mm) (rtk_radio_lead, W_RTK_RTCM)
- error: rx_lead: 107 mm between rx_lead.S1 and rx_lead.S2 with no support (limit 62 mm) (rx_lead, W_RX_GND)

### wire_bend_radius_violation

- warning: power_y bends at 17.5 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle (power_y, W_Y_P_IN)
- error: power_y bends at 3.9 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle (power_y, W_Y_N_ESC, W_Y_N_HAT, W_Y_P_ESC)
- error: power_y bends at 11.0 mm radius; minimum 14.8 mm for a 2-conductor 6.6 mm bundle (power_y, W_Y_N_HAT, W_Y_P_HAT)
- warning: power_y bends at 11.6 mm radius; minimum 13.2 mm for a 1-conductor 3.3 mm bundle (power_y, W_Y_N_HAT, W_Y_P_HAT)
- error: servo_lead bends at 5.7 mm radius; minimum 8.2 mm for a 3-conductor 3.7 mm bundle (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- error: servo_lead bends at 2.1 mm radius; minimum 8.2 mm for a 3-conductor 3.7 mm bundle (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- error: servo_lead bends at 1.5 mm radius; minimum 8.2 mm for a 3-conductor 3.7 mm bundle (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- error: servo_lead bends at 5.8 mm radius; minimum 8.2 mm for a 3-conductor 3.7 mm bundle (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- warning: servo_lead bends at 6.0 mm radius; minimum 7.2 mm for a 1-conductor 1.8 mm bundle (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC)
- error: imu_lead bends at 2.7 mm radius; minimum 4.7 mm for a 4-conductor 2.4 mm bundle (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC)
- error: imu_lead bends at 1.2 mm radius; minimum 4.0 mm for a 1-conductor 1.0 mm bundle (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC)
- error: imu_lead bends at 1.0 mm radius; minimum 4.0 mm for a 1-conductor 1.0 mm bundle (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC)
- error: rtk_radio_lead bends at 3.5 mm radius; minimum 4.7 mm for a 4-conductor 2.4 mm bundle (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2)
- warning: rx_lead bends at 4.2 mm radius; minimum 4.7 mm for a 4-conductor 2.4 mm bundle (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX)
- warning: rx_lead bends at 3.5 mm radius; minimum 4.0 mm for a 1-conductor 1.0 mm bundle (rx_lead, W_RX_5V, W_RX_PI_RX)
- error: rx_lead bends at 2.5 mm radius; minimum 4.0 mm for a 1-conductor 1.0 mm bundle (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX)

### wire_dynamic_clearance_violation

- error: servo_lead comes within 7.1 mm of moving wheel_fr during the slack simulation (required 8 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, wheel_fr)
- error: encoder_lead comes within 1.0 mm of moving gearbox during the slack simulation (required 8 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, gearbox)
- error: rtk_radio_lead comes within 6.4 mm of moving gearbox during the slack simulation (required 8 mm) (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2, gearbox)

### excess_slack_loop

- warning: power_y: 95 mm of cable between pins can reach moving knuckle_fr (excess reach 7.0 mm) (power_y, W_Y_N_HAT, W_Y_N_IN, W_Y_P_IN, knuckle_fr)
- warning: power_y: 180 mm of cable between pins can reach moving wheel_fr (excess reach 52.8 mm) (power_y, W_Y_N_HAT, W_Y_N_IN, W_Y_P_HAT, W_Y_P_IN, wheel_fr)
- warning: power_y: 180 mm of cable between pins can reach moving tie_rod_r (excess reach 38.6 mm) (power_y, W_Y_N_HAT, W_Y_P_HAT, tie_rod_r)
- warning: power_y: 197 mm of cable between pins can reach moving gearbox (excess reach 66.2 mm) (power_y, W_Y_N_HAT, W_Y_P_HAT, gearbox)
- warning: power_y: 197 mm of cable between pins can reach moving motor (excess reach 51.8 mm) (power_y, W_Y_N_HAT, W_Y_P_HAT, motor)
- warning: power_y: 197 mm of cable between pins can reach moving wheel_rr (excess reach 25.7 mm) (power_y, W_Y_P_HAT, wheel_rr)
- warning: power_y: 197 mm of cable between pins can reach moving enc_ring (excess reach 12.2 mm) (power_y, W_Y_P_HAT, enc_ring)
- warning: power_y: 197 mm of cable between pins can reach moving enc_magnet_2 (excess reach 12.1 mm) (power_y, W_Y_P_HAT, enc_magnet_2)
- warning: servo_lead: 204 mm of cable between pins can reach moving knuckle_fr (excess reach 35.8 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, knuckle_fr)
- warning: servo_lead: 204 mm of cable between pins can reach moving stub_fr (excess reach 6.0 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, stub_fr)
- warning: servo_lead: 204 mm of cable between pins can reach moving wheel_fr (excess reach 44.2 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, wheel_fr)
- warning: servo_lead: 204 mm of cable between pins can reach moving bs_kr (excess reach 32.1 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, bs_kr)
- warning: servo_lead: 206 mm of cable between pins can reach moving servo_output (excess reach 48.9 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, servo_output)
- warning: servo_lead: 206 mm of cable between pins can reach moving servo_horn (excess reach 50.5 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, servo_horn)
- warning: servo_lead: 206 mm of cable between pins can reach moving bs_hl (excess reach 49.3 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, bs_hl)
- warning: servo_lead: 206 mm of cable between pins can reach moving bs_hr (excess reach 49.9 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, bs_hr)
- warning: servo_lead: 206 mm of cable between pins can reach moving tie_rod (excess reach 41.6 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, tie_rod)
- warning: servo_lead: 206 mm of cable between pins can reach moving tie_rod_r (excess reach 48.4 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, tie_rod_r)
- warning: servo_lead: 151 mm of cable between pins can reach moving gearbox (excess reach 45.6 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, gearbox)
- warning: servo_lead: 151 mm of cable between pins can reach moving motor (excess reach 21.0 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, motor)
- warning: servo_lead: 151 mm of cable between pins can reach moving wheel_rr (excess reach 10.4 mm) (servo_lead, W_SRV_GND, W_SRV_SIG, W_SRV_VCC, wheel_rr)
- warning: servo_lead: 206 mm of cable between pins can reach moving knuckle_fl (excess reach 2.8 mm) (servo_lead, W_SRV_GND, knuckle_fl)
- warning: esc_signal: 76 mm of cable between pins can reach moving gearbox (excess reach 10.5 mm) (esc_signal, W_ESC_BEC, W_ESC_GND, W_ESC_SIG, gearbox)
- warning: encoder_lead: 163 mm of cable between pins can reach moving gearbox (excess reach 74.6 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, gearbox)
- warning: encoder_lead: 163 mm of cable between pins can reach moving motor (excess reach 74.4 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, motor)
- warning: encoder_lead: 163 mm of cable between pins can reach moving bearing_rr (excess reach 9.0 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, bearing_rr)
- warning: encoder_lead: 163 mm of cable between pins can reach moving axle_rl (excess reach 0.9 mm) (encoder_lead, W_ENC_VCC, axle_rl)
- warning: encoder_lead: 163 mm of cable between pins can reach moving axle_rr (excess reach 38.0 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, axle_rr)
- warning: encoder_lead: 163 mm of cable between pins can reach moving wheel_rr (excess reach 42.8 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, wheel_rr)
- warning: encoder_lead: 163 mm of cable between pins can reach moving enc_ring (excess reach 72.5 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, enc_ring)
- warning: encoder_lead: 163 mm of cable between pins can reach moving enc_magnet_1 (excess reach 57.7 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, enc_magnet_1)
- warning: encoder_lead: 163 mm of cable between pins can reach moving enc_magnet_2 (excess reach 70.8 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, enc_magnet_2)
- warning: encoder_lead: 163 mm of cable between pins can reach moving enc_magnet_3 (excess reach 62.6 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, enc_magnet_3)
- warning: encoder_lead: 163 mm of cable between pins can reach moving enc_magnet_4 (excess reach 69.1 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, W_ENC_VCC, enc_magnet_4)
- warning: encoder_lead: 163 mm of cable between pins can reach moving spacer_rr (excess reach 4.5 mm) (encoder_lead, W_ENC_GND, W_ENC_OUT, spacer_rr)
- warning: gps_lead: 77 mm of cable between pins can reach moving gearbox (excess reach 28.5 mm) (gps_lead, W_GPS_5V, W_GPS_GND, W_GPS_PI_RX, W_GPS_PI_TX, gearbox)
- warning: imu_lead: 175 mm of cable between pins can reach moving gearbox (excess reach 53.2 mm) (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, gearbox)
- warning: imu_lead: 175 mm of cable between pins can reach moving motor (excess reach 33.5 mm) (imu_lead, W_IMU_GND, W_IMU_SCL, W_IMU_SDA, W_IMU_VCC, motor)
- warning: rtk_radio_lead: 123 mm of cable between pins can reach moving gearbox (excess reach 36.9 mm) (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2, gearbox)
- warning: rtk_radio_lead: 123 mm of cable between pins can reach moving motor (excess reach 23.2 mm) (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2, motor)
- warning: rtk_radio_lead: 123 mm of cable between pins can reach moving enc_ring (excess reach 2.4 mm) (rtk_radio_lead, W_RTK_5V, W_RTK_GND, W_RTK_RTCM, W_RTK_TX2, enc_ring)
- warning: rx_lead: 122 mm of cable between pins can reach moving gearbox (excess reach 3.6 mm) (rx_lead, W_RX_5V, W_RX_GND, W_RX_PI_RX, W_RX_PI_TX, gearbox)


## Implementation commits

- `3bf3444` — fresh placed-mesh mating checks, diagnostics, geometry dependencies and negative/release-gate tests.
- `baf7103` — Pi/GY-521/camera catalog evidence, vendor surfaces, source comparisons and accuracy tests.
- `c6ef763` — the separately committed imu_lead.B type change; no other robot.yaml edits.
