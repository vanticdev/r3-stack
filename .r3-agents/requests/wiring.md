# Wiring area: contract / catalog requests

1. **Cylinder frame convention.** `rc_car.yaml` says cylinders are centred at
   mid-length, but `standoff-m3-47` at z = 28 mm only spans plate-to-deck
   (28-75 mm) if a z-axis cylinder starts at its mounting face. `r3_wiring`
   currently uses: boxes and z-axis cylinders sit on their bottom face; x/y-axis
   cylinders (wheels, motor) are centred. Please state this in the catalog header
   (or move the standoff poses to z = 51.5 mm) so CAD and wiring agree.
2. **Custom outlines.** `chassis-plate-al6061` is `shape: custom` (front tapers
   to 70 mm) but has no outline, so routing uses its 340 x 130 mm bounding box.
   Suggest an optional `mechanical.outline: [[x, y], ...]` (component frame) for
   plates; wiring will extrude it when present.
3. **Connector parameters (additive, optional).** `mass` and `termination_allowance`
   per connector type; wiring currently estimates mass from `body_size`
   (0.8 g/cm3 effective) and allowances per family (solder cup 5-6 mm, crimp 3 mm).
4. **routes.json extras (additive).** Downstream stages would benefit from optional
   per-wire `color`, `od`, `pins` (arc length, body) and per-harness `pass_throughs`;
   these are written today to `build/wiring/routing_detail.json` to keep
   `routes.json` exactly on contract.
5. **CableSimulator inputs.** Implemented as: `chassis.quat` is [w, x, y, z]
   (MuJoCo order); `lin_acc`, `ang_vel`, `ang_acc` are world-frame;
   `moving[<component>]` is the world pose of the component frame used by
   `RobotGraph.component_pose`. Please confirm this in `r3-pipeline.md`.

## Wiring overhaul (branch r3/wiring-overhaul)

6. **routes.json additive fields (now written).** `routes.json` keeps the contract
   keys and adds: top-level `release` (`status` released|unreleased, `blocking_codes`);
   per wire `valid`, `violations`, `color`, `od`, `cut` (nominal, tolerance, breakdown),
   `installed_ampacity` + `derating` (read by `r3_sim.electrical.network`), and
   `terminal_load`; per harness `valid`, `violations` and `supports`
   (`id`, `type`, `mpn`, `host`, `position`, `permitted_motion`). Please add them to
   `docs/architecture/r3-pipeline.md` as optional fields.
7. **Termination data (catalog request).** `r3_wiring/data/terminations.yaml` holds per
   cable-side connector type: housing/contact MPNs, strip length and tolerance, insertion
   depth, rework allowance, contact retention and crimp tool. These belong in
   `connectors.yaml` as optional `termination:` blocks (the `r3/connectors` branch adds
   geometry, not termination data). Values are estimates unless marked datasheet.
8. **Support hardware catalog.** `r3_wiring/data/supports.yaml` (tie mounts, screw mounts,
   P-clips, post ties, PLT1M tie). Could move into `r3_catalog` as a `supports.yaml`.
9. **Router hook (after the r3/connectors merge).** `r3_wiring.planner.plan_bundle` /
   `plan_routing` run after routing in `stage.route_and_check`; `router.py` is untouched.
   Once the connector fan-out lands, the router may call `plan_routing(project, self,
   result)` at the end of `route()` (one line); per-conductor contact frames can then
   become fixed junction axes in `planner._node_junction`.

## Planner + pin terminations (branch r3/planner-pins)

10. **Router hook done.** `HarnessRouter.route()` ends with `plan_routing(project, self, result)`
    (`RouterOptions.plan`, default on; the report is `RoutingResult.planning`), so router and
    planner are one pipeline; `stage.route_and_check` adds the supported second pass. Every
    conductor keeps its pin-level fan-out (pin exit, wire-entry tangent) up to a breakout shared by
    the node's conductors on that segment; the planner plans bundle cores from those breakouts as
    fixed junctions (`kind: fanout`, axis = fan-out arrival tangent). Floating pairs that carry
    fan-outs are no longer moved by the planner, and integral pigtails no longer rotate within
    `PIGTAIL_CONE` (their conductors leave the grommet along the authored axis). Bundle segments
    gain an internal `breakouts` entry (not written to `routes.json`). `routes.json` per wire
    carries `ends`/`fanout` and `valid`/`violations`/`installed_ampacity`/`cut`; please document
    `ends`, `fanout` with the other optional fields in `docs/architecture/r3-pipeline.md`.

## CAD mesh audit (branch r3/finish-c-meshaudit, lane C → lane A)

11. **`geometry.cluster_decimate` opens the routing mesh (please fix in lane A).** Its duplicate
    removal `np.unique(np.sort(tri, axis=1), ...)` ignores winding. When 1 mm clustering folds a
    thin wall onto itself, two coincident triangles of OPPOSITE winding are produced. They should
    cancel; instead one is kept, so the mesh is no longer a closed oriented 2-cycle and the
    generalized winding number (the clearance sign) is not exact next to it. The audit
    (`r3_wiring.geometry_audit.audit_routing_mesh`) reports this as `cad_mesh_invalid`
    (`representation: routing_mesh`) on servo (6 unbalanced edges), axle_rl/rr (3), enc_magnet_3/4 (9),
    pi_standoff_1..4 (12) and antenna_mast (6). The CAD exports of these parts are clean.
    Fix: resolve coincident triangles as a 2-chain, i.e. replace the unique step with
    `tri, _, _ = geometry_audit.remove_degenerate_and_duplicate_faces(tri)` (opposite windings
    cancel, one copy of the net winding is kept). Measured on the RC car, this leaves 0 unbalanced
    edges on all ten parts. Also, the docstring bound "the surface moves by at most cell*sqrt(3)/2"
    is wrong: a vertex moves to its cell's vertex mean, which can be up to one cell diagonal
    (cell*sqrt(3)) away. The audit uses cell*sqrt(3).

## From lane B (branch `r3/finish-b`): harness gauge changes that affect routing

Electrical/PCB fixes changed four harnesses' conductors (robot.yaml `wires:` only, `route:` untouched):

- `power_y`: `W_Y_P_HAT` and `W_Y_N_HAT` 18 -> 14 AWG (the 10 A fuse must not exceed the installed ampacity;
  18 AWG was 5.4 A installed, 14 AWG ~10.3 A); `W_Y_P_FUSE` 18 -> 12 AWG (no thinner conductor upstream of the fuse).
  The HAT pair's minimum bend radius rises to 14.8 mm (2-conductor 6.6 mm bundle).
- `esc_signal`: a third conductor `W_ESC_BEC` (A.2 -> B.2, 22 AWG) is back: it powers the ESC side of the HAT's new
  digital isolator; `W_ESC_GND` is now the isolator's ESC-side ground (net still GND through the ESC).
- `servo_lead`: `W_SRV_VCC`/`W_SRV_GND` 22 -> 20 AWG; cable-side housing `servo-3-female-hd` (new connector type,
  same geometry as `servo-3-female`, 20-24 AWG crimps; catalog + connector_geometry entries added).
- `esc_motor` stays 14 AWG.

Request: re-plan these routes for the larger bend radii; in my local wiring run several of the existing
`route_infeasible`/`wire_bend_radius_violation` findings on `power_y` and `servo_lead` became tighter.

## A3 finish blocker: branch separation is absent from the planning problem

The collision checks remain unchanged. `router._route_harness` deliberately
shares used voxels, then `planner.Junction` gives every segment incident at a
bundle branch one shared axis. `plan_bundle` constrains clearance from static
geometry, cable hardware and previously planned harness groups, but has no
separation constraint between different branches within its current group.
`checks._wire_clearance` subsequently detects the overlap correctly.

A numerical lower-bound reproduction explains why moving the layout can merely
move this failure. Two 12 AWG, 4 mm OD branches leave a shared centre with a
shared tangent and minimum centreline radius 16 mm. At 6 mm arc distance, even
opposite maximum-curvature turns separate their centres by only
`2 * 16 * (1 - cos(6 / 16)) = 2.2237561 mm`. Both points are
`2 * 16 * sin(6 / 32) = 5.9649055 mm` from the shared-core endpoint, outside
the current local tube (`2 + 2 + 1 = 5 mm`). There, the unchanged 0.5 mm penetration threshold
requires centre separation at least `2 + 2 - 0.5 = 3.5 mm`. That ideal split still
penetrates by 1.7762439 mm. Increasing bend radius reduces separation further.
This is not room that a moved support can create within the shared-centre branch
representation. It needs physical conductor lay, or independently planned branch
lanes with the correct finite-width fan-out. Do not increase the exemption tube
or collision tolerance.

Measured example: the best bundled 18 AWG motor candidate had only one error,
`wire_intersects_wire`, 1.725649 mm at (66.1429, 32.0853, 37.9770) mm. A3 avoided
that fork by splitting the two motor runs into independently clocked pigtails;
both isolated runs then passed (58.11 s). Power-Y still has several incidental
shared forks and fixed multi-pin housings, so it needs the general implementation.
The expanded over-deck power trial was rejected: 47 errors in 775.85 s, including
21 planning constraints, 15 bends and an inter-wire overlap. It is not committed
as a design. Existing check coverage must remain intact.

A separate limitation affects the camera: `camera_fpc` currently represents its
seated flexible ends and end stiffener only. There is no qualified installed
ribbon path/bend/support simulation. `flat_cable_route_estimated` must continue
to block fabrication release; endpoint reach is not clearance proof. The owner's
UC-717 mounting pattern also remains unconfirmed (4 x 2.5 mm, 30 x 30 mm).

A later vertical-XT90 trial was also rejected rather than promoted. With the
pair axis constrained vertically and its hint moved to (240,25,100) mm, the
three-harness power subset finished in 818.44 s with 24 errors: 11 planning,
3 unsupported spans, 1 connector clearance, 1 inter-wire overlap and 8 bends.
The resulting XT90 body clears the tie rod by only 6.485279 mm versus 8 mm;
the power trunk has a 241.329 mm unsupported span versus 99.469 mm. The trial
removes the earlier wheel-sag symptom but does not make a buildable power loom.
