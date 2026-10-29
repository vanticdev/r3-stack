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
