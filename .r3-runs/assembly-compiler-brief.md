# Object/interface assembly compiler ("puzzle pieces"): user idea

User: "check that all parts line up perfectly; a motor can't just be inside of another part.
Match the holes; single pieces have to fit together. One piece is an object, and to get a
compiled model you combine a bunch of objects. That forces objects to stack onto each other
like puzzle pieces; you can't design one thing inside another thing."

## Model
- **Object** = one catalog component type, modeled in its OWN frame (its own origin), with
  named INTERFACES. Each interface has:
  - `frame`: origin + z axis (outward normal of a seat face, or shaft/bore axis) + x axis
    (clocking reference), in the object frame;
  - `kind` and `params`. Kinds (extend as needed):
    - `face_mount` (planar seat, optional hole pattern)
    - `hole_pattern` (holes [x, y, d, thread|clearance|tapped, depth])
    - `shaft` (d, length, flat_offset?)
    - `bore` (d, depth, flat_offset?)
    - `spline` (teeth, d)
    - `header` / `socket` (pitch, rows, cols)
    - `ball_stud` / `ball_socket` (d)
    - `strap_seat` (width), `strap_slot`
    - `adhesive_pad` (area)
    - `connector_body`: an electrical connector housing, so harness connectors also mate
      physically
- **Robot** = a root object (fixed) plus a list of JOINS:
  `{a: <obj>.<iface>, b: <obj>.<iface>, kind: fasten|revolute|slider|ball|drive_flat, clock: angle, offset: dist, fasteners: {type, count}, joint: <joint id if moving>}`.
  Object poses are DERIVED, never authored.
- **Compiler** (`r3_model/assembly.py`, pure numpy):
  - Build a join graph and traverse from the root (BFS).
  - child_pose = parent_pose · T(a.iface) · R_flip(z opposite, x aligned, then clock) ·
    T(offset) · T(b.iface)⁻¹.
  - Moving joints are evaluated at their reference position (q=0); the compiler can also
    produce poses at any q.
  - Over-constrained loops (a deck on 4 standoffs, a HAT on 4 standoffs plus the GPIO
    header) are checked: every non-tree join must close within tolerance (position
    ≤ 0.05 mm default, angle ≤ 0.1°). Otherwise emit
    `assembly_misfit {join, position_error, angle_error, expected, actual}`.
- **Compatibility checks per join**, as structured diagnostics with numbers:
  - `interface_kind_mismatch` (shaft into socket, …);
  - `hole_pattern_mismatch`: patterns compared in the mated frame, after clocking; report
    the max hole-center offset and unmatched holes;
  - `hole_size_mismatch`: thread vs clearance/tapped per ISO 273;
  - `fit_out_of_range`: shaft/bore clearance vs fit class;
  - `header_mismatch`: pitch/rows/cols/pin-1 orientation;
  - `fastener_length_invalid`: grip from interface thicknesses plus engagement.
- **Structure rules:**
  - `object_not_joined`: every object must be joined (no free-floating objects; no
    authored poses).
  - `object_overconstrained_conflict`.
  - Each join's interfaces are used at most once (occupancy), except where the kind allows
    sharing.
- **Interference after compiling:** catalog envelopes (`orchestrator geometry.py` convex
  pieces) for a fast pre-check. The exact check is the CAD stage's mesh booleans.
  Joined pairs may only touch: contact is required at fasten seats, and penetration is
  never allowed. Code: `object_interference`.
- **Back-compatibility:** `Project.load` runs the compiler and fills
  `spec.components[cid].pose` with the compiled pose, so every downstream stage (graph,
  wiring, sim, electrical) works unchanged. If a spec still authors a pose for a joined
  object, compare them: `pose_conflict` if they differ by more than the tolerance; warn
  `authored_pose_ignored` otherwise. Components with neither joins nor a pose produce an
  error.
- **Moving joints:** `mechanical.joints` can be derived from revolute/slider joins (axis =
  interface z, origin = interface frame origin in the robot frame). Keep the explicit joint
  list compatible: if both exist, they must agree.

## Onshape (phase B, after the direct-CAD branch merges)
This mirrors exactly how a human assembles in Onshape:
- each object type is its own Part Studio (purchased objects in a reusable
  "R3 Parts Library" document), built with standard features at its own origin;
- a `mateConnector` feature at every interface frame;
- the robot is an Assembly: insert the instances, then fastened, revolute, slider and ball
  mates between the named mate connectors. Onshape's solver places everything.
R3 verifies that Onshape's solved occurrence transforms equal the compiled poses, within
tolerance. No FeatureScript (see policy).

## Phase A deliverables (now)
- Catalog schema `interfaces` on `ComponentType`.
- Spec schema `assembly: {root, joins}`.
- The compiler, compatibility checks, loop closure, occupancy and back-compat pose fill.
- A small fixture robot in `tests/fixtures/` that exercises:
  - a deck on 4 standoffs;
  - a motor on a plate via a 2-hole pattern;
  - a wheel on a shaft (revolute);
  - a HAT on a Pi via the header plus 4 standoffs.
- Negative tests:
  - a hole pattern offset by 0.5 mm fails;
  - a standoff 1 mm too short gives a loop misfit;
  - a motor joined to a seat that would put it inside the plate is caught by interference;
  - a missing join gives `object_not_joined`;
  - a shaft larger than its bore.
- An `assembly` stage (or part of the model stage) that writes `build/model/assembly.json`
  (compiled poses, join graph, loop residuals) and a PNG of the join graph.
