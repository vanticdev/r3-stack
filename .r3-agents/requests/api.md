# API area (`r3 serve`, `r3_orchestrator/api/`): contract requests

1. **Persist the wiring slack-sim timeline (wiring).** `r3_wiring.stage` plots
   `drive.timeline` (`slack_clearance_timeline.png`) but writes only per-wire minima
   to `build/wiring/slack_sim.json`. The web app's slack/vibration playback needs the
   samples. Please add an additive `timeline` key to `slack_sim.json`, e.g.
   `{"t": [...], "min_clearance": {"<wire>": [...]}, "chassis": {"pos": [...], "quat": [...]}}`,
   or write `build/wiring/slack_timeline.json`. `GET /api/projects/{pid}/wiring` already
   passes either through as `slack_timeline` (today it is listed in `missing`).
2. **Map cable composites to wires (sim).** The scenario `vehicle.xml` names cable bodies
   `cableNNsM_B_*`. The API returns them as `frames.cables.cableNNsM` with their color.
   A `cables.json` next to `vehicle.xml` (`{"cable00s0": {"harness": ..., "wires": [...]}}`)
   would let the 3D view label and highlight harnesses during playback.
3. **Spec patches keep line wrapping (orchestrator, existing behavior).** `spec_ops.patch_spec`
   re-emits wrapped flow sequences (`outline`, `holes`) on one line, so a small UI or agent
   edit produces a large diff. Comments are preserved. Consider keeping flow-style
   sequences of unchanged nodes as they were written.
