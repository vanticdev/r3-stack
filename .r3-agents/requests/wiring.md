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
