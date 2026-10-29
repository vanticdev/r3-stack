# Common brief for every R3 worker agent

You are one of six parallel worker agents building R3: "Lovable for full-stack
robotics". The goal is an AI-driven pipeline in which a robot is described once in
`robot.yaml`. From it the pipeline generates CAD (Onshape), PCBs (KiCad), 3D-routed
wiring with physically simulated slack, and software deployed through BLITZ. All of
it runs together in a multi-domain digital twin: physics, electrical, wiring, radio,
sensors, and the real robot software against virtual hardware. The twin emits
structured diagnostics, so an agent can iterate to a robot that works on its first
physical build. The first reference robot is an autonomous 1/10 RC car
(`examples/robots/rc-car/robot.yaml`). Fidelity matters more than speed; compute
cost does not matter.

Read first, in this order:
1. `docs/architecture/r3-pipeline.md`: stage contract, JSON hand-off formats, co-sim order, interfaces.
2. `packages/robot-model/r3_model/` (spec.py, graph.py, diagnostics.py, project.py, units.py).
3. `packages/component-catalog/r3_catalog/` (schema.py, data/*.yaml, data/components/rc_car.yaml).
4. `examples/robots/rc-car/robot.yaml`.
5. `packages/hardware-interface/PROTOCOL.md` and `r3_hal/`, if your area touches software or the twin.
6. The full product spec: `/Users/godbrigero/.t3/userdata/attachments/cce0127b-4068-4f34-96f6-96da7bef4a52-7ee0cde1-df60-43bc-819d-25ce6d87d30c-txt.txt`.

Rules:
- You run in your own git worktree. First run `git checkout -b <your branch>`. Commit
  your work there with clear messages, several commits as you go. Do not push and
  do not merge into other branches. The coordinator merges all branches.
- Stay inside the folders you own (listed in your brief), plus new test files under
  `tests/` prefixed with your area name. Do not modify `r3_model`, `r3_catalog` or
  `robot.yaml`. If you need a contract change, add a short entry to
  `.r3-agents/requests/<area>.md` in your branch describing the change and why. Work
  around it locally, for example with an optional field read via `.get`, until the
  coordinator merges it. Small additive catalog *parameters* that your models need
  may go in that request file too.
- Python: use the shared interpreter `/Users/godbrigero/Documents/r3-stack/.venv/bin/python`
  (numpy, scipy, mujoco 3.x, pydantic 2, pyyaml, matplotlib, pillow, pytest, mcp are
  installed). Run tests from your worktree root with
  `/Users/godbrigero/Documents/r3-stack/.venv/bin/python -m pytest tests/<your tests>`.
  The `pythonpath` in `pyproject.toml` makes every `r3_*` package importable. If you
  need another pure-Python package, install it with
  `uv pip install --python /Users/godbrigero/Documents/r3-stack/.venv/bin/python <pkg>`
  and list it in your final report.
- Every stage follows the contract: `run(project) -> StageResult`, artifacts under
  `build/<stage>/`, stable diagnostic codes with numeric `data`, and explicit
  `assumptions`. Distinguish datasheet values from estimates.
- Physics must be real, not mocked. Use proper equations, real datasheet numbers
  where known, and estimates that are labeled as such. Do not fake passing results.
  If the RC-car design genuinely violates a limit, emit the diagnostic and report it
  in your summary. The design will be fixed, not the check.
- Annotate types, keep functions focused, and document non-obvious physics with short
  comments that include the equation or source.
- Also produce visual evidence where your area has any: PNG plots or renders under
  `build/<stage>/`. A separate panel of reviewer models will judge the project from
  screenshots and logs.
- Final report, kept short: branch name, commit SHAs, what works (with test output
  and key numbers), what is approximated, any diagnostics found against the RC-car
  design, contract-change requests, and the exact commands to run your stage on
  `examples/robots/rc-car`.

## CAD policy (user directive): NO FeatureScript, ever
CAD is built only with Onshape's standard, user-editable features: sketches,
extrude, revolve, hole, fillet, chamfer, patterns, mirror, boolean, mate
connectors, and Assemblies with standard mates. This is how a human builds it in
the Onshape UI. Feature Studios, custom features, FeatureScript evaluation and any
generated `.fs` source are forbidden. `integrations/onshape/r3_onshape/policy.py`
enforces this in the R3 client, `tests/test_cad_policy.py` tests it, and
`.claude/settings.json` denies the onshape-native `evaluate` tool. Do not work
around it. Verification uses native REST data instead: `bodydetails` face/edge
surface definitions, mass properties, bounding boxes, and tessellated meshes with
Python mesh booleans (manifold3d/trimesh are installed in the shared venv).

## Simulator policy (user directive): integrate the best existing simulators, never write your own
R3's job is to INTEGRATE the best existing domain simulators into one lockstep
co-simulation tool (`r3_sim`), plus the virtual-hardware glue. It must not
reimplement them.
- **Rigid-body physics:** MuJoCo.
- **Circuits/electronics:** ngspice, through its shared library (libngspice,
  `/opt/homebrew/lib/libngspice.dylib`), co-simulated in lockstep.
- **Wire/cable physics:** MuJoCo's built-in elastic cable (`mujoco.elasticity.cable`
  plugin / `composite type="cable"`) inside the same MuJoCo world as the robot.
- **RF propagation:** Sionna RT (ray tracing).
- **Firmware:** the real program, against the twin HAL.
R3-specific code is limited to the co-simulation master (clock, ordering, data
exchange), device/register models that bridge software to the physics, and the
3D harness router (no external tool exists for it).

## Headless policy (user directive): everything must run headless on a server
Every stage must run with no display and no interactive browser.
- **MuJoCo rendering:** `MUJOCO_GL=egl` (or osmesa) on Linux.
- **Onshape:** the official API-key transport. Images come from the REST shaded-view
  endpoints, never browser captures. The browser bridge is a local-dev convenience only.
- **KiCad:** `kicad-cli`. If a step needs the KiCad app (IPC API), run it under Xvfb.
- **Freerouting:** CLI mode.
- **Docker:** used for firmware.
The supported server environment is the R3 worker Docker image. Any GUI-only step
must document its headless path.
