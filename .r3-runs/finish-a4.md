# A4 working checkpoint — not released

This is an in-progress recovery checkpoint, not the final acceptance report.
No Onshape calls have been made. The integration merges remain pending the
integrator's instruction.

## Commits

- 189d859 Route branched harnesses in independently reserved lanes
- 56dab46 Select intact supplied STEP bodies with isolated model caches
- 663634c Separate routing lanes by physical pin pair
- ad29231 Size independent lane approach runs from their own pins
- cc1fae6 Read archived supplied STEP models and spherical faces offline
- a3f8ae5 Preserve oriented face multiplicity when clustering routing meshes
- 3362ce3 Preserve conductor approach dimensions through curvature planning
- cfbcbf3 Record official DS3218 solid dimensions and mounting surfaces
- 1c77e1a Keep offline blind cuts at their authored depth
- af69e6f Pin recorded corner fit tests to their assembly frames
- 01c94b8 Verify declared clamped spacers across assembly seat offsets

## Latest measured stage results

| Stage | A3 handoff | Latest A4 evidence |
|---|---:|---|
| Assembly | 0 errors | 0 errors; 193 exact solids, 125 objects, 148 joins |
| Model | 0 errors | 0 errors; 2.144314 kg, 55.686 g below 2.2 kg limit |
| Offline recompose | 0 errors | 11 errors before the latest horn-clearance/longer-screw revision |
| Canonical CAD | 1 error | Official model dimensions corrected; canonical stage not yet rerun |
| Connectors | 1 error | Inline ESC XT60 removed; fresh stage running |
| Electrical | 0 errors | 0 errors on the preceding layout; final run pending |
| Wiring | 43 errors, 5/12 valid | Battery input subset: 2 errors, battery_lead valid; complete power subset running |

The installed harness count is now **11**: esc_power is consolidated into
power_y by soldering its leads directly to esc.J_PWR. There is no inline XT60
pair at that location. The original shared-core exemption is unchanged.

The full power-system geometry and conductor-by-conductor bend analysis are
in [finish-a4-power-layout.md](finish-a4-power-layout.md). The official purchased
DS3218 measurements and the lower-ranked DS3218MG discrepancy are in
[finish-a4-servo.md](finish-a4-servo.md). Only the Pi, GY-521 and UC-717 are owned;
no physical servo measurements are awaited.

## Harness/scenario acceptance still pending

The battery_lead passed the planned focused subset after the conductor-lane
approach correction. power_y's two remaining input-trunk failures were measured
splice-sleeve penetrations of 3.756 and 3.388 mm, caused by doubled-back paths.
Those splices are now aligned with the XT90 pins. A complete power subset is
running on the enlarged 420 mm wheelbase layout. The other nine installed
harnesses have not been rerun on this layout and are not claimed valid.

None of the seven required scenarios has final-layout A4 evidence yet:
autonomous_course, full_throttle, range_failsafe, low_battery, steering_sweep,
top_speed_failsafe, pi_hang. A3's steering_sweep was -0.21 mm to wheel_fl against
8 mm required. Final minimum-clearance measurements remain pending.

## Source-model limitation

The original official STEP's output gear/shaft and upper housing overlap by
1054.320808 mm³ in native Open CASCADE. The housing has no corresponding analytic
bearing bore. The detailed supplied thread is also segmented: the fit check
reports a maximum individual cylindrical-face overlap of 0.450 mm. With the
revised M3x12 screw, measured thread volume is 10.151 mm³, equivalent to 4.700 mm
by the current smooth-bore model (4.500 mm required). The volume/individual-face
comparison still fails. Neither the supplied solids nor the check tolerances
have been altered. The external mount fixture passes; the revised horn seat has
10.561 mm² contact with the actual shaft tip.

## Tests so far

- Independent-lane/fan-out/waypoint/planner/bend regressions: 11 passed.
- Model-bank selection/interface/core tests: 19 passed.
- Blind cut and pinned recorded fit tests: 10 passed.
- Declared-spacer and assembly compiler tests: 35 passed.
- Current web typecheck and production build pass; 69 Vitest tests pass.
- Loader tests: 4 passed.
- Initial full pytest run interrupted after 294 passed and 4 failures in
  1213.43 s. The recorded-fixture failures are repaired; assembly seat warnings
  were subsequently corrected and require the final full-suite rerun.
- Initial full Playwright run: 17 passed, 8 failed. The ambiguous ESC motor
  selector is corrected; timed failures remain unresolved. Machine load was
  150–218 on 16 cores during overlapping validation jobs. No timeout was raised.

## Pending Onshape rebuild (latest recompose report)

- antenna-mast-petg: antenna_mast
- arducam-b0445-imx296: front_camera
- arducam-fpc-22-22-150mm: camera_fpc
- chassis-plate-al6061@31e18550: chassis
- encoder-lead-post-petg: encoder_lead_post
- foam-tape-3m-4026@98b66ba2: tape_gps
- foam-tape-3m-4026@a1375a8e: camera_tape
- foam-tape-3m-4026@bf9287ae: tape_rtk_radio
- fuse-atc-7.5a: fuse
- gnss-riser-petg: gnss_riser
- gnss-rtk-zed-f9p: gps
- gy-521-mpu6050: imu
- hall-encoder-a1120: encoder
- imu-lead-post-petg: imu_corner_post, imu_outboard_post
- imu-pedestal-petg: imu_mount
- imu-screw-cap-1-petg: cap_imu_1
- imu-screw-cap-2-petg: cap_imu_2
- lead-post-petg: lead_post
- motor-lead-post-petg: motor_lead_post
- power-lead-post-petg: power_lead_post
- r3-carrier-hat: hat
- raspberry-pi-5-8gb: pi
- rc-camera-mount-petg: camera_mount
- receiver-riser-petg: receiver_riser
- screw-cap-m2-petg: cap_camera_1, cap_camera_2, cap_camera_3, cap_camera_4
- sensor-rtk-mast-petg: sensor_lead_post
- servo-approach-post-petg: servo_approach_post
- servo-climb-post-petg: servo_climb_post
- servo-digital-20kg: servo
- servo-ear-clamp-pa12: servo_clamp_a, servo_clamp_b
- servo-horn-al7075: servo_horn
- servo-output-25t: servo_output
- servo-post-al6061: servo_post_b
- servo-post-al6061-cable: servo_post_a
- servo-south-post-petg: imu_south_post
- servo-upper-post-petg: servo_upper_post
- shcs-m2x8: fastener:camera_screws_1, fastener:camera_screws_2, fastener:camera_screws_3, fastener:camera_screws_4
- shcs-m3x16: fastener:servo_clamp_a_1, fastener:servo_clamp_a_2, fastener:servo_clamp_b_1, fastener:servo_clamp_b_2
- shim-din988-3x6x0.3: pi_shim_1, pi_shim_2, pi_shim_3, pi_shim_4
- sourced-expresslrs-radiomaster-rm-rp1: receiver
- telemetry-sik-915-100mw: rtk_radio
- upper-deck-petg@1815c459: deck
