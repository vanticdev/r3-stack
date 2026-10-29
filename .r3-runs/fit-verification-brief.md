# Definitive mechanical fit verification: design decisions (coordinator)

Problem: both independent reviewers found that CAD verification treats parts within
0.05 mm as "attached" and accepts declared joints without physical hardware. Fix it
definitively. A part is fitted only when a physical interface is verified on exact
Onshape B-rep. Contact alone counts for nothing.

## 1. Spec and catalog additions (you own these changes)
- `r3_model.spec.Mechanical`:
  - `fasteners: list[Fastener]`. Fields: id; type (catalog fastener id); position
    (head seat point, robot frame); axis (insertion direction); clamps (ordered
    part ids, head side first); thread_into (part with tapped hole, or null); nut
    (catalog nut id, or null).
  - `interfaces: list[Interface]`. Fields: id; kind; parts [a, b]; axis_point;
    axis_dir; params. Kinds:
    - `revolute_fit` (shaft↔bore, clearance fit range, min engagement, axial retainer ids)
    - `press_fit`
    - `drive_flat` (D-flat torque transfer: flat offset, min overlap)
    - `ball_joint` (sphere↔socket concentric, radii fit)
    - `spline` (servo output)
    - `adhesive` (min contact-area fraction of the smaller face)
    - `strap` (band through slots around a part)
    - `magnetic_target` (encoder magnet↔sensor air gap vs datasheet operate point)
    - `connector_retention` (board-to-board header mate: pin rows coaxial, insertion depth)
  - `Joint.interface: str | None`. It is REQUIRED for revolute/continuous/prismatic
    joints and validated.
  - `Transmission.path: list[str]`: an ordered list of interface ids from actuator to
    driven joints, forming a continuous chain. Consecutive interfaces share a part.
- `r3_catalog.schema.Mechanical`:
  - `features: list[Feature]`. Fields: id; kind (bore, d_bore, shaft, d_shaft,
    tapped_hole, clearance_hole, spline, face, slot, sphere, socket); position;
    axis; diameter; depth; flat_offset. These are the datasheet interfaces of
    purchased parts. The generator realizes them on the envelope: holes and bores
    are cut, shafts and spheres are added.
  - Mount holes that already exist become features.
- Catalog `fasteners.yaml`. Use real ISO dimensions: M2.5/M3/M4 SHCS (ISO 4762
  head dia/height), M3 button heads, M3 and M6 nyloc nuts (ISO 10511), M3 ball
  studs, and heat-set inserts if needed. Include d, pitch, minor diameter, head
  dia/height, standard lengths, and clearance-hole close/normal sizes (ISO 273),
  with provenance.

## 2. RC-car mechanical redesign (encode it in robot.yaml + catalog; adjust numbers if checks demand)
- **Rear transmission.** The purchased transmission envelope becomes a custom shape:
  - the body box;
  - a motor plate on the -y side;
  - a gear cover outboard of the plate, which encloses a 17T pinion and 66T m0.6 spur
    (internal, vendor-verified; the internal ratio is set so the total stays 9.6:1).
  Catalog features:
  - `motor_axis`: an 8 mm pinion clearance bore in the plate;
  - `motor_face`: the plate inner face;
  - 2× M3 clearance slots at 25 mm spacing about the motor axis;
  - `output_left` / `output_right`: D-bores, 6 mm, flat offset 2.5 mm, 10 mm deep, on
    the outer faces;
  - 4× M3 tapped holes in the bottom for chassis screws.
  Place the motor so its shaft is coaxial with `motor_axis` and its face sits flat on
  `motor_face`. Earlier hand calculation: motor center ≈ (50, *, 46) mm robot frame,
  motor body spanning y −30..+20 mm. Re-verify everything with the checks; do not
  trust these numbers blindly.
- **Motor** catalog features: a 3.17 mm D-shaft (filed flat, offset 1.385 mm) of
  real length, and 2× M3 tapped face holes at ±12.5 mm.
- **Rear axles** (fabricated steel): D-shafts that engage the transmission outputs
  (`drive_flat`) and the wheel D-bores (`drive_flat`), with an axial retainer (an M6
  nyloc on a threaded end, or a modeled e-clip). Left and right axle lengths may differ.
- **Wheels:** a hub bore (front: round 6.05 mm on a stub axle with clearance fit
  `revolute_fit`; rear: D-bore) and a nut seat. Bearings are allowed as purchased
  parts (MR106-style 6×10×3), if you want to model them, which is better.
- **Front:**
  - **Suspension arms** are screwed to the chassis, with an M3 tapped kingpin hole.
  - **Knuckles** get a vertical 3.2 mm kingpin bore, with an M3 screw kingpin
    threading into the arm; this is the `revolute_fit` for steer_*. The steer joint
    axis must be coaxial with that screw.
  - **Stub axles** are press-fit into the knuckles (`press_fit`), with the front wheel
    on the stub axle (`revolute_fit`) retained by a nyloc.
  - **Steering arms** are on the knuckles, with M3 ball studs.
  - **Two tie rods** with ball-socket ends run from a servo horn ball pair to each knuckle
    arm ball.
  - **The servo horn** is on the servo spline (`spline` interface, plus a horn screw
    M3 into the spline).
  - **The servo** mounts via its ears with 4 screws onto two fabricated servo posts.
    The posts are screwed to the chassis.
- **Deck stack:**
  - chassis → standoffs: screws from below into the tapped standoffs;
  - deck → standoffs: screws from above;
  - Pi: on 4 M2.5 spacers on the deck;
  - HAT: on 4 M2.5 stacking standoffs on the Pi.
  HAT mounting holes must coincide with the Pi holes (HAT spec: same 58×49 pattern;
  the HAT left edge is flush with the Pi left edge). Fix the HAT pose and the
  catalog J_GPIO position if the fastener check says they are misaligned. I believe
  the HAT is currently 2.5 mm off. The GPIO header↔socket is a
  `connector_retention` interface.
- **Battery:** a hook-and-loop strap through two chassis slots (`strap`).
- **ESC / receiver / GPS:** adhesive (double-sided foam tape) with a contact-area check.
- **Encoder:** an M2 screw to the transmission, plus magnets in a fabricated
  magnet-carrier ring on the left axle. The `magnetic_target` air gap must be
  0.5–2.0 mm; derive the field at that gap from N52 magnet dimensions and compare it
  with the A1120 operate point (datasheet B_OP max). Pulses per wheel revolution must
  equal the number of magnets.
- **Fuse:** zip-tied to a deck standoff or chassis tie slot. A zip tie is a strap.
- **Antenna:** add a fabricated antenna mast tube and the receiver's T-antenna as
  parts. The receiver behavior `antenna_height_above_mount` must be derived from
  this geometry (write the derived value into cad_model.json for the sim:
  `rf.antenna_position`).

## 3. CAD generation (`integrations/onshape/r3_onshape/featurescript.py`)
- Fabricated parts get their holes and bores cut from the fasteners and interfaces
  that use them: clearance holes per ISO 273 normal fit, and tapped holes at the
  thread minor diameter to the needed depth.
- Purchased parts get ONLY their catalog features, never holes derived from the spec.
  So a misplaced fastener collides with them.
- Every fastener, nut, strap and magnet is its own named part (`fastener:<id>` etc.).
  Exact interference then proves holes exist and align. Screw shanks are modeled at
  the major diameter. Inside a tapped hole the screw↔part intersection volume is
  π/4·(d² − d_minor²)·engagement. Verify the measured volume against it, which
  measures engagement length; ±15 % tolerance. No other intersections are allowed.
- Keep one part per component. Parts are keyed by immutable component id, and you
  must reject name collisions and collisions of the sanitized FeatureScript id.

## 4. Verification on exact geometry (an Onshape FeatureScript eval, read-only temp context; already proven to work for opBoolean/evDistance/evVolume)
For each fastener:
- no illegal interference;
- head underside in contact with the first clamped part;
- consecutive clamped parts in face contact around the hole, with the contact area
  measured by extruding the face 0.05 mm into the other part, intersecting, and
  dividing the volume by the depth;
- edge distance: an annulus with r from the hole radius to 1.0·d beyond it, fully
  inside material for each clamped part (≥ 95 % volume);
- thread engagement ≥ 1.5·d for steel/aluminum and ≥ 2·d for plastics; a nut fully
  engaged with the bolt protruding ≥ 1 pitch;
- the tip not hitting anything;
- tool access: a cylinder of head-dia+2 mm × 60 mm along −axis from the head is clear.
  Report blocked access as a warning.

For each interface kind, run the geometric probe:
- coaxial cylinder faces found by `evSurfaceDefinition` (axis angle < 0.2°, offset
  < 0.02 mm), shaft convex and bore concave, radial clearance within the fit range,
  axial overlap ≥ the minimum;
- D-flats: coplanar planar faces parallel to the axis at the offset;
- spheres concentric;
- adhesive contact area ≥ fraction;
- strap: passes through the slots without interference and touches the strapped part;
- magnetic gap.

Joints: the referenced interface must exist and be verified, and its axis must equal
the joint axis.

Transmissions: the path chain is continuous from the actuator part to every driven
joint child.

Retention: build a graph of VERIFIED attachments only. Every part, including every
fastener, must reach `chassis`. Report `part_not_retained` with the subjects
otherwise. Delete the old contact-as-attachment rule.

Motion:
- Write a Python linkage solver (`r3_onshape/kinematics.py`, pure numpy) for the
  servo-horn/two-tie-rod/steering-arm linkage. Over the full servo travel it derives
  wheel angle vs servo angle per side, the Ackermann percentage, and detects toggle or
  lock-up and unreachable joint limits.
- Write the derived steering table into cad_model.json (`kinematics.steering`) so the
  sim uses geometry instead of the 0.5 ratio.
- Then sweep it in CAD: at ≥ 9 samples across travel, plus wheel spin where relevant
  (the spin pose is irrelevant for round wheels, but include D-flat parts), transform
  the moving subtrees (knuckle+wheel+stub axle+nuts+tie rods+horn) and check
  interference against everything static. Include a 0.5 mm clearance margin.
  Codes: `cad_motion_interference`, `steering_linkage_toggle`,
  `steering_range_unreachable`.

Every failure is a structured diagnostic with subjects and numeric data.

## 5. Cache and identity fixes
- The CAD cache key hashes: the complete generated FS source, `interference/probe .fs`
  sources, the validator version constant, `mechanical.*` from the spec, and the
  catalog entries used.
- Pin the Onshape microversion of the verified state in cad_model.json and
  r3.lock.yaml, and hash the exported meshes.

## 6. Process
- Build in Onshape from scratch via the existing `r3_onshape.sync` +
  `NativeBridgeTransport` (see `.r3-runs/cad_attempt.py`). The folder is
  `c3f7706a252cbfca26630a10`.
- Every failed attempt must be deleted (the existing sync does this). Only one live
  document should remain at the end, and it is pinned in r3.lock.yaml. Delete the
  currently pinned attempt-6 doc (314246dec6d07475f0ca7c44) once your new build passes.
- Capture an isometric viewport screenshot plus close-ups of the front steering and
  the rear drivetrain with the onshape-native MCP tools if you have them. Otherwise
  export the STL meshes and render PNGs with matplotlib (`build/cad/*.png`).
- Unit tests that need no Onshape: fastener math, the linkage solver, retention
  graph logic, and the probe-result → diagnostic mapping, using recorded fixture JSON
  from a real run.
