# A3 handoff — release blocked

Branch: `r3/finish-a3`. **Not released: 5/12 harnesses valid; 43 wiring errors remain.** No live Onshape calls were made. No purchased housing was sculpted, and no fit, bend, collision or insertion limit was relaxed.

## Completed mechanical work

The 17 measured-part recomposition failures are repaired through joins, shims, a derived IMU pedestal pattern and printed cap-rim reliefs. Offline recomposition, assembly and model each have zero errors. The two CAD assembly-cleanliness tests pass without changes since the measured-part merge 3f6b20f. An earlier camera commit explicitly expects its required estimated-pattern warning; no fit/interference assertion was weakened.

The GY-521 base entry uses its downloaded STEP’s 16.35 mm pattern, with estimated provenance and the 15.5/16.35/17.05 mm source spread. `1bed937` corrects that mismatch; `68546ab` restores only the two derived cap-seat interfaces unintentionally dropped by part-accuracy. No installation variant exists. Both caps use ordinary adhesive joins and fit/retention checks. Each has 100% contact, over 2.869 and 2.098 mm². Both M2.5 screws have 7.006 mm engagement against 5 mm required. The future joint-physics layer is not on integration at b630f8b and has not been claimed as tested. See [IMU proof](finish-a3-imu-fit.md).

The Pi stack uses the measured 1.276 mm PCB, four 0.3 mm DIN 988 shims and the existing 11 mm standoffs. A 0.230 mm socket seating offset closes the joins, leaves zero interference in the fit model, and gives 5.770 mm measured GPIO engagement against 5.5 mm required. The rounded offline header gives 5.774 mm; that 4 µm distinction is explicit. See [Pi proof](finish-a3-pi-stack.md).

The owned front-facing camera is mounted through joins and capped screws. Its four 2.5 mm holes on a 30 × 30 mm square are still unconfirmed; the mount copies one parameterised camera interface. `mount_pattern_estimated` keeps it provisional. The flexible camera cable endpoints fit, but its complete installed bends/restraints/swept route are still unverified and `flat_cable_route_estimated` blocks release. See [camera report](finish-a3-camera.md).

Motor pigtails are electrically verified 18 AWG with separate physically restrained paths. Encoder, servo and IMU routes have printed collars/posts; GPS has a saddle shelf. The encoder/GPS/RTK subset passed with all three valid and 8.970975 mm minimum moving-part clearance. Isolated results are not a claim of full-assembly release.

## External and representation blockers

- XT60 `power_y.P2 / esc_power.B`: 8.360045 mm³ housing overlap in the installed supplied meshes. Insertion is 7.25 mm against 6.30 mm required. The largest permissible axial gap, 0.95 mm, still leaves 7.125 mm³ overlap. A half-turn makes the fit worse. The files match current upstream KiCad; the other public supplier pair evaluated has 651.844886 mm³ overlap and no separate contacts. No acceptable replacement pair was found. See [source-model probes](finish-a3-xt60.md).
- The canonical CAD stage additionally retains the pre-existing purchased-servo model-bank mismatch: measured 65.191703 × 48.099999 × 20.039128 mm, expected 20 × 54 × 45.5 mm, tolerance 1.5 mm. Zero recomposition errors must not be confused with zero canonical CAD errors.
- The current branch representation has no finite-width conductor lay between branches inside one routing group. At 6 mm arc length, two opposite minimum-radius 12 AWG turns can separate only 2.223756 mm, leaving 1.776244 mm overlap outside the shared-core exemption. Independently separated branch lanes or physical conductor lay are required; the exemption must not grow. See [.r3-agents/requests/wiring.md](../.r3-agents/requests/wiring.md).
- Owned GY-521 and camera hole measurements remain pending. Their mounts remain estimated, and camera ribbon routing remains unverified.

## Generality review

Reviewed `git diff r3/integration -- packages services integrations apps`. A3 connector dimensions moved from Python into `connector_proxies.yaml`; support-match and capture distances moved into the support catalog unchanged. New routing/support code derives dimensions from the catalog, wires and grid, and has no rc-car IDs or vehicle branches. The hole-pattern transform, supplied-STEP loader, estimated-mount diagnostic and fresh-build fabricated-solid fallback use generic fixtures. See [audit](finish-a3-generality.md).

## Native CAD rebuild/import list

The following are the recomposition report’s pending native rebuild/import entries relative to the copied assembly; this includes purchased imports and merged lane-B/part-accuracy changes as well as A3 printed geometry:

- `antenna-mast-petg (plan changed, 470.903 mm^3) -> ['antenna_mast']`
- `arducam-b0445-imx296 (new part, None mm^3) -> ['front_camera']`
- `arducam-fpc-22-22-150mm (new part, None mm^3) -> ['camera_fpc']`
- `chassis-plate-al6061@3e279b97 (new part, None mm^3) -> ['chassis']`
- `encoder-lead-post-petg (new part, None mm^3) -> ['encoder_lead_post']`
- `foam-tape-3m-4026@98b66ba2 (new part, None mm^3) -> ['tape_gps']`
- `foam-tape-3m-4026@a1375a8e (new part, None mm^3) -> ['camera_tape']`
- `foam-tape-3m-4026@bf9287ae (new part, None mm^3) -> ['tape_rtk_radio']`
- `fuse-atc-7.5a (new part, None mm^3) -> ['fuse']`
- `gnss-riser-petg (new part, None mm^3) -> ['gnss_riser']`
- `gnss-rtk-zed-f9p (new part, None mm^3) -> ['gps']`
- `gy-521-mpu6050 (supplied STEP, None mm^3) -> ['imu']`
- `hall-encoder-a1120 (plan changed, 1344.0 mm^3) -> ['encoder']`
- `imu-lead-post-petg (new part, None mm^3) -> ['imu_corner_post', 'imu_outboard_post']`
- `imu-pedestal-petg (new part, None mm^3) -> ['imu_mount']`
- `imu-screw-cap-1-petg (new part, None mm^3) -> ['cap_imu_1']`
- `imu-screw-cap-2-petg (new part, None mm^3) -> ['cap_imu_2']`
- `lead-post-petg (new part, None mm^3) -> ['lead_post']`
- `motor-lead-post-petg (new part, None mm^3) -> ['motor_lead_post']`
- `power-lead-post-petg (new part, None mm^3) -> ['power_lead_post']`
- `r3-carrier-hat (plan changed, 1404.0 mm^3) -> ['hat']`
- `raspberry-pi-5-8gb (plan changed, 1524.502 mm^3) -> ['pi']`
- `rc-camera-mount-petg (new part, None mm^3) -> ['camera_mount']`
- `receiver-riser-petg (new part, None mm^3) -> ['receiver_riser']`
- `screw-cap-m2-petg (new part, None mm^3) -> ['cap_camera_1', 'cap_camera_2', 'cap_camera_3', 'cap_camera_4']`
- `sensor-rtk-mast-petg (new part, None mm^3) -> ['sensor_lead_post']`
- `servo-approach-post-petg (new part, None mm^3) -> ['servo_approach_post']`
- `servo-climb-post-petg (new part, None mm^3) -> ['servo_climb_post']`
- `servo-post-al6061-cable (new part, None mm^3) -> ['servo_post_b']`
- `servo-south-post-petg (new part, None mm^3) -> ['imu_south_post']`
- `servo-upper-post-petg (new part, None mm^3) -> ['servo_upper_post']`
- `shcs-m2x8 (new part, None mm^3) -> ['fastener:camera_screws_1', 'fastener:camera_screws_2', 'fastener:camera_screws_3', 'fastener:camera_screws_4']`
- `shim-din988-3x6x0.3 (new part, None mm^3) -> ['pi_shim_1', 'pi_shim_2', 'pi_shim_3', 'pi_shim_4']`
- `sourced-expresslrs-radiomaster-rm-rp1 (new part, None mm^3) -> ['receiver']`
- `telemetry-sik-915-100mw (new part, None mm^3) -> ['rtk_radio']`
- `upper-deck-petg@1815c459 (new part, None mm^3) -> ['deck']`


## Final integrated validation

The comparable measured-model baseline before the final support/reservation follow-up had 52 wiring errors and 4/12 valid harnesses. The original user reports (75, then 82 after new checks) refer to earlier revisions; they are not the same baseline.

| Stage | Measured-model baseline | Final |
|---|---:|---:|
| Assembly | 0 | 0 |
| Model | 0 | 0 |
| Offline CAD recomposition | 0 (17 reported immediately after part-accuracy) | 0 |
| Canonical CAD stage | 1 existing servo-bank mismatch | 1, unchanged |
| Wiring | 52 | 43 |
| Connectors | 7 (6 stale HAT STEP findings plus XT60) | 1 |
| Electrical | 0 | 0 |

The HAT STEP was refreshed using KiCad's native headless export from the committed PCB; the PCB hash was unchanged. No PCB editing or live Onshape access occurred. The wiring stage took 3400.44 s. Its manufacturing status is **UNRELEASED**. There is no remaining pending-HAT-header exception: the relocated HAT ends clear the Pi, and the remaining failures below still block fabrication.

Wiring error counts: {'route_infeasible': 16, 'cable_support_unplaceable': 4, 'cable_unsupported_span': 5, 'connector_clearance_violation': 1, 'wire_intersects_wire': 1, 'wire_bend_radius_violation': 15, 'cable_sags_into_moving_part': 1}.

| Harness | Valid | Remaining diagnostic codes (counts) |
|---|---|---|
| `power_y` | false | `route_infeasible` × 10, `cable_unsupported_span` × 1, `wire_intersects_wire` × 1, `wire_bend_radius_violation` × 9 |
| `battery_lead` | false | `route_infeasible` × 2, `cable_unsupported_span` × 1, `connector_clearance_violation` × 1, `wire_bend_radius_violation` × 3, `cable_sags_into_moving_part` × 1 |
| `esc_power` | false | `route_infeasible` × 1, `wire_bend_radius_violation` × 2 |
| `servo_lead` | false | `cable_support_unplaceable` × 1, `cable_unsupported_span` × 1 |
| `esc_signal` | false | `route_infeasible` × 2, `cable_unsupported_span` × 1, `wire_bend_radius_violation` × 1 |
| `esc_motor` | true | none |
| `esc_motor_upper` | true | none |
| `encoder_lead` | true | none |
| `gps_lead` | true | none |
| `imu_lead` | false | `cable_support_unplaceable` × 3, `cable_unsupported_span` × 1 |
| `rtk_radio_lead` | false | `route_infeasible` × 1 |
| `rx_lead` | true | none |

### Exact remaining harness findings

**power_y**

- `route_infeasible`: power_y: curvature cannot be met at (168, -21, 75) mm: required 18.0 mm, best 0.0 mm
- `route_infeasible`: power_y: tangent cannot be met at (172, -23, 69) mm: required 18.1 mm, best 5.6 mm
- `route_infeasible`: power_y: tangent cannot be met at (175, -7, 101) mm: required 18.0 mm, best 3.6 mm
- `route_infeasible`: power_y: tangent cannot be met at (190, -35, 52) mm: required 18.0 mm, best 4.5 mm
- `route_infeasible`: power_y: tangent cannot be met at (203, -34, 53) mm: required 17.7 mm, best 2.1 mm
- `route_infeasible`: power_y: hardware clearance cannot be met at (195, -35, 45) mm: required 1.2 mm, best 0.4 mm
- `route_infeasible`: power_y: tangent cannot be met at (202, -37, 39) mm: required 18.0 mm, best 9.8 mm
- `route_infeasible`: power_y: tangent cannot be met at (187, -8, 70) mm: required 16.0 mm, best 9.8 mm
- `route_infeasible`: power_y: tangent cannot be met at (182, 3, 70) mm: required 18.0 mm, best 11.7 mm
- `route_infeasible`: power_y: tangent cannot be met at (125, -65, 84) mm: required 13.2 mm, best 7.9 mm
- `cable_unsupported_span`: power_y: 119 mm between power_y.S6 and connector with no support (limit 99 mm)
- `wire_intersects_wire`: power_y bundle surfaces overlap by 4.3 mm
- `wire_bend_radius_violation`: power_y bends at 6.7 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle
- `wire_bend_radius_violation`: power_y bends at 7.5 mm radius; minimum 18.1 mm for a 3-conductor 8.1 mm bundle
- `wire_bend_radius_violation`: power_y bends at 5.3 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle
- `wire_bend_radius_violation`: power_y bends at 8.4 mm radius; minimum 18.0 mm for a 2-conductor 8.0 mm bundle
- `wire_bend_radius_violation`: power_y bends at 3.1 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle
- `wire_bend_radius_violation`: power_y bends at 6.7 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle
- `wire_bend_radius_violation`: power_y bends at 9.4 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle
- `wire_bend_radius_violation`: power_y bends at 10.5 mm radius; minimum 14.8 mm for a 2-conductor 6.6 mm bundle
- `wire_bend_radius_violation`: power_y bends at 4.5 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle

**battery_lead**

- `route_infeasible`: battery_lead: curvature cannot be met at (229, 26, 40) mm: required 18.0 mm, best 0.0 mm
- `route_infeasible`: battery_lead: tangent cannot be met at (229, 26, 40) mm: required 18.0 mm, best 0.5 mm
- `cable_unsupported_span`: battery_lead: 128 mm between battery_lead.S1 and floating with no support (limit 100 mm)
- `connector_clearance_violation`: connector battery_lead.B body is 7.8 mm from moving wheel_fl (required 8 mm)
- `wire_bend_radius_violation`: battery_lead bends at 0.8 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle
- `wire_bend_radius_violation`: battery_lead bends at 5.8 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle
- `wire_bend_radius_violation`: battery_lead bends at 6.1 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle
- `cable_sags_into_moving_part`: battery_lead cable enters the wheel_fl envelope by 0.2 mm during the slack simulation (t = 1.251 s)

**esc_power**

- `route_infeasible`: esc_power: tangent cannot be met at (163, 40, 39) mm: required 18.0 mm, best 0.9 mm
- `wire_bend_radius_violation`: esc_power bends at 1.4 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle
- `wire_bend_radius_violation`: esc_power bends at 1.0 mm radius; minimum 16.0 mm for a 1-conductor 4.0 mm bundle

**servo_lead**

- `cable_support_unplaceable`: servo_lead: requested tie has no installed support holding its wires
- `cable_unsupported_span`: servo_lead: 80 mm between servo_lead.S2 and servo_lead.S7 with no support (limit 78 mm)

**esc_signal**

- `route_infeasible`: esc_signal: tangent cannot be met at (158, 57, 50) mm: required 7.3 mm, best 2.8 mm
- `route_infeasible`: esc_signal: hardware clearance cannot be met at (158, 57, 50) mm: required 1.2 mm, best 0.3 mm
- `cable_unsupported_span`: esc_signal: 276 mm between connector and connector with no support (limit 74 mm)
- `wire_bend_radius_violation`: esc_signal bends at 3.2 mm radius; minimum 6.4 mm for a 1-conductor 1.6 mm bundle

**imu_lead**

- `cable_support_unplaceable`: imu_lead: requested tie has no installed support holding its wires
- `cable_support_unplaceable`: imu_lead: requested tie has no installed support holding its wires
- `cable_support_unplaceable`: imu_lead: requested tie has no installed support holding its wires
- `cable_unsupported_span`: imu_lead: 183 mm between connector and connector with no support (limit 62 mm)

**rtk_radio_lead**

- `route_infeasible`: rtk_radio_lead: tangent cannot be met at (30, -10, 104) mm: required 4.7 mm, best 3.0 mm

### Real-firmware steering sweep

Command: `python -m r3_sim_worker examples/robots/rc-car --scenario steering_sweep`. Result: **FAIL**.

- `wire_clearance_violation`: steering_sweep: min_wire_clearance_m expected 0.008, measured -0.00021 (W_BAT_P to wheel_fl); data `{"limit": 0.008, "measured": -0.00021}`.

Sweep measurements: `{"brownouts": 0, "distance_m": 7.37, "min_rail_5v": 5.103, "min_wire_clearance_component": "wheel_fl", "min_wire_clearance_m": -0.00021, "min_wire_clearance_wire": "W_BAT_P", "packets_lost": 0, "rc_frames": 1801, "sim_time_s": 12.0, "top_speed_mps": 1.443, "wall_time_s": 81.91}`.

The remaining six scenarios were not rerun on this final A3 layout; earlier B2 passes are not claimed as current A3 evidence.

### Tests

**870 Python tests passed, 8 warnings, 2102.04 s (35:02)**. Web typecheck passed; **69 Vitest tests across 8 files passed**. The under-20-minute test target is unmet. See [test evidence](finish-a3-tests.md).

### Full error measurements

The full diagnostic payloads and numeric witnesses are in [the error-measurement appendix](finish-a3-error-measurements.md).

## A3 first-parent commits

```text
4ef625f Correct Pi laminate stack with shims and measure insertion at socket mouth
5c35f71 Keep unrelated XT60 pairs out of signal routing optimization
2f25fc9 Consider nearby round posts for cable supports without relaxing access checks
f18e727 Stage mechanical cable clearance layout for electrical integration
ad720a8 Preserve vendor Pi datum and verify socket contact engagement
89792d4 Move sensor tie post clear of extended HAT and deck standoff
2fc48b2 Require real support hardware for authored cable ties
a2bbaec Reject requested cable ties that do not hold routed wires
7964096 Raise and restrain the owned IMU lead above the Pi
70274ae Align the receiver pigtail with the south-facing HAT header
55c110e Build the IMU pedestal as connected native features
b526397 Preserve exact wire termination coordinates in JSON
6cc3c4e Derive fixtures and cap seats from one measured mount pattern
2f9a4bf Reserve later harness terminal exits before routing larger bundles
abf2200 Mount the RTK radio above the aligned GNSS lead exits
5bc294d Allow physical clocking and straight exits of floating connector pairs
22b6adf Seat flexible cable ends while retaining an estimated route release gate
04d4c73 Mount the owned front camera with a single provisional hole pattern
9b0abf2 Verify the camera ribbon estimate blocks model release
525e218 Allocate pin fan-out length only to ends that have pins
c7c9c10 Size the HAT fuse for the camera load within routed wire ampacity
32ac4a3 Keep unrelated harness branches out of splice entry apertures
691f894 Check provisional camera provenance and the revised fuse surge budget
adb6791 Restrain the servo pigtail and HAT climb on collared supports
5489961 Raise the ELRS radiator above the RTK radio body
1b1f5b1 Hold the encoder return span above the gearbox on a collared rail
3e9b83f Support both GNSS leads on the printed lid riser
a55d0ab Allow physical route waypoints for individual loom branches
3cc4df6 Keep post-fuse rail discharge differentiable through zero current
5f70967 Bound cable solver positions to the routing volume
bf9a784 Stand the IMU lead off its outboard posts with printed collars
7353953 Keep near-180-degree cable foldbacks local and invalid
5ceafef Keep waypoint cable paths from retracing their occupied voxels
7199e47 Correct XT90H contact pitch and male length from AMASS drawing
c5fb2b6 Use the physical loose-bundle bend rule in initial routing
1d0a504 Stop cable optimization after an audited feasible solution
68f2e04 Separate and clock motor pigtails with electrically verified 18 AWG leads
c02c215 Document finite-width branch and camera ribbon release blockers
b9dd9e5 Evaluate the branch proof strictly outside the shared-core tube
9e363e0 Stop infeasible cable solves at a proven fixed-clearance lower bound
d4fc556 Give the servo turnaround room and restrain both climb spans
d4a3346 Reserve required cable ties and plan short connector fan-outs
a06efbb Keep connector dimensions and support placement allowances in catalog data
f65f9d7 Finish pin convergence before turning a loose wire bundle
1bed937 Align estimated GY-521 mounting pattern with the downloaded STEP
68546ab Restore derived GY-521 cap seats unintentionally dropped by part accuracy
2e26dbb Derive asymmetric fixture hole patterns in opposing interface frames
0f31a89 Use supplied STEP faces when a purchased part has no authored solids
49827fd Close measured Pi stack and fit the IMU pedestal and protective caps
6c0d823 Preserve primitive fallback models and reconcile measured connector test expectations
b54ad9e Reserve later support destinations with their required inter-bundle spacing
4188869 Keep owned mounts with estimated holes visible to the release gate
3e77ff9 Keep future support reservations consistent at short connector fan-outs
a90cf10 Record measured IMU and Pi fit proofs and the XT60 source-model blocker
14cf6cf Clear the measured GNSS and encoder pigtails with joined supports
d833b32 Verify straight pin fan-out before the first physical bundle bend
7ea925d Route fresh projects through authored openings in fabricated supports
897cf66 Record ordinary IMU cap checks and reject an overlapping supplier XT60 model
0226dba Record the rejected vertical power-connector layout with measured failures
91c452b Record the passing full A3 test suite and outstanding runtime target
0a24816 Clarify the unchanged mechanical tests and prior camera warning expectation
```

Required merge commits: `3f6b20f` brings part-accuracy b73acfa; `e8d9781` brings integration through 145a823 (including 8aa8b3e). The later integration b630f8b change is documentation only.
