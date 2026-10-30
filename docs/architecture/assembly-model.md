# Assembly model: objects, interfaces and joins

A robot is compiled from **objects** joined at matching **interfaces**, like
puzzle pieces. Only the root object has an authored pose. Every other pose is
derived from the joins. As a result, a part cannot be placed inside another part,
and its holes must line up with the holes it bolts to.

- Compiler: `packages/robot-model/r3_model/assembly.py` (pure numpy).
- Stage: `assembly`, which runs before `model`. It writes
  `build/model/assembly.json` and `build/model/assembly_graph.png`.
- Fixture: `tests/fixtures/assembly_bench/`.

## 1. Author an object (catalog)

An object is a catalog `ComponentType`, modeled in its own frame. The frame
origin follows the catalog envelope convention:

- boxes and z-axis cylinders: the center of the bottom face;
- x- and y-axis cylinders: the center of the body.

Add one entry under `interfaces` for each mating feature:

```yaml
- id: standoff-m3-30
  kind: structure
  mechanical: {shape: cylinder, size: [5.5 mm, 30 mm], mass: 0.003}
  interfaces:
    bottom: {kind: hole_pattern, z: [0, 0, -1], params: {holes: [[0, 0, 3 mm, tapped, 6 mm]]}}
    top:    {kind: hole_pattern, origin: [0, 0, 30 mm], params: {holes: [[0, 0, 3 mm, tapped, 6 mm]]}}
```

Each interface has a frame defined in the object frame:

- `origin`: a point on the seat face, or on the shaft or bore axis. It must lie
  on the envelope surface; otherwise you get `seat_not_in_contact`.
- `z`: the **outward** normal of the seat face. For a shaft or bore, it is the
  axis pointing out of the part: out of the motor along the shaft, or out of the
  bore opening.
- `x`: the clocking reference, perpendicular to `z`. The frame's `y` is `z × x`.
  Hole positions and header grids are written in `(x, y)`.

| kind | params |
| --- | --- |
| `face_mount`, `hole_pattern` | `holes: [[x, y, d, clearance\|tapped\|thread, depth]]`. `depth` is the plate thickness, the thread depth or the stud length. Optional: `thickness`, `min_engagement_factor` (default 1.0), `keyed`. |
| `shaft` / `bore` | `d`, `tol` (ISO 286 grade: shafts `c11 f7 g6 h6 k6 n6 p6 s6`, holes `H7 H8 H11`), `length` / `depth`, `flat_offset` (the flat is on +x) |
| `header` / `socket` | `pitch`, `rows`, `cols`, `pin1: [x, y]`. The grid is centered on the origin, with columns along x. |
| `spline` | `teeth`, `d`, `gender: male\|female` |
| `ball_stud` / `ball_socket` | `d` |
| `strap_seat` / `strap_slot` | `width` |
| `adhesive_pad` | `area` |
| `connector_body` | `type`: a catalog connector id, checked against `mates_with` |

Any kind also accepts `capacity` (default 1), the number of joins that may use
the interface.

## 2. Author joins (robot.yaml)

```yaml
components:
  plate: {type: bench-plate, pose: {xyz: [0, 0, 0]}}   # only the root has a pose
  s_fl:  {type: standoff-m3-30}
  panel:  {type: bench-panel}
assembly:
  root: plate
  joins:
    - {a: plate.so_fl, b: s_fl.bottom, fasteners: {type: M3x8}}
    - {a: s_fl.top, b: panel.h_fl, fasteners: {type: M3x8}}
    - {a: plate.bracket_seat, b: bracket.foot, clock: 0 deg, fasteners: {type: M3x10, count: 2}}
    - {a: axle.shaft, b: wheel.hub, kind: revolute, joint: wheel_spin}
```

Join fields:

- `kind`: one of `fasten` (default), `revolute`, `slider`, `ball` or `drive_flat`.
- `clock`: the rotation of `b` about `a`'s z axis.
- `offset`: a gap along `a`'s z axis. A positive offset leaves a gap.
- `fasteners`: written as `M<d>x<length>`, with an optional `count`.
- `joint`: the id of a mechanical joint, used by moving joins.
- `limits`: joint limits.
- `fit`: the required shaft/bore fit, one of `clearance`, `transition`,
  `interference` or `any`.
- `id`: the join id. The default is `<a>~<b>`.

The default tolerances are `assembly.tolerance: {position: 0.05 mm, angle: 0.1 deg}`.

**Placement.** The compiler places `b` with

`pose_b = pose_a · F_a · Rz(clock + q) · Tz(offset) · Rx(π) · F_b⁻¹`.

The two z axes oppose each other, and the x axes align before the clock is
applied. In `a`'s interface frame, a point `(x, y)` of `b`'s face lands at
`Rz(clock)·(x, −y)`. The mate is a half-turn, so writing a join as `b → a`
gives the same placement as `a → b`.

**Clock.**

- If you omit `clock`, the compiler infers it from the hole pattern, or from the
  header grid plus `pin1`.
- A symmetric pattern, such as a 2-hole motor face or a 58 × 49 mm Pi rectangle,
  matches at several angles. The compiler then reports `clock_ambiguous` with the
  candidate angles; add an explicit `clock`.
- A rotationally free seat, such as a single centered hole or a round shaft,
  defaults to clock 0. Keep the x axes of such seats aligned with the object's x
  axis.

**Moving joins.**

- The compiled poses use `q = 0`.
- `AssemblyResult.poses_at({"wheel_spin": q})` evaluates any `q`: radians for
  revolute joins, metres for sliders, and a rotation vector for ball joins.
- Revolute and slider tree joins produce `mechanical.joints` entries. The axis is
  the interface z axis and the origin is the interface origin. If the spec also
  lists the joint explicitly, the two must agree, or you get `joint_conflict`.

**Loops.** The graph is traversed breadth-first from the root, in join order.
Every join outside the tree, such as the other three standoffs under a panel,
must close within tolerance:

- Spin about the axis is ignored for clock-free seats and revolute joins.
- Travel along the axis is ignored for sliders.
- A loop with exactly one moving join and otherwise rigid joins locks that join
  (`object_overconstrained_conflict`).

## 3. Diagnostics

Every diagnostic carries numeric `data`, in SI units.

| code | when |
| --- | --- |
| `assembly_misfit` | A loop does not close: `position_error`, `angle_error`, `expected`, `actual`, `error_vector`, `cycle` |
| `hole_pattern_mismatch` | A hole-center offset exceeds the floating/fixed fastener allowance Σ(H − F)/2 over the clearance holes, or too few holes match: `max_offset`, `allowance`, `unmatched_a/b`, `matching_clocks_deg` |
| `hole_size_mismatch` | A clearance hole is outside the ISO 273 fine-to-coarse range, two tapped (or two threaded) holes are paired, or thread sizes differ |
| `fastener_length_invalid` | The screw is outside `[grip + engagement, grip + thread depth]`, or is shorter than grip plus an ISO 4032 nut for a through bolt |
| `fit_out_of_range` | The ISO 286 clearance range breaks the required fit (running fits ≤ H11/c11, press fits ≤ H7/s6). Also: a flat that does not enter, or a ball socket clearance outside 0 to 0.2 mm |
| `header_mismatch` | Pitch, rows or columns differ, or pin 1 is offset |
| `clock_ambiguous` | Several clock angles fit; set `clock` |
| `interface_kind_mismatch`, `join_kind_invalid`, `interface_unknown` | The interface kinds cannot mate, the join kind is not allowed for that pair, or the interface name is wrong |
| `interface_occupied` | An interface is used by more joins than its `capacity` |
| `object_not_joined` | An object has no joins (error when it has no pose, warning when an authored pose is kept), or its group never reaches the root |
| `object_overconstrained_conflict`, `loop_mobility_low` | A moving join is locked by a rigid loop, or the Kutzbach mobility is below 1 |
| `joint_conflict`, `pose_conflict`, `authored_pose_ignored` | An explicit joint or authored pose disagrees with (or merely repeats) the compiled one |
| `object_interference` | Compiled envelopes penetrate. Exception: a shaft inside its joined bore |
| `seat_not_in_contact` | A fasten seat does not touch its object's envelope, or has an offset gap |
| `drive_flat_invalid`, `spline_mismatch`, `strap_mismatch`, `connector_mismatch` | Kind-specific checks |

## 4. Back-compatibility

`Project.load` compiles a spec that has an `assembly:` section. It then writes
the compiled pose into `spec.components[*].pose`. It also fills `mount` with the
tree parent, unless `mount` is authored, and adds the derived joints. Downstream
stages (graph, wiring, sim, electrical) therefore run unchanged. Specs without
`assembly:` are not touched: the catalog is not even loaded.

## 5. Derived mechanics (fasteners, fit interfaces, joints)

The CAD fit checks, the motion sweep and the simulator read robot-frame
`mechanical.fasteners`, `mechanical.interfaces` and `mechanical.joints`. With an
`assembly:` section these are **derived** from the joins at load time
(`r3_model/mechanics.py`), so no robot-frame coordinate is authored twice:

| Join | Derived |
| --- | --- |
| `fasten` with `fasteners`, clearance + tapped hole pair | one screw per pair (`<join id>` or `<join id>_<n>`): head on the clearance side's far face, threading into the tapped side; `part` defaults to `shcs-m<d>` |
| both holes clearance, `fasteners.nut` | a through bolt ending in that nut, head on side `head`, grip = both thicknesses + offset |
| a `thread` hole on an object + a tapped hole | a fastener whose body is that object (kingpin screw, ball stud, stacking standoff): type from the interface's `fastener` param |
| a nut object (`fastener: nut-…`) on a `thread` stud | the nut fastener; `fasteners.clamps` lists the clamped stack |
| shaft/bore | `revolute_fit` (revolute/slider), `drive_flat`, `press_fit` (interference), `spline` (`teeth`); a clearance `fasten` slip fit (spacer tube) derives nothing |
| ball, header/socket, adhesive pads, strap seat/slot | `ball_joint`, `connector_retention`, `adhesive`, `strap` (the join naming `verify.strap`) |

`verify` on a join holds the exact-geometry limits copied into the derived
interface (radial clearance, interference, engagement, retainers, thrust parts);
`verify.kind` overrides the derived kind, e.g. a gearbox output that rotates with
the axle (`kind: drive_flat, rotating_bore: true` on a revolute join) or a bonded
magnet (`kind: adhesive`). `damping`/`friction` on a moving join go to its
derived joint. Authored `mechanical.interfaces` may give `frame: <component>` to
write their axis in that object's frame (e.g. the encoder's magnetic target).

**Mirrored instances.** `params.mirror: [1, -1, 1]` mirrors a type's interfaces
(origin, z, x mirrored; the frame stays right-handed, so hole and pin y
coordinates change sign) together with its geometry.

**Printed parts.** Holes and bores in PETG/PA12 parts use the process table in
`r3_catalog/data/print_fits.yaml` (FDM, SLS: hole-to-screw clearance, sliding
clearance, press interference, cap clearance) instead of ISO 273/286. All values
are labeled estimates.

**Interference.** The assembly stage intersects the exact CAD part plans
(catalog solids, datasheet features, the holes and bores interfaces cut, every
derived fastener) pairwise at the compiled poses. Threads, press fits and
splines are the only allowed overlaps. Seats are probed on the exact solids.

## 6. Onshape (phase B)

`r3_onshape.organized` builds the document from the joins:

- Each object type becomes a Part Studio modeled at the object's own origin: standard
  features on standard planes only (start-offset extrudes, face-started holes, Mirror for
  right-hand parts), fully defined sketches, named features in feature folders, and
  `mc_<interface>` mate connectors (`mc_<interface>_h<n>` at each clearance hole's far face).
  Purchased parts and fasteners live in the versioned `R3 Parts Library` document.
- Subassemblies (`cad.subassemblies` in robot.yaml) and the top-level assembly are mated on
  those connectors. Each mate reproduces the compiled `M = Rz(clock)·Tz(offset)·Rx(π)`:
  primary axes flipped, rotation −clock, offset −offset. Mates cover every tree join, the
  ball-joint loop closures, and one fastened mate per fastener.
- R3 reads the solved occurrence transforms back and requires them to equal the compiled
  poses (`assembly_pose_mismatch`). The fit checks then run on the Onshape geometry at
  those transforms.
