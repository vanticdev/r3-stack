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

## Final acceptance test (user requirement, runs LAST after lanes A–D merge)
A completely independent Claude Opus 5.5 agent, using the default system prompt and given none of these
internal briefs, builds an autonomous RC car from scratch ("literally nothing") with R3, as a user would
(CLI / MCP / web / docs). Constraints from the user:
- Do NOT create a GitHub repository or link.
- Use as many off-the-shelf parts as possible.
- Everything structural (frame, mounts, brackets, body) is 3D printed.
- No custom gears or gearbox: the drivetrain (motor, gearbox/differential, axles, wheels) is bought
  off the shelf.
- The user already owns a **Raspberry Pi 5 4 GB** (confirmed by the user), an MPU-6050 IMU and an **Arducam UC-717** camera. Everything else is bought: GPS module, wheels,
  motor(s), ESC/drivers, battery, servo, and so on.
- The Arducam UC-717 faces forward. No LiDAR.
- It must drive autonomously in the twin.
The run passes when that agent's project meets the same finish line (sections 1–4 above), with only
the project's own files authored. Its transcript, friction points and failures become the next
improvement round.

## Lane D: from-scratch readiness, so the acceptance test is possible
Camera support (HAL + twin), off-the-shelf drivetrain catalog, FDM 3D-printing as a manufacturing
process, a blank-project flow, and agent-facing docs. See the lane D task.
