# Finish line: the RC car is "done" when ALL of these hold on `r3/integration`

1. **Release gate** (`r3 status --release -C examples/robots/rc-car`, `r3_orchestrator/release.py`):
   `fabrication_ready: true`. Every requirement and scenario expectation is `verified` with current
   (non-stale) evidence. An `estimated` input is acceptable only when it is labelled, its source is
   recorded, and the requirement passes with ≥ 20 % margin. Otherwise replace the estimate with
   datasheet data.
2. **Every stage reports 0 errors:** assembly, model, cad (including the organization check), pcb,
   wiring (every harness `valid`, manufacturing package RELEASED), connectors, electrical, software, sim.
3. **Every scenario passes with the REAL firmware:** `autonomous_course`, `full_throttle`, `range_failsafe`,
   `low_battery`, `steering_sweep`, `top_speed_failsafe`, `pi_hang`.
4. **Tests and UI:** `pytest tests -p no:randomly` passes in under 20 min. The web UI's Overview shows
   "Ready for fabrication".
5. **Honesty rule:** never weaken a check, threshold or requirement to make it pass. You may change a
   requirement only when physics makes it impossible; then do it explicitly in robot.yaml, with a
   comment giving the physics and the twin's evidence, and report it. Fix the DESIGN, not the checks.

## Work split (each agent has its own branch from r3/integration; stay in your lane to keep merges clean)
- **A, mechanical layout + wiring.** Owns:
  - robot.yaml `assembly:`, component types and route hints (`route:`);
  - the catalog's mechanical/interfaces/features/model fields;
  - `integrations/onshape`, `packages/wiring` and `packages/connectors`.
- **B, electrical + PCB + failsafe.** Owns:
  - robot.yaml harness `wires:` (gauges, fusing), `requirements` (electrical) and the HAT/ESC/servo/battery
    electrical catalog fields;
  - `integrations/kicad` and `examples/robots/rc-car/pcb`;
  - `packages/simulation/r3_sim/electrical`, the ESC/arbiter models, and firmware motor and failsafe behaviour.
- **C, navigation, sensing, sim, infrastructure.** Owns:
  - GPS/RTK catalog entries plus the base station;
  - the radio and RTCM link, the sim kernel/evaluation/scenarios, and firmware estimation/navigation;
  - the wiring CAD-mesh audit tolerance (`packages/wiring/r3_wiring/geometry_audit*` or the equivalent);
  - `source:` slots, test infrastructure and performance.
- **If you need a change in another lane,** write it to `.r3-agents/requests/<lane>.md` and implement the
  minimal version yourself only if it's blocking.
- **Before every pytest run,** regenerate the artifacts with
  `python -m r3_orchestrator validate -C examples/robots/rc-car --stage <...>`.

Onshape note: the per-user quota on the Part Studio features endpoint was exhausted around 2026-10-29
10:00 local, with about 11 h to reset. Probe it with one cheap call before any build. Thanks to the
puzzle-piece model, objects are rigid Part Studios, so pose-only changes need NO Onshape rebuild:
recompose cad_model.json from the per-object meshes and the compiled poses. Only changed part geometry
needs rebuilding.
