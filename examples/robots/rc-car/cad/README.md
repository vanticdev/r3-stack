# RC car CAD (direct, no FeatureScript)

The car is built by `r3_onshape` from `robot.yaml` with standard Onshape features
only (sketches, extrudes, revolves, ISO Hole features) plus an Assembly mated on the
verified interfaces. The live document is pinned in `../r3.lock.yaml`. Evidence
(headless Onshape renders and full-lock renders of the exported meshes) is in
`evidence/`.

Run: `python .r3-runs/cad_attempt.py --force` (stage) and
`python -m r3_onshape.negative examples/robots/rc-car` (negative tests).

## Design problems the checks found, and the fixes

| # | Check / diagnostic | Problem | Fix |
|---|---|---|---|
| 1 | `part_not_retained` (contact is not attachment) | The previous design held parts only by touching. | 61 fasteners and 36 verified interfaces. Every part reaches the chassis through verified attachments. |
| 2 | `cad_interference`, `fastener_hole_missing`, `connector_misaligned` | HAT 2.5 mm off the Pi hole pattern; `J_GPIO` 2.5 mm off the Pi header. | HAT x 102.5 -> 100 mm (left edge flush); catalog `J_GPIO` x -2.5 -> 0. Negative test `hat_offset_2_5mm` reproduces it. |
| 3 | `cad_interference` | The Pi's 17 mm envelope box went through the HAT. | Pi modeled as PCB + component zone + USB/Ethernet block + 2x20 header; HAT on 11 mm M2.5 standoffs, 6 mm pin insertion. |
| 4 | local preview + `cad_interference` | Gear cover (spur 30 deg above the pinion) crossed the right axle; spur cover and motor plate hit the deck. | Motor to x 49 mm, case lengthened to x 30, spur 40 deg above the pinion (6.2 mm axle clearance), plate top lowered to 74 mm, deck notched over the cover. |
| 5 | geometry review | Motor terminals sat inside the motor plate (connectors at the shaft end). | Terminals moved to the rear end bell. |
| 6 | screw/spur clearance; `assembly_pose_mismatch` | Motor screws on the horizontal line ran behind the spur gear; rounded screw positions were 6 um off the rotated motor's holes. | Motor rotated -50 deg (screws perpendicular to the mesh line, mesh-adjust slots), exact positions (57.0348, 55.5756) / (40.9652, 36.4244) mm. |
| 7 | `steering_range_unreachable`, lock-up, Ackermann | Horn balls at +-12 mm locked up. +-51 deg servo travel drove the inner knuckle to 0.79 rad (> 0.45 stop) with ~600 % Ackermann. | Parallel steering arms, horn balls 19.8 mm behind the spline at +-6 mm, servo end points +-30 deg (1278/1722 us). Inner 0.445 rad, outer 0.367 rad, 97 % Ackermann, transmission angle >= 62 deg. |
| 8 | interference | Servo posts collided with the suspension arms; the 70 mm front chassis was too narrow for the posts. | Servo turned along x, front plate widened to 80 mm (wheel sweep still clear). |
| 9 | `cad_interference` | Knuckles intersected the solid wheel envelope; wheels had no hub bore or nut seat. | Wheels get a 14 mm hub recess, a 6.05 mm bore (front) / 6 mm D-bore (rear) and a nut seat. Knuckle upright and rear bearing carrier sit inside the recess. |
| 10 | kingpin retention | A screw kingpin cannot be preloaded without clamping the knuckle. | Threadlocked M3x20 pivot screw (`preloaded: false`), 0.15 mm head clearance, 0.1 mm radial running clearance, 7.85 mm thread in the arm. |
| 11 | `fastener_tool_access_blocked` | The steering arm covered the kingpin screw head. | Arm routed around the kingpin (7.6 mm clear of the tool path). |
| 12 | `thread_engagement_short` (2 d in plastic) | PA12 parts need >= 6 mm of M3 thread. | Knuckle steering arm 9 mm (8 mm stud), arms 8 mm (M3x10, 7 mm), kingpin 7.85 mm. |
| 13 | `magnet_release_marginal` | 3x2 mm N52 magnets on a 16 mm ring leave 10.6 mT between magnets, above the A1120 release point: missed pulses. | 2x1 mm N52 on a 20 mm ring: 69 mT at the die (B_OP max 17.5 mT), about 1 mT between magnets. Encoder lowered 2 mm (1.0 mm air gap). |
| 14 | `cad_feature_failed` | Two blind tapped holes meeting mid-spacer failed to regenerate. | Pi spacers threaded through, as real F-F spacers are. |
| 15 | `cad_part_missing` / builder | The tie rod's outer socket was added before the rod, so Onshape made it a separate part. | Solids ordered so every ADD touches the body (unit test). |
| 16 | revolute fit / `joint_interface_unverified` | Rear wheels had no bearing; the joints had no physical axis. | Rear hub carriers with press-fit MR106ZZ bearings, D-flat axles with collars, spacers and M6 nylocs clamping the stack. |
| 17 | RF geometry | `antenna_height_above_mount` was a guess (0.10 m). | Antenna mast and T-antenna modeled; feed point derived from CAD as 0.0829 m above the receiver mount (`cad_model.json: rf`). |
| 18 | strap / adhesive | Battery, fuse, ESC, receiver and GPS were floating. | Hook-and-loop strap and zip tie through chassis slots; 1.6 mm foam tape (contact >= 80 %); components raised 1.6 mm. |

Remaining warnings are tool-access notes for assembly order. Examples: motor screws
are reached with the right rear wheel off, and Pi deck screws are driven before the
deck is mounted.
