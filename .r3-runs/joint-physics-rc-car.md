# Joint physics: rc-car handoff

The generic joint layer is implemented and release-blocking. **The rc-car is not mechanically qualified.** No robot design files were changed. This report uses the existing recordings as requested; no new scenario sweep or benchmark was started during wrap-up.

Layout: merged integration `143518c` (finish-a3 and part-accuracy), 195 CAD parts. `robot.yaml` SHA-256: `08714ca05d85c66920f18449dd8c0b57f5dec1425da9e32b0bda15fd0ea38c84`. CAD recomposition had zero CAD errors. Existing wiring evidence has 43 errors and 5/12 valid harnesses; none was waived.

## What the preflight actually says

There are 236 connection records: 160 screw, 49 adhesive, 5 drive_flat, 5 press_fit, 7 revolute_fit, 1 spline, 4 ball_joint, 1 connector_retention, 4 strap. These are edges, not distinct fasteners or independent defects.

| Original zero-report group | Records | Interpretation after sampling |
| --- | ---: | --- |
| Screw clamp edges in 23 overloaded groups | 48 | Estimated assembly torque demands exceed the modeled proof/thread/pull-through limit. Construction/process conflict under these assumptions; not a missing nut. `joint_assembly_overload` plus fail-closed `joint_capacity_zero`. |
| Screw bearing/pull-through material | 32 | R3 material/allowable data gap for bought component mounting surfaces. Now `joint_capacity_unknown`, with null capacities. |
| `motor_pinion`, `output_l`, `output_r` (`drive_flat`) | 3 | Purchased-mechanism retention path/rating is unqualified; associated clamp-path data is also incomplete. Missing data, not evidence of absent physical retainers. |
| `servo_output` (`revolute_fit`) | 1 | Servo-internal bearing/retention material/rating data is missing. Missing data, not a physically zero-strength servo. |

**Current static totals: 48 `joint_capacity_zero`, 36 `joint_capacity_unknown`, 23 `joint_assembly_overload`.** The 23 overloads overlap the 48 zero records. No original zero report on this merged layout is an explicit “no nut or tapped hole” case. Missing-nut failures are verified with generic synthetic fixtures, including a non-vehicle arm.

The 32 unknown screw edges represent 16 fastener groups: encoder (4 edges / 2 groups), servo ears (8 / 4), IMU (4 / 2), HAT (8 / 4), and camera PCB (8 / 4). Known thread/proof bounds remain recorded; the unknown bearing surface must not inherit anonymous steel or printed strength.

Examples: encoder mounts request 300 N with a known 2,011 N screw proof bound, but the encoder plate pull-through allowable is absent. Servo ears request 833.333 N and have a known female-thread bound around 4,136 N, but their bearing material is unqualified. Camera/IMU material gaps may hide additional torque conflicts; filling them is required before drawing that conclusion.

## The 23 assembly overloads

Every demand below uses a catalog-estimated assembly torque and nut factor. Printed shear/punching allowables are estimated from weakest-direction tensile TDS values; local bearing thickness/engagement uses CAD where available. These are actionable model conflicts, not measured installation torques or certified failure loads.

| Fastener group | Requested preload N | Governing capacity N | Governing limit |
| --- | ---: | ---: | --- |
| `servo_approach_post` | 833.333 | 391.005 | thread_strip |
| `arm_fl_mount_1` | 833.333 | 764.136 | thread_strip |
| `arm_fl_mount_2` | 833.333 | 764.136 | thread_strip |
| `arm_fr_mount_1` | 833.333 | 764.136 | thread_strip |
| `arm_fr_mount_2` | 833.333 | 764.136 | thread_strip |
| `so_rl_top` | 833.333 | 688.337 | pull_through |
| `so_rr_top` | 833.333 | 688.337 | pull_through |
| `so_fl_top` | 833.333 | 688.337 | pull_through |
| `so_fr_top` | 833.333 | 688.337 | pull_through |
| `imu_outboard_post_mount` | 833.333 | 391.005 | thread_strip |
| `imu_south_post_mount` | 833.333 | 391.005 | thread_strip |
| `imu_corner_post_mount` | 833.333 | 391.005 | thread_strip |
| `lead_post_mount` | 833.333 | 391.005 | thread_strip |
| `motor_lead_post_mount` | 833.333 | 391.005 | thread_strip |
| `power_lead_post_mount` | 833.333 | 391.005 | thread_strip |
| `encoder_lead_post_mount` | 833.333 | 391.005 | thread_strip |
| `sensor_lead_post_mount` | 833.333 | 391.005 | thread_strip |
| `mast_mount_1` | 833.333 | 688.336 | pull_through |
| `mast_mount_2` | 833.333 | 688.336 | pull_through |
| `camera_bracket_mount_1` | 833.333 | 391.005 | thread_strip |
| `camera_bracket_mount_2` | 833.333 | 391.005 | thread_strip |
| `servo_climb_post` | 833.333 | 391.005 | thread_strip |
| `servo_upper_post` | 833.333 | 391.005 | thread_strip |

For example, `servo_approach_post` uses M3 torque 0.5 N·m, K=0.2: 833.333 N requested versus about 391.005 N female-thread stripping. Its roughly 3 mm CAD bearing thickness and 13.279 MPa estimated PETG shear give about 688.337 N punching and 4,879.907 N screw proof. Changing design or process belongs to the mechanical lane; this task changed neither.

## Interpreting the first 136 releases

The first historical `gravity_upright` recording contains 136 `joint_failed` events: 84 at t=0 and 52 later. The initial 84 are exactly the groups above: 48 overloaded edges and 36 missing-data artifacts. In the final implementation, unknown data produces a separate `joint_capacity_unknown` event rather than a fabricated zero-strength release; unqualified constraints remain inactive and block release.

The 52 later events comprise 38 adhesive, 4 ball-joint, 3 revolute-fit, 4 screw, 1 connector-retention and 2 strap records. Screw and strap counts include propagated clamp/support releases. **These are conditional modeled threshold crossings after an already unqualified assembly was released, not 52 independently established physical design gaps.** Most non-screw ratings are generic estimates, and coupled motion depends on the missing connections.

| Sample type / joint | Time s | Demand | Modeled capacity | What it establishes |
| --- | ---: | ---: | ---: | --- |
| `enc_magnet_3_bond` (adhesive) | 0.00025 | 0.000112397 N·m | 6.282e-05 N·m | Conditional modeled threshold crossing; catalog/interface capacity |
| `cap_ear_b1` (adhesive) | 0.00025 | 2.50368 N | 0.25198 N | Conditional modeled threshold crossing; catalog/interface capacity |
| `gpio_header` (connector_retention) | 0.00025 | 13.1651 N | 10 N | Conditional modeled threshold crossing; catalog/interface capacity |
| `bj_hl` (ball_joint) | 0.0005 | 36.047 N | 20 N | Conditional modeled threshold crossing; catalog/interface capacity |
| `antenna_bond` (adhesive) | 0.002 | 0.00542627 N·m | 0.00376704 N·m | Conditional modeled threshold crossing; catalog/interface capacity |
| `kingpin_fr` (revolute_fit) | 0.0005 | 2.25113 N·m | 2.025 N·m | Conditional modeled threshold crossing; catalog/interface capacity |
| `wheel_nut_fl` (screw) | 0.02475 | 65.6689 N·m | 50.3083 N·m | Conditional modeled threshold crossing; preload / proof / female thread / pull-through limit |
| `battery_strap_loop` (strap) | 0.084 | 1.11688 N·m | 0.775 N·m | Conditional modeled threshold crossing; catalog/interface capacity |

## Available wall times and coverage

Seconds below include setup. Baseline is the merged `143518c` lumped-body implementation on the same layout. Runs overlapped other workers and used different concurrency; these are observed wall times, **not an isolated speed benchmark**. Only full-throttle and steering-sweep are duration-matched full driving comparisons. No speed optimization or car tuning was performed during wrap-up.

All recorded mechanical cases use historical fixture version 1. Final fixture version 2 removes ambient support, uses native-distance clearance and an infinite impact plane, and verifies a contact reaction. Version 2 is targeted-test validated but **not run on the rc-car**. Final missing-data classification is also **not rerun dynamically on the rc-car**. Historical events and utilization remain exploratory evidence.

| Scenario | Required sim s | Recorded sim s | Before wall s | After wall s | After setup s | Releases | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `autonomous_course` | 90 | 2 | 1151.91 | 1396.47 | 1119.16 | 136 | 2 s prefix; incomplete |
| `full_throttle` | 10 | 10 | 185.6 | 1285.59 | 103.99 | 144 | full duration |
| `range_failsafe` | 300 | 2 | 5278.39 | 3029.35 | 2739.42 | 136 | 2 s prefix; incomplete |
| `pi_hang` | 40 | 2 | 2242.65 | 1739.51 | 1504.41 | 136 | 2 s prefix; incomplete |
| `top_speed_failsafe` | 16 | 2 | 433.73 | 757.77 | 108.32 | 136 | 2 s prefix; incomplete |
| `low_battery` | 120 | 2 | 1660.95 | 1390.21 | 1079.61 | 136 | 2 s prefix; incomplete |
| `steering_sweep` | 12 | 12 | 252.79 | 1531.47 | 117.33 | 144 | full duration |
| `gravity_upright` | 2 | 2 | — | 615.93 | 118.52 | 136 | full duration |
| `gravity_inverted` | 2 | 2 | — | 713.19 | 106.69 | 129 | full duration |
| `gravity_left` | 2 | 2 | — | 598.64 | 207.65 | 135 | full duration |
| `gravity_right` | 2 | 2 | — | 546.01 | 219.81 | 135 | full duration |
| `gravity_front` | 2 | 2 | — | 361.97 | 129.17 | 138 | full duration |
| `gravity_rear` | 2 | 2 | — | 496.35 | 126.03 | 132 | full duration |
| `drop_upright` | 2.4516 | 0.47 | — | 268.64 | 146.06 | 169 | numerically incomplete |
| `drop_inverted` | 2.4516 | 0.485 | — | 298.06 | 188.26 | 173 | numerically incomplete |
| `drop_left` | 2.4516 | 0.445 | — | 283.6 | 184.19 | 177 | numerically incomplete |
| `drop_right` | 2.4516 | 0.488 | — | 264.34 | 169.55 | 167 | numerically incomplete |
| `drop_front` | 2.4516 | 0.519 | — | 261.29 | 152.01 | 165 | numerically incomplete |
| `drop_rear` | 2.4516 | 1.048 | — | 351.96 | 151.46 | 191 | numerically incomplete |
| `drop_corner_nnn` | 2.4516 | 0.611 | — | 294.33 | 160.89 | 184 | numerically incomplete |
| `drop_corner_nnp` | 2.4516 | 0.486 | — | 197 | 139.87 | 180 | numerically incomplete |
| `drop_corner_npn` | 2.4516 | 0.468 | — | 251.26 | 149.17 | 186 | numerically incomplete |
| `drop_corner_npp` | 2.4516 | 0.368 | — | 229.56 | 151.73 | 188 | numerically incomplete |
| `drop_corner_pnn` | 2.4516 | 0.52 | — | 246.24 | 153.49 | 175 | numerically incomplete |
| `drop_corner_pnp` | 2.4516 | 0.237 | — | 179.08 | 132.55 | 189 | numerically incomplete |
| `drop_corner_ppn` | 2.4516 | 0.456 | — | 213.28 | 133.18 | 173 | numerically incomplete |
| `drop_corner_ppp` | 2.4516 | 0.547 | — | 199.04 | 132.49 | 192 | numerically incomplete |
| `wall_impact` | 2 | 0.111 | — | 118.49 | 99.14 | 189 | numerically incomplete |
| `vibration_x` | 60 | 2 | — | 376.38 | 94.57 | 144 | 2 s prefix; incomplete |
| `vibration_y` | 60 | 2 | — | 355.46 | 86.59 | 145 | 2 s prefix; incomplete |
| `vibration_z` | 60 | 2 | — | 334.51 | 80.1 | 133 | 2 s prefix; incomplete |

A dash in a new mechanical-case baseline means no baseline counterpart was run. All 31 IDs have historical recordings; full-duration reruns of the five truncated driving cases and three vibration cases were **not run**. All 14 drops and wall impact ended with `joint_simulation_unstable` / `scenario_incomplete`; they do not qualify. The unstable native coordinates sampled were elastic cable joints. Checks remain enabled; no numerical error was hidden or tolerated. Baseline pi-hang, range-failsafe and steering-sweep already failed their authored checks.

Historical summaries wrote the truncated duration as 2 s; this table recovers the real required duration from the spec, and the final runner now preserves requested duration separately. Superseded interrupted full-duration attempts are retained in [interrupted-attempts.json](joint-physics-interrupted-attempts.json).

## Formulas, sources and generality

- Screws/nuts/inserts: T=KFd; friction slip μFpre n; minimum preload separation, proof, female thread stripping and head/nut punching. Bare screws have no axial clamp; retained pivots retain proof/thread capacity without preload friction. Sources: [NASA RP-1228](https://ntrs.nasa.gov/citations/19900009424), Shigley chapter 8, [VDI 2230 scope](https://www.vdi.de/fileadmin/pages/vdi_de/redakteure/richtlinien/inhaltsverzeichnisse/2360550.pdf). This is a conservative lumped capacity model, not full VDI qualification.
- Press/interference fits: Lamé thick-cylinder pressure, friction times pressure times cylindrical area; elastic-yield limit checked.
- Adhesive/tape: sourced shear/peel rating times area. Straps/ties: rated tension. Connector retention: minimum retention/unmating force. Purchased bearing/flat/spline and ball retention require qualified rating/material/retainer paths.
- Snap fits: Euler–Bernoulli cantilever deflection/strength minimum; [BASF guidance](https://ultrasimweb.basf.com/snapfit/blog/introduction/). Resting parts use contacts only.
- Unlocked screw loosening: [Junker SAE 690055](https://doi.org/10.4271/690055) transverse-slip mechanism. Exponential slip-distance rate is explicitly estimated; locked screws disable that decay, not overload.
- Vibration: tailored [MIL-STD-810H Method 514.8 Annex C](https://quicksearch.dla.mil/qaDocDetails.aspx?ident_number=35978) approach; profile itself is an estimated small-platform default, spec-overridable. Not a standard qualification spectrum.

The generic assembly builder/runtime are shared by passive structures and articulated robots. Vehicle tire/steering behavior is gated on declared wheel/steering components. Gravity, drops and vibration do not require wheels; stationary bases are fixture-held except for drops, and wall impact skips with an explicit diagnostic. Tests include a screw-retained two-link arm with and without nuts, and a tall non-vehicle impact fixture.

See [joint-physics architecture](../docs/architecture/joint-physics.md) for equations, assumptions, numerical carrier approximation and replay schema.

## Every estimated value

The complete machine-readable inventory is [joint-physics-estimates.json](joint-physics-estimates.json): every catalog entry marked estimated, every current and historical per-connection substitution, vibration values, and each recorded runtime assumption. Missing data is separately listed in [joint-physics-rc-car-results.json](joint-physics-rc-car-results.json), never silently relabeled an estimate.

Main defaults: K=0.2; friction μ=0.2; assembly torque M2=0.12, M2.5=0.25, M3=0.5, M4=1.2 N·m (other fastener values remain enumerated in the inventory); loosening e-fold distance=0.001 m; generic fit tolerance=0.0002 m; adhesive shear=100 kPa / peel=20 kPa; strap=50 N; connector=10 N; ball retention=20 N; moving-frame mass/inertia fraction maximum=0.0001. These are estimates, not certified product ratings.

Material shear uses weakest XY/Z tensile ultimate divided by √3 (estimated isotropic conversion); dry surface friction is estimated. Generic metal properties and every FDM Poisson ratio are catalog estimates as annotated. Envelope-derived contact areas/radii, engagement, bearing thickness, hub diameter, projected bearing strength and any authored-but-unqualified torque are estimates unless a CAD/datasheet measurement replaces them. The inventory preserves each numeric value and provenance.

Vibration estimates: PSD points (Hz, (m/s²)²/Hz)=(5,.05),(20,.2),(80,.2),(200,.02); 120 Hz motor harmonic at 1 m/s² peak; 1 s ramp; 60 s/axis. Geometry/mass/inertia fallbacks, regularization and numerical timestep assumptions are also retained verbatim in the inventory.

## Validation and commits

Final targeted Python: **47 passed** (41 joint fixtures/regressions, 6 release tests), 108.96 s. Command: `python -m pytest -q -p no:randomly tests/test_joint_physics.py tests/test_api_release.py`. The pending fixture increment separately passed 43 tests before commit.

Web: `npx tsc --noEmit` and `npm run typecheck` passed; **73 unit tests in 9 files**, **4 loader tests**, production build, and **25 headless Chromium smoke tests** passed (2.8 min).

Merged baseline full Python suite: **867 passed, 3 skipped**, 3365.76 s.

The earlier final full Python-suite attempt did not complete and showed failures/errors before its provider interruption. It is not claimed as passing. The latest integrator scope replaces further long validation with the targeted checks above. No full after-suite or scenario rerun was started during wrap-up.

Implementation commits already on the branch:

- `6d7d5f3` Checkpoint generic breakable assembly physics and replay
- `f3414da` Merge updated integration layout into joint physics
- `ae5ae9f` Add resumable scenario benchmarking for merged assemblies
- `271710e` Preserve scene texture colors in generic robot simulations
- `fe47088` Reject incomplete joint evidence after early software shutdown
- `4b9c623` Use robot terminology for shared assembly playback
- `04d3841` Retain coupled failure replay and reject truncated qualification
- `e35ca73` Use native headless rendering for macOS WebGL validation
- `f12804c` Reuse exact convex pieces across multipart model builders
- `4ee6303` Validate generic load fixtures and retain full qualification duration
- `0e4a5ba` Block unknown joint capacities without claiming zero strength

## Per-joint historical failure samples

Every distinct joint with a recorded failure is named below. The representative event has the largest recorded failure utilization for that joint (or the first zero-capacity event); this is not a post-release peak-load estimate. All events, times, modes, loads, capacities, diagnostic causes and per-scenario utilization are retained in [the results JSON](joint-physics-rc-car-results.json). N·m applies to torsion/bending; N to axial/shear. “Data gap” means the numeric legacy zero is invalid as a strength claim. All runtime samples are conditional on incomplete data and estimated ratings.

| Joint | Type | Scenario / time s | Mode | Load | Legacy threshold | Interpretation / cause |
| --- | --- | --- | --- | ---: | ---: | --- |
| `antenna_bond` | adhesive | drop_rear / 0.0005 | axial | 7.24448 | 0.31392 | Conditional modeled release: catalog/interface capacity |
| `arm_fl_mount_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `arm_fl_mount_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after arm_fl_mount_1:1 |
| `arm_fl_mount_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `arm_fl_mount_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after arm_fl_mount_2:1 |
| `arm_fr_mount_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `arm_fr_mount_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after arm_fr_mount_1:1 |
| `arm_fr_mount_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `arm_fr_mount_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after arm_fr_mount_2:1 |
| `battery_strap_loop` | strap | drop_corner_nnn / 0.00025 | bending | 1.76849 | 0.775 | Conditional modeled release: catalog/interface capacity |
| `battery_strap_loop:anchor:chassis` | strap | drop_corner_nnn / 0.00025 | bending | 1.77152 | 0.775 | Conditional modeled release: strap support tension |
| `bearing_axle_rr` | revolute_fit | drop_inverted / 0.47075 | axial | 2623.82 | 2500 | Conditional modeled release: catalog/interface capacity |
| `bearing_seat_rl` | press_fit | drop_corner_ppn / 0.4555 | bending | 9.53842 | 8.05404 | Conditional modeled release: catalog/interface capacity |
| `bearing_seat_rr` | press_fit | drop_left / 0.4445 | bending | 13.1961 | 8.05404 | Conditional modeled release: catalog/interface capacity |
| `bj_hl` | ball_joint | vibration_z / 0.0005 | shear | 36.047 | 20 | Conditional modeled release: catalog/interface capacity |
| `bj_hr` | ball_joint | vibration_z / 0.0005 | shear | 35.8608 | 20 | Conditional modeled release: catalog/interface capacity |
| `bj_kl` | ball_joint | vibration_z / 0.0005 | shear | 37.0767 | 20 | Conditional modeled release: catalog/interface capacity |
| `bj_kr` | ball_joint | vibration_z / 0.0005 | shear | 36.8834 | 20 | Conditional modeled release: catalog/interface capacity |
| `bs_kr` | screw | drop_rear / 0.09125 | torsion | 0.727644 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `camera_bracket_mount_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `camera_bracket_mount_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after camera_bracket_mount_1:1 |
| `camera_bracket_mount_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `camera_bracket_mount_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after camera_bracket_mount_2:1 |
| `camera_screws_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for front_camera |
| `camera_screws_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after camera_screws_1:1 |
| `camera_screws_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for front_camera |
| `camera_screws_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after camera_screws_2:1 |
| `camera_screws_3:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for front_camera |
| `camera_screws_3:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after camera_screws_3:1 |
| `camera_screws_4:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for front_camera |
| `camera_screws_4:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after camera_screws_4:1 |
| `camera_strain_tape_cable` | adhesive | drop_corner_ppp / 0.40225 | bending | 0.00331669 | 0.00184 | Conditional modeled release: catalog/interface capacity |
| `cap_camera_1` | adhesive | drop_corner_pnn / 0.00025 | axial | 1.09271 | 0.2612 | Conditional modeled release: catalog/interface capacity |
| `cap_camera_2` | adhesive | drop_corner_ppp / 0.00025 | axial | 1.27926 | 0.2612 | Conditional modeled release: catalog/interface capacity |
| `cap_camera_3` | adhesive | drop_front / 0.08075 | axial | 1.50481 | 0.2612 | Conditional modeled release: catalog/interface capacity |
| `cap_camera_4` | adhesive | drop_corner_ppp / 0.00375 | bending | 0.00170416 | 0.00043098 | Conditional modeled release: catalog/interface capacity |
| `cap_camera_mount_1` | adhesive | drop_inverted / 0.00625 | shear | 13.5202 | 1.7338 | Conditional modeled release: catalog/interface capacity |
| `cap_camera_mount_2` | adhesive | drop_upright / 0.0025 | bending | 0.00270276 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_ear_a1` | adhesive | wall_impact / 0.022 | bending | 0.00218653 | 0.000541757 | Conditional modeled release: catalog/interface capacity |
| `cap_ear_a2` | adhesive | gravity_right / 0.0315 | bending | 0.00239938 | 0.000541757 | Conditional modeled release: catalog/interface capacity |
| `cap_ear_b1` | adhesive | drop_corner_ppp / 0.00025 | axial | 3.15114 | 0.25198 | Conditional modeled release: catalog/interface capacity |
| `cap_ear_b2` | adhesive | drop_corner_pnp / 0.00025 | axial | 1.27387 | 0.25198 | Conditional modeled release: catalog/interface capacity |
| `cap_encoder_lead_post` | adhesive | drop_corner_nnp / 0.00025 | bending | 0.00128559 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_hat_1` | adhesive | drop_corner_npn / 0.00025 | axial | 1.78367 | 0.2965 | Conditional modeled release: catalog/interface capacity |
| `cap_hat_2` | adhesive | drop_corner_npp / 0.00025 | axial | 1.34619 | 0.2965 | Conditional modeled release: catalog/interface capacity |
| `cap_hat_3` | adhesive | drop_corner_npn / 0.00025 | axial | 0.763102 | 0.2965 | Conditional modeled release: catalog/interface capacity |
| `cap_hat_4` | adhesive | drop_corner_nnp / 0.00025 | axial | 0.811974 | 0.2965 | Conditional modeled release: catalog/interface capacity |
| `cap_imu_1` | adhesive | gravity_rear / 0.00025 | axial | 0.0951403 | 0.05738 | Conditional modeled release: catalog/interface capacity |
| `cap_imu_2` | adhesive | gravity_rear / 0.00025 | axial | 0.0693366 | 0.04196 | Conditional modeled release: catalog/interface capacity |
| `cap_imu_corner_post` | adhesive | drop_corner_npp / 0.0265 | bending | 0.00239316 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_imu_outboard_post` | adhesive | drop_upright / 0.03475 | shear | 6.05313 | 1.7338 | Conditional modeled release: catalog/interface capacity |
| `cap_imu_south_post` | adhesive | drop_inverted / 0.00375 | bending | 0.00289594 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_lead_post` | adhesive | drop_corner_pnn / 0.08525 | bending | 0.00329424 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_motor_lead_post` | adhesive | drop_corner_npp / 0.00025 | bending | 0.00102045 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_power_lead_post` | adhesive | drop_front / 0.0015 | bending | 0.00596763 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_sensor_lead_post` | adhesive | drop_rear / 1.046 | axial | 15.3978 | 0.34676 | Conditional modeled release: catalog/interface capacity |
| `cap_servo_approach_post` | adhesive | drop_front / 0.357 | bending | 0.00193069 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_servo_climb_post` | adhesive | gravity_front / 0.09875 | bending | 0.00549014 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_servo_upper_post` | adhesive | gravity_rear / 0.557 | shear | 32.6268 | 1.7338 | Conditional modeled release: catalog/interface capacity |
| `cap_so_fl` | adhesive | vibration_z / 0.0775 | axial | 1.02963 | 0.34676 | Conditional modeled release: catalog/interface capacity |
| `cap_so_fr` | adhesive | drop_rear / 0.00025 | bending | 0.00216485 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_so_rl` | adhesive | drop_right / 0.0005 | bending | 0.0044186 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `cap_so_rr` | adhesive | drop_rear / 0.012 | bending | 0.00506356 | 0.000745534 | Conditional modeled release: catalog/interface capacity |
| `enc_magnet_1_bond` | adhesive | drop_corner_ppp / 0.54575 | axial | 0.218866 | 0.06282 | Conditional modeled release: catalog/interface capacity |
| `enc_magnet_2_bond` | adhesive | drop_upright / 0.44525 | axial | 0.351062 | 0.06282 | Conditional modeled release: catalog/interface capacity |
| `enc_magnet_3_bond` | adhesive | drop_corner_ppn / 0.00025 | bending | 0.000129955 | 6.282e-05 | Conditional modeled release: catalog/interface capacity |
| `enc_magnet_4_bond` | adhesive | drop_corner_pnp / 0.0105 | axial | 0.34475 | 0.06282 | Conditional modeled release: catalog/interface capacity |
| `enc_ring_press` | press_fit | drop_corner_npp / 0.007 | torsion | 12.4663 | 7.62901 | Conditional modeled release: catalog/interface capacity |
| `encoder_lead_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `encoder_lead_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after encoder_lead_post_mount:1 |
| `encoder_mount_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for encoder |
| `encoder_mount_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after encoder_mount_1:1 |
| `encoder_mount_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for encoder |
| `encoder_mount_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after encoder_mount_2:1 |
| `esc_tape_bottom` | adhesive | wall_impact / 0.07825 | bending | 0.989749 | 0.2816 | Conditional modeled release: catalog/interface capacity |
| `esc_tape_top` | adhesive | wall_impact / 0.07825 | bending | 0.919416 | 0.2816 | Conditional modeled release: catalog/interface capacity |
| `fuse_tie_loop` | strap | drop_left / 0.4445 | bending | 0.720169 | 0.35 | Conditional modeled release: catalog/interface capacity |
| `fuse_tie_loop:anchor:chassis` | strap | drop_left / 0.4445 | bending | 0.718827 | 0.35 | Conditional modeled release: strap support tension |
| `gb_mount_1:1` | screw | drop_corner_ppp / 0.47575 | bending | 10.3823 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `gb_mount_1:2` | screw | drop_upright / 0.42925 | axial | 22.7903 | 0 | Conditional modeled release: clamp preload lost after gb_mount_1:1 |
| `gb_mount_2:1` | screw | drop_upright / 0.4295 | torsion | 2.32828 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `gb_mount_2:2` | screw | drop_upright / 0.4295 | axial | 22.6808 | 0 | Conditional modeled release: clamp preload lost after gb_mount_2:1 |
| `gb_mount_3:1` | screw | drop_inverted / 0.4625 | bending | 12.0384 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `gb_mount_3:2` | screw | drop_upright / 0.4295 | axial | 33.3423 | 0 | Conditional modeled release: clamp preload lost after gb_mount_3:1 |
| `gb_mount_4:1` | screw | vibration_y / 0.0085 | bending | 7.81937 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `gb_mount_4:2` | screw | drop_upright / 0.4295 | axial | 22.7487 | 0 | Conditional modeled release: clamp preload lost after gb_mount_4:1 |
| `gnss_riser_bond` | adhesive | vibration_y / 0.0085 | axial | 61.3422 | 29.92 | Conditional modeled release: catalog/interface capacity |
| `gpio_header` | connector_retention | drop_corner_nnn / 0.00025 | bending | 0.924676 | 0.288152 | Conditional modeled release: catalog/interface capacity |
| `gps_tape_bottom` | adhesive | drop_corner_ppp / 0.47575 | axial | 138.02 | 28.39 | Conditional modeled release: catalog/interface capacity |
| `gps_tape_top` | adhesive | drop_corner_ppp / 0.47575 | axial | 138.309 | 29.92 | Conditional modeled release: catalog/interface capacity |
| `hat_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for hat |
| `hat_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after hat_1:1 |
| `hat_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for hat |
| `hat_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after hat_2:1 |
| `hat_3:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for hat |
| `hat_3:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after hat_3:1 |
| `hat_4:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for hat |
| `hat_4:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after hat_4:1 |
| `hub_fl` | revolute_fit | gravity_front / 0.029 | bending | 12.669 | 12.15 | Conditional modeled release: catalog/interface capacity |
| `hub_fr` | revolute_fit | drop_corner_pnp / 0.01525 | bending | 14.1098 | 12.15 | Conditional modeled release: catalog/interface capacity |
| `hub_rl_mount_1:1` | screw | drop_left / 0.4445 | torsion | 0.884357 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `hub_rl_mount_1:2` | screw | drop_inverted / 0.47225 | axial | 8.11461 | 0 | Conditional modeled release: clamp preload lost after hub_rl_mount_1:1 |
| `hub_rl_mount_2:1` | screw | drop_left / 0.4445 | torsion | 0.774513 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `hub_rl_mount_2:2` | screw | drop_inverted / 0.47225 | axial | 12.5249 | 0 | Conditional modeled release: clamp preload lost after hub_rl_mount_2:1 |
| `hub_rl_mount_3:1` | screw | drop_left / 0.4445 | torsion | 1.03244 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `hub_rl_mount_3:2` | screw | drop_inverted / 0.47225 | axial | 13.419 | 0 | Conditional modeled release: clamp preload lost after hub_rl_mount_3:1 |
| `hub_rl_mount_4:1` | screw | drop_corner_ppn / 0.4555 | bending | 14.4155 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `hub_rl_mount_4:2` | screw | drop_inverted / 0.472 | axial | 21.723 | 0 | Conditional modeled release: clamp preload lost after hub_rl_mount_4:1 |
| `hub_rr_mount_1:1` | screw | drop_corner_ppn / 0.4555 | torsion | 2.52023 | 0.293059 | Conditional modeled release: transverse-slip self-loosening |
| `hub_rr_mount_1:2` | screw | drop_upright / 0.43525 | axial | 19.4747 | 0 | Conditional modeled release: clamp preload lost after hub_rr_mount_1:1 |
| `hub_rr_mount_2:1` | screw | drop_corner_nnn / 0.0005 | torsion | 1.00583 | 0.44389 | Conditional modeled release: transverse-slip self-loosening |
| `hub_rr_mount_2:2` | screw | drop_upright / 0.4355 | axial | 19.6212 | 0 | Conditional modeled release: clamp preload lost after hub_rr_mount_2:1 |
| `hub_rr_mount_3:1` | screw | drop_corner_ppn / 0.4555 | torsion | 1.49211 | 0.277924 | Conditional modeled release: transverse-slip self-loosening |
| `hub_rr_mount_3:2` | screw | drop_upright / 0.4355 | axial | 12.6221 | 0 | Conditional modeled release: clamp preload lost after hub_rr_mount_3:1 |
| `hub_rr_mount_4:1` | screw | drop_corner_ppp / 0.0005 | bending | 5.81698 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `hub_rr_mount_4:2` | screw | drop_upright / 0.4355 | axial | 12.7651 | 0 | Conditional modeled release: clamp preload lost after hub_rr_mount_4:1 |
| `imu_corner_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `imu_corner_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after imu_corner_post_mount:1 |
| `imu_mount_bond` | adhesive | drop_rear / 0.0005 | bending | 0.370213 | 0.125 | Conditional modeled release: catalog/interface capacity |
| `imu_outboard_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `imu_outboard_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after imu_outboard_post_mount:1 |
| `imu_screws_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for imu |
| `imu_screws_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after imu_screws_1:1 |
| `imu_screws_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for imu |
| `imu_screws_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after imu_screws_2:1 |
| `imu_south_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `imu_south_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after imu_south_post_mount:1 |
| `kingpin_fl` | revolute_fit | gravity_front / 0.14075 | bending | 17.124 | 2.025 | Conditional modeled release: catalog/interface capacity |
| `kingpin_fr` | revolute_fit | gravity_rear / 0.3025 | bending | 8.4728 | 2.025 | Conditional modeled release: catalog/interface capacity |
| `kingpin_thread_fl` | screw | gravity_front / 0.14075 | bending | 17.1047 | 2.35628 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `kingpin_thread_fr` | screw | gravity_rear / 0.3025 | bending | 8.46668 | 2.35628 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `lead_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `lead_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after lead_post_mount:1 |
| `mast_mount_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `mast_mount_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after mast_mount_1:1 |
| `mast_mount_1:3` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after mast_mount_1:1 |
| `mast_mount_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `mast_mount_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after mast_mount_2:1 |
| `mast_mount_2:3` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after mast_mount_2:1 |
| `motor_lead_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `motor_lead_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after motor_lead_post_mount:1 |
| `motor_mount_1:1` | screw | drop_corner_ppp / 0.47775 | bending | 3.78209 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `motor_mount_1:2` | screw | drop_upright / 0.46975 | axial | 9.36299 | 0 | Conditional modeled release: clamp preload lost after motor_mount_1:1 |
| `motor_mount_2:1` | screw | drop_corner_pnp / 0.23225 | bending | 4.21877 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `motor_mount_2:2` | screw | drop_upright / 0.46975 | axial | 18.7029 | 0 | Conditional modeled release: clamp preload lost after motor_mount_2:1 |
| `motor_pinion` | drive_flat | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no qualified retention/material data |
| `output_l` | drive_flat | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no qualified retention/material data |
| `output_r` | drive_flat | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no qualified retention/material data |
| `pi_s1:1` | screw | drop_rear / 1.0485 | axial | 2.21848 | 0 | Conditional modeled release: clamp preload lost after pi_s1:2 |
| `pi_s1:2` | screw | drop_corner_npn / 0.45975 | axial | 22.8047 | 0 | Conditional modeled release: clamp preload lost after pi_s1:3 |
| `pi_s1:3` | screw | drop_rear / 1.0485 | axial | 200.146 | 0 | Conditional modeled release: clamp preload lost after pi_s1:2 |
| `pi_s2:1` | screw | drop_rear / 1.04625 | axial | 0.215722 | 0 | Conditional modeled release: clamp preload lost after pi_s2:2 |
| `pi_s2:2` | screw | drop_corner_nnn / 0.595 | bending | 4.10945 | 1.22594 | Conditional modeled release: transverse-slip self-loosening |
| `pi_s2:3` | screw | drop_rear / 1.04625 | axial | 188.868 | 0 | Conditional modeled release: clamp preload lost after pi_s2:2 |
| `pi_s3:1` | screw | drop_rear / 1.0485 | axial | 1.93582 | 0 | Conditional modeled release: clamp preload lost after pi_s3:2 |
| `pi_s3:2` | screw | drop_corner_ppp / 0.54475 | bending | 13.7515 | 1.25 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `pi_s3:3` | screw | drop_rear / 1.0485 | axial | 45.2719 | 0 | Conditional modeled release: clamp preload lost after pi_s3:2 |
| `pi_s4:1` | screw | drop_upright / 0.47025 | axial | 0.0318801 | 0 | Conditional modeled release: clamp preload lost after pi_s4:2 |
| `pi_s4:2` | screw | drop_right / 0.4835 | axial | 12.7065 | 0 | Conditional modeled release: clamp preload lost after pi_s4:3 |
| `pi_s4:3` | screw | drop_upright / 0.47025 | axial | 13.5811 | 0 | Conditional modeled release: clamp preload lost after pi_s4:2 |
| `pi_spacer_1:1` | screw | drop_corner_nnp / 0.48675 | torsion | 0.770157 | 0.215844 | Conditional modeled release: transverse-slip self-loosening |
| `pi_spacer_1:2` | screw | drop_rear / 1.04625 | axial | 7.2559 | 0 | Conditional modeled release: clamp preload lost after pi_spacer_1:1 |
| `pi_spacer_2:1` | screw | drop_corner_nnp / 0.48675 | bending | 7.12929 | 1.07868 | Conditional modeled release: transverse-slip self-loosening |
| `pi_spacer_2:2` | screw | drop_rear / 1.04625 | axial | 26.1095 | 0 | Conditional modeled release: clamp preload lost after pi_spacer_2:1 |
| `pi_spacer_3:1` | screw | drop_corner_ppp / 0.54475 | bending | 12.6702 | 0.664105 | Conditional modeled release: transverse-slip self-loosening |
| `pi_spacer_3:2` | screw | drop_upright / 0.47025 | axial | 6.38467 | 0 | Conditional modeled release: clamp preload lost after pi_spacer_3:1 |
| `pi_spacer_4:1` | screw | drop_corner_nnp / 0.48675 | torsion | 0.747928 | 0.225 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `pi_spacer_4:2` | screw | drop_upright / 0.47025 | axial | 13.8631 | 0 | Conditional modeled release: clamp preload lost after pi_spacer_4:1 |
| `post_a_mount_1:1` | screw | wall_impact / 0.085 | torsion | 1.17492 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `post_a_mount_1:2` | screw | gravity_front / 0.026 | axial | 1.47491 | 0 | Conditional modeled release: clamp preload lost after post_a_mount_1:1 |
| `post_a_mount_2:1` | screw | drop_corner_ppn / 0.032 | torsion | 0.554554 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `post_a_mount_2:2` | screw | gravity_front / 0.029 | axial | 2.05019 | 0 | Conditional modeled release: clamp preload lost after post_a_mount_2:1 |
| `post_b_mount_1:1` | screw | drop_left / 0.441 | torsion | 1.44635 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `post_b_mount_1:2` | screw | drop_left / 0.441 | axial | 7.21312 | 0 | Conditional modeled release: clamp preload lost after post_b_mount_1:1 |
| `post_b_mount_2:1` | screw | drop_corner_npn / 0.4655 | torsion | 0.785407 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `post_b_mount_2:2` | screw | drop_left / 0.44075 | axial | 7.40576 | 0 | Conditional modeled release: clamp preload lost after post_b_mount_2:1 |
| `power_lead_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `power_lead_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after power_lead_post_mount:1 |
| `receiver_riser_bond` | adhesive | drop_corner_pnp / 0.00025 | bending | 0.0208583 | 0.0144 | Conditional modeled release: catalog/interface capacity |
| `rtk_radio_tape_bottom` | adhesive | drop_right / 0.45725 | bending | 0.24672 | 0.1 | Conditional modeled release: catalog/interface capacity |
| `rtk_radio_tape_top` | adhesive | drop_right / 0.45725 | bending | 0.243735 | 0.1 | Conditional modeled release: catalog/interface capacity |
| `rx_tape_bottom` | adhesive | vibration_z / 0.0455 | axial | 3.41355 | 1.98 | Conditional modeled release: catalog/interface capacity |
| `rx_tape_top` | adhesive | vibration_z / 0.0455 | axial | 3.46879 | 1.98 | Conditional modeled release: catalog/interface capacity |
| `sensor_lead_post_mount:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `sensor_lead_post_mount:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after sensor_lead_post_mount:1 |
| `servo_approach_post:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `servo_approach_post:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after servo_approach_post:1 |
| `servo_climb_post:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `servo_climb_post:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after servo_climb_post:1 |
| `servo_ears_a_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for servo |
| `servo_ears_a_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after servo_ears_a_1:1 |
| `servo_ears_a_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for servo |
| `servo_ears_a_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after servo_ears_a_2:1 |
| `servo_ears_b_1:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for servo |
| `servo_ears_b_1:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after servo_ears_b_1:1 |
| `servo_ears_b_2:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no bearing/pull-through strength for servo |
| `servo_ears_b_2:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: clamp preload lost after servo_ears_b_2:1 |
| `servo_output` | revolute_fit | autonomous_course / 0 | axial | 0 | 0 | Data gap; legacy zero is unknown: zero capacity: no qualified retention/material data |
| `servo_upper_post:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `servo_upper_post:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after servo_upper_post:1 |
| `so_fl_top:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `so_fl_top:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after so_fl_top:1 |
| `so_fr_bottom:1` | screw | drop_corner_pnp / 0.23325 | bending | 3.46044 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `so_fr_bottom:2` | screw | full_throttle / 2.28425 | axial | 15.6023 | 0 | Conditional modeled release: clamp preload lost after so_fr_bottom:1 |
| `so_fr_top:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `so_fr_top:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after so_fr_top:1 |
| `so_rl_bottom:1` | screw | drop_corner_npn / 0.44125 | torsion | 3.12825 | 0.458333 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `so_rl_bottom:2` | screw | drop_corner_nnn / 0.47425 | axial | 26.2544 | 0 | Conditional modeled release: clamp preload lost after so_rl_bottom:1 |
| `so_rl_top:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `so_rl_top:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after so_rl_top:1 |
| `so_rr_bottom:1` | screw | drop_corner_nnn / 0.609 | bending | 2.80848 | 2.29167 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `so_rr_bottom:2` | screw | drop_upright / 0.44475 | axial | 11.988 | 0 | Conditional modeled release: clamp preload lost after so_rr_bottom:1 |
| `so_rr_top:1` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: zero capacity: assembly preload exceeds proof/thread/pull-through capacity |
| `so_rr_top:2` | screw | autonomous_course / 0 | axial | 0 | 0 | Assembly overload model: clamp preload lost after so_rr_top:1 |
| `stub_press_fl` | press_fit | drop_inverted / 0.4645 | torsion | 2.79681 | 1.0097 | Conditional modeled release: catalog/interface capacity |
| `stub_press_fr` | press_fit | drop_left / 0.07575 | torsion | 2.35916 | 1.0097 | Conditional modeled release: catalog/interface capacity |
| `wheel_drive_rl` | drive_flat | drop_corner_npn / 0.442 | bending | 22.0686 | 16.2 | Conditional modeled release: catalog/interface capacity |
| `wheel_drive_rr` | drive_flat | drop_inverted / 0.46225 | bending | 20.226 | 16.2 | Conditional modeled release: catalog/interface capacity |
| `wheel_nut_fl` | screw | drop_corner_pnp / 0.02725 | bending | 75.5357 | 50.3083 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `wheel_nut_fr` | screw | drop_corner_ppp / 0.0175 | bending | 54.621 | 50.3083 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `wheel_nut_rl:1` | screw | drop_corner_npn / 0.4435 | bending | 12.9627 | 12.5 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `wheel_nut_rl:2` | screw | drop_corner_npn / 0.4435 | axial | 174.448 | 0 | Conditional modeled release: clamp preload lost after wheel_nut_rl:1 |
| `wheel_nut_rl:4` | screw | drop_corner_npn / 0.4435 | axial | 104.75 | 0 | Conditional modeled release: clamp preload lost after wheel_nut_rl:1 |
| `wheel_nut_rr:1` | screw | drop_inverted / 0.46875 | bending | 14.4957 | 12.5 | Conditional modeled release: preload / proof / female thread / pull-through limit |
| `wheel_nut_rr:2` | screw | drop_inverted / 0.46875 | axial | 286.044 | 0 | Conditional modeled release: clamp preload lost after wheel_nut_rr:1 |
| `wheel_nut_rr:4` | screw | drop_inverted / 0.46875 | axial | 144.827 | 0 | Conditional modeled release: clamp preload lost after wheel_nut_rr:1 |
