# Wiring review packet

R3 is a full-stack robotics platform. The wiring subsystem routes every harness of an RC car in 3D,
simulates cable slack physically (MuJoCo elastic cables), and checks it electrically and physically.
The user says wiring "needs a HUGE IMPROVEMENT" and wants connectors displayed properly and the
Raspberry Pi pins rendered properly.

Look at:
- Code: `packages/wiring/r3_wiring/` (router.py, smoothing.py, obstacles.py, checks.py, mujoco_cables.py,
  cable_sim.py, terminations.py, render.py, stage.py) and the harness definitions in
  `examples/robots/rc-car/robot.yaml` (`electrical.harnesses`). Also look at the connector and wire
  catalog in `packages/component-catalog/r3_catalog/data/` (`connectors.yaml`, `wires.yaml`,
  `components/rc_car.yaml`).
- Outputs in this folder: `routes.json`, `result.json` (diagnostics), `harness_bom.csv`, renders `*.png`,
  and screenshots of the web UI's wiring and 3D pages. Note: the UI screenshots were taken against a
  partly stale CAD build.
- UI code: `apps/web/src/pages/WiringPage.tsx` and `apps/web/src/viewer/` on branch `r3/web`
  (worktree `/Users/godbrigero/.t3/worktrees/r3-stack/t3-46ba1818`).
