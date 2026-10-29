# Requests from lane A (mechanical layout + wiring) to lane C (sim, infrastructure, mesh audit)

1. **`cad_mesh_invalid` (39) blocks every harness release.** These are the Onshape tessellation seams and the
   2.3 % chord-error volume deficit of queue item 25, and the audit tolerance is lane C's. Parts rebuilt
   locally from their plans (`source.recomposed.pending_rebuild` in cad_model.json) come from exact
   manifold3d booleans and are closed by construction.
2. **Offline CAD recomposition.** `python -m r3_onshape.recompose examples/robots/rc-car` rewrites
   cad_model.json and the meshes from the recorded Onshape Part Studio geometry at the compiled poses. It needs
   `build/cad/fixtures/onshape_local_plans.json.gz`, which organized builds now write. Stages that copy
   `build/cad` should copy the fixtures too.
3. **Stage order.** `model` checks interference on CAD only when cad_model.json matches the current digest.
   After a robot.yaml change, run `cad` (or the recompose) before `model`. Otherwise `model` falls back to
   envelope boxes and reports about 76 `component_interference` errors.
