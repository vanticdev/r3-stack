# Synthetic fixture migration

Work in progress. Infrastructure tests are being moved to independent instrument, mechanism, connector, wiring and PCB coupons.

## Removed application-only tests

- `test_firmware_control.py::test_pure_pursuit_converges_on_straight_line` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_pure_pursuit_tracks_rectangle_course` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_path_projection_and_corners` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_pulse_mapping` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_speed_controller_feed_forward_and_no_reverse` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_transient_sag_does_not_cut_off` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_low_voltage_limit_scales_throttle` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_sustained_under_voltage_cuts_off_and_latches` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_resting_voltage_and_emergency_floor` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_supervisor_requires_arm_switch_cycle_and_brakes_on_link_loss` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_control.py::test_link_monitor_timeout_and_lq_zero` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_radio_failsafe_brakes_then_neutral_and_disarms` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_autonomous_rectangle_two_laps_through_estimator` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_low_voltage_limit_then_cutoff` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_manual_mode_maps_sticks_and_failsafes` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_config_from_blitz_base64_and_hal_bindings` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_robot_yaml_document_matches_firmware_defaults` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_lockstep_runs_are_deterministic` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_closed_loop.py::test_autonomy_waits_for_an_rtk_fix` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_motor_model_round_trips_current_and_duty` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_slip_controller_cuts_force_on_wheel_spin_and_restores_it_on_grip` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_slip_controller_ignores_encoder_lag_at_launch` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_stall_current_limit_bounds_the_motor_current` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_ground_speed_follows_accel_and_gps_and_ignores_a_spinning_wheel` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_launch_limiter_slews_only_at_low_speed` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_yaw_stabilizer_counter_steers_with_speed_scheduled_gain` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_yaw_heading_hold_integrates_lost_heading_and_cuts_force` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_full_throttle_launch_holds_slip_and_drives_straight` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_yaw_assist_recovers_heading_after_a_disturbance` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_motor_current_limit_at_stall` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_channel_map_comes_from_crsf_channels` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_remapped_channels_drive_manual_mode` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_flat_hal_document_lifts_mission_and_origin` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_brake_slip_controller_releases_a_locking_wheel_and_never_reaches_neutral` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_brake_command_stays_on_the_brake_side_of_the_esc_deadband` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_firmware_traction.py::test_failsafe_prediction_does_not_fuse_delayed_gnss_speed` — Application-only control law or closed-loop behavior; implementation retired with the project firmware.
- `test_cad_phaseb.py::test_hat_follows_board_json_hole_offset` — Only pinned removed board geometry, printed cap sizes, or project firmware calibration; generic interfaces remain covered.
- `test_cad_phaseb.py::test_caps_follow_the_print_clearance` — Only pinned removed board geometry, printed cap sizes, or project firmware calibration; generic interfaces remain covered.
- `test_cad_phaseb.py::test_sim_encoder_reads_the_differential_carrier` — Only pinned removed board geometry, printed cap sizes, or project firmware calibration; generic interfaces remain covered.
- `test_electrical_finish_b2.py::test_hat_edge_headers_and_owned_imu_circuit` — Only pinned removed board connector placement, component references and isolation layout. Generic board generation, connectivity and isolation remain tested.

## Purchased catalog entries moved

The following entries retain their data and provenance in `purchased.yaml`:

`ball-stud-m3-5.8x6`, `ball-stud-m3-5.8x8`, `bearing-mr106zz`, `elrs-receiver-2g4`, `elrs-t-antenna`, `esc-brushed-60a`, `foam-tape-3m-4026`, `fuse-atc-10a`, `fuse-atc-5a`, `fuse-atc-7.5a`, `gearbox-spur-diff`, `gnss-rtk-zed-f9p`, `gps-ublox-m10`, `hat-standoff-mf-m2.5-11`, `kingpin-screw-m3x20`, `lipo-2s-5000-25c`, `magnet-n52-2x1`, `motor-540-brushed`, `raspberry-pi-5-8gb`, `servo-digital-20kg`, `servo-output-25t`, `shim-din988-3x6x0.3`, `spacer-m2.5-ff-10`, `standoff-m3-47`, `strap-hook-loop-20`, `telemetry-sik-915-100mw`, `wheel-110x45`, `wheel-110x45-d`, `wheel-nut-m6-nyloc`, `zip-tie-2.5`.
