# RC-car part accuracy audit

Generated from the fresh model, wiring and connectors stages on `r3/part-accuracy`; baseline is immutable `13dc2ff`. SI values in JSON; dimensions below in mm.

This report does not certify unknown hardware. No parts outside Pi 5, GY-521 and camera provenance were corrected. Full source and caliper details: [part-accuracy guide](../docs/guide/part-accuracy.md).

## Per-pair build results

| Pair | Result | Physical counts | Worst lateral, mm | Insertion / required, mm | Diagnostics |
|---|---|---|---:|---:|---|
| battery.J1 ↔ battery_lead.A | unverifiable | battery.J1: unavailable, battery_lead.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| battery_lead.B ↔ power_y.P1 | unverifiable | battery_lead.B: unavailable, power_y.P1: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| hat.J_BATT ↔ power_y.P3 | failed | hat.J_BATT: 0, power_y.P3: unavailable | — | — / — | connector_unverifiable, connector_empty |
| fuse.J1 ↔ power_y.F1 | unverifiable | fuse.J1: unavailable, power_y.F1: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| fuse.J2 ↔ power_y.F2 | unverifiable | fuse.J2: unavailable, power_y.F2: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| esc.J_PWR ↔ esc_power.A | unverifiable | esc.J_PWR: unavailable, esc_power.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| power_y.P2 ↔ esc_power.B | failed | power_y.P2: 2, esc_power.B: 2 | 0.000000 | 7.250000 / 6.300000 | connector_body_interference, connector_unverifiable |
| esc.J_MOT ↔ esc_motor.A | unverifiable | esc.J_MOT: unavailable, esc_motor.A: unavailable | — | — / — | connector_unverifiable, connector_unverifiable |
| motor.J_POS ↔ esc_motor.BP | failed | motor.J_POS: 0, esc_motor.BP: unavailable | — | — / — | connector_unverifiable, connector_empty |
| motor.J_NEG ↔ esc_motor.BN | failed | motor.J_NEG: 0, esc_motor.BN: unavailable | — | — / — | connector_unverifiable, connector_empty |
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

Pair totals: {'unverifiable': 20, 'failed': 4, 'passed': 1}.

The Pi/HAT has 40 contacts on each side and clears 5.5 mm insertion; open source surfaces prevent a complete housing-volume certification. The GY-521 has eight measured pads and all four occupied pads pass against the new routed wire ends. No old wire endpoints were reused for the final result.

Blocking mesh findings:

- hat.J_BATT: No part surface intersects the connector search volume.
- power_y.P2 / esc_power.B: housings/part bodies intersect by 7.468 mm^3 outside contact channels
- motor.J_POS: Part geometry is present but no contact posts, rings or entry openings can be resolved.
- motor.J_NEG: Part geometry is present but no contact posts, rings or entry openings can be resolved.

The two motor entries have body surfaces but no resolved contacts in their declared mating volumes. These are missing-geometry findings, not proof that the physical motor lacks terminals. The XT60 models have some open surfaces; 7.468 mm³ is the measured closed-solid overlap outside contact corridors, and the total collision check remains incomplete.

## Every purchased component with connectors

| Component | Connector(s) | Evidence / result |
|---|---|---|
| servo (servo-digital-20kg) | J1 | servo.J1: unverifiable |
| motor (motor-540-brushed) | J_POS, J_NEG | motor.J_POS: failed; motor.J_NEG: failed |
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
| connectors | error: connector_empty | 0 | 3 |
| connectors | warning: component_model_dimension_mismatch | 1 | 1 |
| connectors | warning: component_model_unavailable | 1 | 1 |
| connectors | warning: connector_unverifiable | 0 | 37 |
| connectors | warning: wire_display_merged | 5 | 9 |

Changed-code explanations:

- `route_infeasible` 38→40, unsupported spans 9→11, bend errors 19→21: the HAT moved with the measured J8 datum; the IMU termination changed from packed pigtail conductors to four distinct pads on the 2.54 mm row. Fresh routing changes endpoint tangents, support spans and obstacle paths. These failures remain blocking; the routing/layout lane must resolve them.
- Bend warnings 10→8 and slack warnings 69→45: fresh routes change the curvature/slack samples; some bends now meet the error threshold instead. No threshold changed.
- Dynamic-clearance errors 2→3: moving wheel/gearbox clearance in the rerouted servo, encoder and RTK leads. Full diagnostic messages below preserve the affected wires and measured values.
- New connector errors and 37 unverifiable warnings are additional physical geometry checks. Frame alignment checks still produce no errors; no wire-endpoint errors remain after fresh routing.
- Display-overlap warnings 5→9 reflect changed paths. They concern the rendered conductor separation; actual clearance diagnostics are listed independently.
- Existing unavailable servo model and Pi PCB-thickness warnings remain (one each).

## Mechanical integration blockers (checks retained)

- `hole_pattern_mismatch`: IMU mount/board centres differ by 0.358 mm; allowed 0.250 mm. Do not fabricate this mount until the owned board is measured.
- Four `assembly_misfit` errors: HAT/standoff loops miss by 0.224 mm; allowed 0.050 mm. The mechanical lane must shorten/reconcile its standoff stack with the measured header datum.
- Recomposition reports 17 CAD errors, including HAT/Pi/standoff interference, fastener seating/thread evidence and retention. No layout, router, standoff or `rc_car.yaml` edit was made here.
- Two existing `test_cad_phaseb` cleanliness tests fail on these real assembly defects. Their assertions and tolerances remain unchanged.

## Validation

- Baseline full suite: **734 passed**, 1783.00 s, immutable source snapshot of 13dc2ff.
- New geometry, purchased-part, catalog-refinement and release-gate tests: **44 passed**.
- Full after run: **775 passed, 3 failed**, 6 warnings, 1570.16 s. Two failures are the unchanged mechanical assembly cleanliness checks above. The third was an obsolete generic-FPC type assertion, imported before that test was corrected to require the measured vertical socket and +Z exit. Its complete catalog test file was rerun: **26 passed**.
- Final catalog/accuracy checks after the spring-end data refinement: **33 passed**.
- Final rerun of all three full-suite failures: **1 passed, 2 failed** (10.02 s). The FPC expectation is fixed; only the two unchanged mechanical assembly tests remain failing.
- Fresh CAD / stale catalog-revision smoke check: passed; stale placed exports are not accepted as current contact evidence.
- GY-521 model-bank validator: successful bbox validation; status remains **unverified**, no key-feature pass fabricated.
- Added runtime dependency: Shapely (2.2.0 installed in the shared venv). SciPy, rtree and manifold3d are declared in the models extra alongside existing trimesh/cascadio dependencies.

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
