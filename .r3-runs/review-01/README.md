# Review packet 01: R3 RC car, CAD attempt log (other domains in progress)

Project: R3, "Lovable for full-stack robotics". Full feature spec:
`/Users/godbrigero/.t3/userdata/attachments/cce0127b-4068-4f34-96f6-96da7bef4a52-7ee0cde1-df60-43bc-819d-25ce6d87d30c-txt.txt`.

## What was done
- A unified robot spec (`examples/robots/rc-car/robot.yaml`) and component catalog
  (`packages/component-catalog/r3_catalog/data/`) with provenance.
- A derived connectivity graph (`packages/robot-model/r3_model/graph.py`). All six
  software bindings (PWM, UART, I2C, GPIO) are electrically verified to reach their
  devices through the harnesses and the HAT.
- CAD (`integrations/onshape/r3_onshape/`): FeatureScript is generated from
  robot.yaml. A new Onshape document is built from scratch in folder
  r3-stack-testing. Its part envelopes, masses and exact B-rep contacts are then
  verified, and failed attempts are deleted.
- Attempt log:
  - 1: compiled and regenerated, but the STL export failed (bridge redirect). Deleted.
  - 2: the bridge frame dropped mid-run. Deleted.
  - 3: tessellation parse bug. Deleted.
  - debug: format inspection only. Deleted.
  - 4: envelope mismatches (Pi ports +2 mm, HAT connectors overhanging). Deleted.
  - 5: passed the envelope check. The new exact interference check then found
    standoffs through the ESC and motor, knuckles intersecting the wheels, and
    floating wheels/Pi/HAT/encoder. Deleted; see `onshape_attempt5_before_fixes.png`.
  - 6: PASS. 28 parts, no interference, everything physically connected. Total
    mass 1.569 kg. Two tight-clearance warnings. See `onshape_attempt6_isometric.png`.
- Live Onshape document (attempt 6, public because the account is on the Free plan):
  https://cad.onshape.com/documents/314246dec6d07475f0ca7c44/w/57e0e065fe043f19dde9637b/e/9d77b679d1c916902ea71d0e
- In progress, built by parallel agents on their own branches: electrical twin
  (MNA + ngspice validation), KiCad carrier HAT, 3D wiring router + slack physics,
  MuJoCo co-sim + radio + register-level sensors + lockstep virtual HAL,
  firmware + BLITZ deploy, and the orchestrator CLI/MCP/GitHub.

## Files
- `docs/architecture/r3-pipeline.md`: architecture and contracts.
- `cad_model.json`: imported per-part data. `attempt_*.json`: per-attempt diagnostics.
- `examples/robots/rc-car/cad/rc_car.fs`: generated CAD source.
