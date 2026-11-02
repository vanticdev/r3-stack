# Common brief for every R3 worker agent

You are one of six parallel worker agents building R3: "Lovable for full-stack
robotics". The goal is an AI-driven pipeline in which a robot is described once in
`robot.yaml`. Native CAD is authored in Onshape and pulled read-only; from the local description the pipeline builds PCBs (KiCad), 3D-routed
wiring with physically simulated slack, and software deployed through BLITZ. All of
it runs together in a multi-domain digital twin: physics, electrical, wiring, radio,
sensors, and the real robot software against virtual hardware. The twin emits
structured diagnostics, so an agent can iterate to a robot that works on its first
physical build. Fidelity matters more than speed; compute
cost does not matter.

Read first, in this order:
1. `docs/architecture/r3-pipeline.md`: stage contract, JSON hand-off formats, co-sim order, interfaces.
2. `packages/robot-model/r3_model/` (spec.py, graph.py, diagnostics.py, project.py, units.py).
3. `packages/component-catalog/r3_catalog/` (schema.py, data/*.yaml, data/components/*.yaml).
4. The current project’s `robot.yaml`, if one has been created.
5. `packages/hardware-interface/PROTOCOL.md` and `r3_hal/`, if your area touches software or the twin.

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
  If a project genuinely violates a limit, emit the diagnostic and report it
  in your summary. The design will be fixed, not the check.
- Annotate types, keep functions focused, and document non-obvious physics with short
  comments that include the equation or source.
- Also produce visual evidence where your area has any: PNG plots or renders under
  `build/<stage>/`. A separate panel of reviewer models will judge the project from
  screenshots and logs.
- Final report, kept short: branch name, commit SHAs, what works (with test output
  and key numbers), what is approximated, any diagnostics found against the project, contract-change requests, and the exact commands to run your stage on
  the current project.

## CAD policy (user directive): NO FeatureScript, ever
CAD is built only with Onshape's standard, user-editable features: sketches,
extrude, revolve, hole, fillet, chamfer, patterns, mirror, boolean, mate
connectors, and Assemblies with standard mates. This is how a human builds it in
the Onshape UI. Feature Studios, custom features, FeatureScript evaluation and any
generated `.fs` source are forbidden. `integrations/onshape/r3_onshape/policy.py`
enforces this in the R3 client, `tests/test_cad_policy.py` tests it, and
`.claude/settings.json` denies the onshape-native `evaluate` tool. Do not work
around it. The exact-export path verifies pinned STEP with OCP and native exported meshes.
For a working prototype, the build guide also documents `r3_cad_preview` and
`r3_cad_pull(mesh_source="viewer")`: unchanged native OBJ bodies, placements and
mass properties support rendering and real prototype physics without STEP.
Unsupported analytic qualification remains explicitly pending; numeric checks
are not weakened. Browser-session REST reads supply native metadata, never locally
reconstructed geometry. Only unchanged geometry files retrieved from Onshape count as the model. R3 never generates
parts or assemblies from YAML. Native connector frames, mates and poses are pulled evidence.

## Tool-use policy (user directive): use every tool the way a human engineer would
For every external tool, the AI works through the same operations a person uses
in that tool's UI. These are the tool's own editors, libraries, checkers and
routers, reached through its official automation API (or, if none exists, through
UI-level automation). It does not reverse-engineer or hand-write the tool's
internal file formats, invent a scripting layer the user would never touch, or
reimplement a capability the tool already has.
- **Onshape:** standard features (sketch, extrude, revolve, hole, fillet, pattern,
  mirror, boolean, mate connector) and Assemblies with standard mates. No
  FeatureScript (see the CAD policy above).
- **KiCad (headless, no window ever; user directive):** use KiCad's own engine with
  no GUI: the `pcbnew` Python module bundled with KiCad (SWIG; on macOS
  `~/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3`)
  for board editing, `kicad-cli` for ERC/DRC/netlist/exports, official KiCad symbol
  and footprint libraries, and Freerouting in CLI mode. Never launch the KiCad, Eeschema
  or Pcbnew apps, use the IPC API (it needs the GUI), or use UI automation. KiCad 10 has
  no headless schematic-editing API, so schematics are written with a structured
  KiCad-format library using official library symbols, then normalized by
  `kicad-cli sch upgrade` and validated by `kicad-cli sch erc`. Never write a custom
  router or DRC.
- **GitHub:** the `gh` CLI and normal git.
- **BLITZ / Docker / MuJoCo / ngspice:** their normal public interfaces.
- **R3's own wiring editor** is the exception: no external tool covers it. Its
  authored data is `robot.yaml`, which humans also edit.
Independent verification is still encouraged. R3 may re-check results from the
tool's own data, such as measured geometry or exported netlists, but the design
itself must be produced inside the tool.

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
- **Onshape:** author standard features, connectors and assembly mates through the
  onshape-native browser-session `/api` tools; use native editor tools only when supported.
  Pull through `/native/api/v17` exports. Capture actual viewport images. This authoring/pull step
  needs the signed-in native browser/companion; headless stages consume its pinned copy.
  Never use API keys or an expired saved session. On a real rejection, inspect and use a
  supported alternative; on HTTP 429 respect Retry-After without polling the throttled API.
- **KiCad:** `kicad-cli` plus the bundled `pcbnew` Python module. Never a GUI, not even under Xvfb.
- **Freerouting:** CLI mode.
- **Docker:** used for firmware.
The supported server environment is the R3 worker Docker image. Any GUI-only step
must document its headless path.

## Purchased-part model policy (user directive): source real models first
A purchased component (Pi, motor, ESC, servo, battery, sensors, connectors, wheels, gearbox, …) must use a
real downloaded 3D model whenever available. Source it in this order:
1. **Onshape public documents:** inserted from the public document version, the way a human uses
   Insert → Other documents → Public.
2. **The manufacturer's official STEP:** Raspberry Pi, Arducam, Traxxas, u-blox, Pololu, …
3. **Open model databases with programmatic or public access:** KiCad 3D libraries, TraceParts,
   3D ContentCentral, SnapEDA / Ultra Librarian. Respect terms and robots.txt, and never scrape behind
   logins.
4. **Recorded search miss:** only after a real search finds no downloadable exact model,
   the project agent may model a simple datasheet-dimension proxy in Onshape using standard
   features. Label it PROXY, keep its provenance estimated, and record the search and datasheet
   with `params.cad_proxy` as described in the build guide. This is the only exception to the
   prohibition on sculpting purchased parts. Continue with estimated items, keep them visible
   in release status until measured or replaced, and list them at the end. Never edit or fake a pull.

Every sourced model is:
- validated against the datasheet (bounding box and key features);
- given the datasheet mass (mass override);
- given interface frames derived from its real holes and faces;
- recorded with URL, licence and checksum.

## Generality policy (user directive, 2026-10-30): every feature works for any robot
Every feature, check, stage, simulation layer, data format and UI you add must work for any robot R3 can
describe: arms, drones, legged robots, boats and stationary rigs, including stationary rigs and mobile mechanisms.
- Do not put robot-, project- or component-specific ids, names, dimensions or special cases in package
  code (`packages/`, `services/`, `integrations/`, `apps/`). Derive everything from robot.yaml, the
  catalog and the build artifacts. Design-specific values belong in robot.yaml or the catalog.
- Do not assume vehicle concepts (wheels, steering, chassis, top speed) in generic code. Gate them on what
  the spec declares, and give non-vehicle robots a sensible path.
- Test new behaviour on synthetic, robot-agnostic fixtures, under `tests/fixtures/`.
- If something can only work for one robot type, say so explicitly in the code and the docs, and emit a
  diagnostic when it is skipped. Never fail silently.
