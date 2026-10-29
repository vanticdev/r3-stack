# Web area (`apps/web/`): contract requests

The web app is built against `docs/architecture/api.md` and checked against a live `r3 serve`.
Every page loads without console errors. The requests below are additive, and the UI already
works around their absence.

1. **Body rest poses in playback frames (api, `projects._body_frames`).** To place a robot-frame
   CAD mesh on a moving body, the client needs the body's rest pose. Today it derives the pose
   from frame 0, as the body's pose relative to the chassis, assuming the scenario starts at rest
   with zero joint angles. That is wrong for scenarios that start mid-motion. Please add
   `frames.rest: {body: [x, y, z, qw, qx, qy, qz]}` from `mj_kinematics` at `model.qpos0`. The
   dev converter (`apps/web/scripts/convert_recording.py`) already emits it, and the UI prefers
   it when present.
2. **Cable span diameter and harness in `frames.cables` (api/sim).** This complements
   `.r3-agents/requests/api.md` item 2. With `od` (and `harness`/`wires`) per span, the
   playback tubes could use the real bundle diameter instead of a fixed 2.8 mm.
3. **Worker image builds the web app (docker).** `r3 serve` serves `apps/web/dist`. The worker
   image should run `cd apps/web && npm ci && npm run build` (Node 24 is already installed).
   Playwright browsers are only needed for `npm run test:e2e`
   (`npx playwright install --with-deps chromium`).
4. **Hash routing.** The app uses `#/p/<pid>/...` routes. `r3 serve`'s SPA fallback is therefore
   not required, but it is harmless.
