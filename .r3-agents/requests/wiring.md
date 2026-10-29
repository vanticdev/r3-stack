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
