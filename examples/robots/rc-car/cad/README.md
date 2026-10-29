# RC car CAD (phase B: joined objects, organized Onshape document)

The car is compiled from 83 objects joined at catalog interfaces (`robot.yaml` `assembly.joins`).
Only the chassis has an authored pose. `r3_onshape.organized` builds the CAD from the joins:

* **R3 Parts Library**: purchased parts and fasteners. There is one Part Studio per family (Pi,
  servo, transmission, wheels, screws...), each modeled at its own origin with named
  `mc_<interface>` mate connectors. The library is versioned and inserted by reference. Its tabs
  are in `01 Electronics / 02 Mechanical / 03 Fasteners`.
* **R3 RC Car**: `01 Variables` (`#plate_thickness`, `#deck_thickness`, `#wheelbase`,
  `#kingpin_track`), `02 Fabricated parts` (16 Part Studios), `04 Subassemblies` (Rear drivetrain,
  Front steering L/R, Steering linkage, Deck and electronics stack, Power), and
  `05 Top-level assembly`. Purchased parts (`03`) are the linked library.
* Every feature is a standard one on the Top/Front/Right planes: start-offset extrudes, Hole
  features started from part faces, and Mirror for the right-hand parts. The build creates no
  construction planes. All 191 sketches are fully defined (`WELL_DEFINED`).
* Mates sit on the interface connectors. The solved occurrence transforms equal the compiled
  poses: 132 instances, 0.0 mm / 0.0 deg, 133 mates, none failing.

The live documents are pinned in `../r3.lock.yaml` (`cad`, `cad_library`). Evidence is in
`evidence/`: headless REST shaded views of every subassembly and the top level, representative
Part Studios, full-lock renders, `negative_tests.json` and `cad_verification_summary.json`.

Run it with `r3 validate -C examples/robots/rc-car --stage assembly,cad`. The organization pass
(feature, assembly and tab folders; hidden reference geometry) needs the onshape-native bridge:
`python -m r3_onshape.organized organize examples/robots/rc-car`. Negative tests:
`python -m r3_onshape.negative_organized examples/robots/rc-car`.

## Phase B findings and fixes

| Check | Finding | Fix |
| --- | --- | --- |
| exact interference (assembly stage, plan solids) | The 18T pinion (9.066:1 for top-speed margin) moves the motor 0.3 mm along its mesh slots; the 540 can then dips 0.2 mm (34 mm^3) into the chassis plate. | Motor relief cut in the plate (38 x 52 mm). |
| exact interference / fit `cad_interference` | Through-bolt nyloc nuts on the mast were mated with flipped axes: the nut body ran 51 mm^3 into the deck. | Nuts mate with aligned axes (their body continues outward from the hole's far face). |
| fit `encoder_ppr_mismatch` | The magnetic-target check ran before the magnet bonds were verified (0 retained magnets). | Sensing checks run after all attachments. |
| fit `adhesive_contact_insufficient` | The servo-ear caps overhang the 7 mm ear end by 1.6 mm: 73 % of the rim is bonded. | Accepted at 70 % for a bonded dust cap (12.6 mm^2), recorded as the join's `verify.min_fraction`. |
| `cap_keepout_exceeded` (warning) | The HAT screw caps (outer Ø 6.7 mm) exceed board.json's 2.55 mm top-component keep-out. | Request to the PCB stage: 3.45 mm rule areas (`.r3-agents/requests/pcb.md`). |
| HAT holes vs board.json | The HAT+ pattern sits 0.25 mm toward the GPIO edge (board.json holes y +24.75 / -24.25). | HAT interfaces, socket and solids follow board.json; the HAT center is 0.25 mm off the Pi pattern. |
| encoder on the left axle (queue item 13) | One-wheel spin was invisible to the encoder. | Magnet carrier pressed on a spur-shaft extension, Hall board on the gear cover. It reads the differential carrier (4 x 2.4727 = 9.891 pulses per mean wheel revolution). |
| fuse upstream of the splice (queue item 5) | 79 mm of 18 AWG ran unprotected from the splice. | Fuse holder turned and the splice moved to 6 mm off its input: 7 mm routed (43 mm cut). `wire_unprotected_by_fuse` remains a warning for that stub. |
| steering calibration | Firmware assumed 0.45 rad; the linkage reaches inner 0.445 / outer 0.367 rad. | `max_steer_rad` 0.4021 (Ackermann-equivalent single-track angle from the CAD table). |

### Open wiring findings (need Part Studio edits; the Onshape feature quota is exhausted)

The wiring stage went from 105 errors (55 of them `route_infeasible`) on `r3/integration` to 46.
The rest are layout problems that a route hint cannot fix. Each fix needs a Part Studio change,
and the per-user quota on the Part Studio features endpoint is used up for this window.

| Diagnostic | Problem | Planned fix |
| --- | --- | --- |
| `wire_intersects_component` servo_post_b 3.5 mm, `cap_cable_interference` cap_ear_b2 -0.08 mm | The DS3218 lead leaves the case end face 0.5 mm in front of servo post B, which is as wide as the servo. | A 6 x 5 mm cable notch in post B under the lead exit. The lead then runs below the ear cap. |
| `cable_sags_into_moving_part` tie_rod_r 1.2 mm; sim `steering_sweep` -1.85 mm | The lead spans about 85 mm unsupported from the servo to the deck edge, over the moving linkage. Route hints do not fix it: a `keep_away` alone changed nothing, and a route under the tie rods meets the horn and ball studs (54 errors). | A printed cable clip on the servo-post cap and one at the deck's front edge, holding the lead above the linkage sweep. |
| `wire_intersects_component` esc 11.5 mm (W_MOT_B) | The motor bullets point +y, 4 mm from the ESC's face. | Move the ESC pad +x beside the battery so the bullets have free space (a chassis `mc_esc_pad` change). |
| `wire_clearance_violation` gearbox: gps_lead 2.6 mm, esc_signal 2.3 mm | Both leads leave their connectors facing the spur envelope 11-12 mm away, closer than one minimum bend radius. | Clock the GPS tape joint +90 deg (lead toward the HAT), and turn the ESC with the pad move. |
| `wire_clearance_violation` encoder_lead: enc_ring 4.0 mm, motor 5.7 mm | The Hall board's lead leaves 4 mm under the ring it senses. | The 8 mm moving-part rule should treat the sensed ring as the sensor's own target. The lead clears the motor once the ESC move frees the space behind it. |
| `wire_clearance_violation` power_y: motor 4.5 mm, gearbox 4.0 mm | The splice sits 6 mm off the fuse input, beside the motor can. | Resolved with the ESC move (the Y-harness then runs along the battery). |

## Finish lane A: layout and wiring fixes (2026-10-29)

Every change goes through the joins and the object interfaces. The Onshape Part Studio features quota was
exhausted for the whole session (probe: HTTP 429 on `/partstudios/.../features`, retry after 7.3 h at
12:47). So `cad_model.json` is recomposed offline with `python -m r3_onshape.recompose examples/robots/rc-car`.
Unchanged parts keep their recorded Onshape geometry and move to the compiled poses. Parts whose plan solid
changed are built from their plan with exact manifold3d booleans and analytic faces, and the cad stage
reports them as `cad_part_rebuild_pending` until the next organized build (`R3_CAD_FORCE=1`).

| Change | Why | Onshape |
| --- | --- | --- |
| Routing floor = base plate (largest footprint) | The battery strap loops under the plate. As the "lowest structure" it had set the router's floor at 54.5 mm, so no harness could run along the plate. | - |
| ESC clocked 180 deg, pad moved beside the battery (chassis `esc_pad` (-7, 47)) | The motor bullets' solder cups were inside the ESC envelope (W_MOT_B 11.5 mm). `J_MOT` now faces the motor. | pose only |
| Battery 17 mm aft (`battery_seat`, `strap_slots` x 11 mm) | The 12 AWG lead's 16 mm U-turn hit servo post A. | chassis slots |
| Fuse on the right of the battery, clocked 180 deg (`fuse_seat`, `tie_slots` (36, -37)) | Its old place is now under the battery; SP stays 7 mm from the fuse input. | chassis slots |
| `so_rl` standoff to (-99, 47) chassis / (-89, 47) deck, deck rear-left tab | It stood in the motor-lead corridor right behind the bullets. | chassis + deck |
| GPS tape joint clocked +90 deg | The lead now leaves toward the HAT instead of over the deck edge, 2.5 mm from the spur cover. | pose only |
| Hall board 10 mm taller, lead on its outward lower edge | The lead sat 4 mm under the spinning magnet carrier. It is now 15 mm away (sensor face and air gap unchanged). | library part |
| `servo_post_b` -> `servo-post-al6061-cable`: lead pocket through the post, open on its +y side | The DS3218 lead leaves 0.5 mm in front of post B. It now turns inside the post and runs aft along the plate under the linkage. Web 7 x 9 mm, ~10 MPa at stall. | new part |
| Deck lead slot (24 x 9 mm) and power slot (16 x 10 mm), printed `lead_post` on a deck edge tab | The servo/ESC leads climb to top-entry headers. The HAT's fused feed comes up in line with its XT30. | deck + new part |
| Support placement: tie posts per slender solid (mast tube), hull-shaped tie loops, flat-face projections, printed saddles (6/10/16 mm), re-placement of supports that make a route infeasible, spans across unheld junctions, CAD slots as grommeted pass-throughs | Supports had only been found where the first pass already lay flat on a face. | - |

Still open (see the final report): the power-harness 12 AWG bends around the fuse/splice corner and the ESC
XT60 pair placement, the servo pigtail fan-out bend inside post B, and the climb spans to the HAT's top-entry
headers. Also open: `cad_mesh_invalid` (lane C), the HAT `J_RX`/`J_BATT` exits over the Pi USB block
(lane B, `.r3-agents/requests/lane-b.md`), the Pi 1.276 mm board (queue item 20, deferred: lowering the stack
by 0.324 mm drives the 8.5 mm HAT socket 0.10 mm into the STEP header insulator), and the Onshape rebuild of
the pending parts plus the organization pass (queue item 26), both waiting on the quota.

## Direct-CAD history (before phase B)

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
