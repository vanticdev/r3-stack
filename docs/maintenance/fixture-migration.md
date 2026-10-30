# Synthetic fixture migration

Infrastructure tests use independent instrument, mechanism, connector, wiring and PCB coupons. Each project is copied without build outputs and generates its evidence in temporary directories.

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


## Fixture coverage

- `bench`: stationary instrument with a sensor arm, computer, explicit controller, actuators, radio, navigation sensors and ten harnesses. Exercises graph validation, electrical models, firmware/HAL, sensing, radio, sourcing, orchestration and release evidence.
- `mechanism`: small rotary coupon with a support, sleeve, rotor and two screws. Generates exact solids locally for mating, retention, interference, motion, mesh hand-offs and fabrication checks.
- `connector_stack`: header/socket coupon with its own pin-field and socket geometry. Exercises seating, insertion, pin alignment and connector interference without recorded CAD.
- `wiring`: branched power loom with pigtail, floating pair, splices, two sockets and a separate sensor pivot. Exercises routing, manufacturing, supports and native elastic cables. Smaller generated arm benches cover cable equations and deterministic stepping.
- `pcb`: passive filter with two connectors, a resistor, capacitor and mounting holes. KiCad generates every source and output in a temporary directory; ERC, DRC, parity, netlist and placement checks remain enforced.
- `drive_bench`: triangular three-roller base, independently arranged purchased power components and a minimal HAL controller. Exercises the reusable drive adapter, transmission inertia, tire contact and scenario runner; it is not a vehicle replica.
- `transmission`: standalone purchased gearbox/motor mating coupon.
- Existing stationary `printbot` and `assembly_bench` fixtures remain independent examples of camera and assembly infrastructure.

## Replaced design-only assertions

- Reference component counts, joint names and coordinate tables are replaced by counts/sets derived from each fixture's declaration and independent geometry calculations.
- Frozen CAD exports are replaced by synthetic exact solids, including the socket insertion and bore/thread engagement cases. Tests read no project build directory.
- Board-specific regulator choices, connector positions, net membership and component references are replaced by declared passive-board intent, real KiCad read-back and separate generic converter/arbiter physics checks.
- The reference mounting-offset, printed-cap sizing and firmware encoder-calibration tests were removed individually (listed above); they pin retired design results.
- Purchased-drive wheel-center/track assertions are replaced by standalone motor/gearbox compiled connector poses. Unsafe-drive rejection still uses real firmware and the same 2 m/s requirement, zero brownouts and supply threshold; its replay tail is explicitly bounded after irreversible release.
- Receiver cable core/pin-end checks use independent three-pin harnesses; endpoint counts are computed from the declared crimp terminations.
- The original application firmware's navigation, traction and supervisor tests are removed with that application. Generic device protocols, estimation math, HAL transport, launch/deployment adapters, electrical failsafe models and simulation infrastructure remain covered.

## Generality corrections

- Empty project roots are supported and new projects default to the blank scaffold in CLI, MCP, HTTP and web flows.
- Retention and CAD mate planning derive the root from the declared assembly or an unambiguous world-mounted component.
- CAD variables come from fabricated operation depths. Family variants with incompatible shared operation depths retain literal dimensions. Camera targets and full-lock framing derive from geometry; no installed layout coordinates remain.
- Ackermann geometry requires an explicitly declared positive wheelbase; track can derive from joint origins.
- Passive PCB intent exports only declared regulators and arbiters. Generic converter sizing, SPICE export, copper, parity and manufacturing infrastructure remain available.
- Electrical operating points derive mass, dimensions, wheel loading, motor speed/torque and servo loads from the project. Zero requested load can select zero throttle without an invalid root bracket.
- Supply telemetry accepts the declared computer model's voltage output without requiring a particular net label.
- Connector close-ups choose the actual mating component rather than a particular board or connector identifier.
- Fast KiCad coupons use a shorter real GUI audit polling interval, preserving the sample-count and no-window checks.

## Intentionally retained generic terms and purchased data

`chassis` remains in simulation frame/API names and generic wiring terminology: it means the robot's rigid base, with no design dimensions or component identity. Steering vocabulary (knuckle, horn, kingpin, wheel and Ackermann) remains in reusable joint/steering equations and vendor interfaces. Bought catalog IDs, including wheel, motor, gearbox, ball-stud and HAT stacking hardware, describe purchased products. Supplier CAD models and their provenance remain; no removed custom part has a model-bank entry.

The Raspberry Pi, GY-521 and UC-717 entries are unchanged. Purchased consumable cross-section/material/mass data remain; installed strap/tie loops, magnet arrangement notes and project mount descriptions were removed. Supplier-specific screw interface splits are labeled proxy estimates rather than copied installed engagement lengths.

## Ported reference-dependent Python files

`tests/api_helpers.py`, `tests/conftest.py`, `tests/firmware_fake_hal.py`, `tests/orchestrator_helpers.py`, `tests/sim_test_controller.py`, `tests/sourcing_helpers.py`, `tests/test_api_agents.py`, `tests/test_api_release.py`, `tests/test_api_robustness.py`, `tests/test_api_runs.py`, `tests/test_api_server.py`, `tests/test_assembly_compiler.py`, `tests/test_cad_consumers.py`, `tests/test_cad_kinematics.py`, `tests/test_cad_library_aliases.py`, `tests/test_cad_phaseb.py`, `tests/test_cad_plan.py`, `tests/test_cad_spec.py`, `tests/test_connector_mesh_mates.py`, `tests/test_connectors_connection_sites.py`, `tests/test_connectors_geometry.py`, `tests/test_connectors_onshape.py`, `tests/test_connectors_stage.py`, `tests/test_electrical_finish_b2.py`, `tests/test_electrical_stage.py`, `tests/test_electrical_system.py`, `tests/test_firmware_container.py`, `tests/test_firmware_deploy.py`, `tests/test_mechanical_pi_stack.py`, `tests/test_model_core.py`, `tests/test_modelbank_pi5.py`, `tests/test_orchestrator_cli.py`, `tests/test_orchestrator_mcp.py`, `tests/test_orchestrator_model_stage.py`, `tests/test_orchestrator_scaffold.py`, `tests/test_pcb_board.py`, `tests/test_pcb_intent.py`, `tests/test_pcb_rollout.py`, `tests/test_pcb_stage.py`, `tests/test_sim_cables.py`, `tests/test_sim_hal.py`, `tests/test_sim_radio.py`, `tests/test_sim_radio_sionna.py`, `tests/test_sim_rtk.py`, `tests/test_sim_scenario.py`, `tests/test_sim_vehicle.py`, `tests/test_sourcing_pipeline.py`, `tests/test_sourcing_requirements.py`, `tests/test_wiring_layout_fixes.py`, `tests/test_wiring_overhaul.py`, `tests/test_wiring_sim.py`, `tests/test_wiring_stage.py`.
