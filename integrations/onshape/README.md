# Onshape integration (`r3_onshape`)

Builds the robot in Onshape directly from `robot.yaml`, the way a person does in the
Onshape UI: sketches on default/offset planes, extrude, revolve, Hole (ISO tapped /
clearance) and an Assembly with fastened, revolute and ball mates. No FeatureScript in
any form: `policy.py` rejects Feature Studios, `/featurescript` evaluation, custom
feature namespaces and non-standard feature types before a request leaves the process
(`tests/test_cad_policy.py`).

## Headless session (no browser, no extension)

`session.py` talks to Onshape's REST API with a logged-in web session from a plain
Python process: `POST /api/users/session` creates the session; requests carry the
session cookies and, for writes, the `X-XSRF-TOKEN` header, exactly like the Onshape
web client. Cookies are persisted to `~/.config/r3/onshape-session.json` (mode 0600)
and refreshed on every response.

```sh
# once per machine (password read from a file, never from the command line)
ONSHAPE_EMAIL=you@example.com python -m r3_onshape.session login --password-file /path/to/secret
python -m r3_onshape.session status
```

`default_transport()` prefers API keys (`ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`) when
configured, then this session, then the onshape-native browser bridge;
`R3_ONSHAPE_TRANSPORT=session` forces the session. `describe_transport()` reports the same
order without network calls (a stored session is reported, not verified). Screenshots are rendered by
Onshape's `shadedviews` endpoint and steering poses are set through `matevalues`, so the
whole pipeline (build, verify, assemble, render) runs on a server.

## Pipeline (`stage.run`)

1. `plan.py`: spec + catalog -> per-part plans. Purchased parts are their catalog
   envelope (`mechanical.solids`) plus only their datasheet `features`; fabricated
   parts get clearance/tapped holes from the fasteners that use them, bores from the
   interfaces whose bore side they are, and strap slots. Every fastener, nut, strap and
   magnet is its own part.
2. `direct.py`: plans -> standard features in a new document (failed attempts are
   deleted); names, appearance, material density (fabricated) or catalog mass override.
3. `model.py`: `bodydetails` (analytic faces) + fine tessellations (chord 0.05 mm,
   angle 2 deg), recorded to `build/cad/fixtures/` for offline tests.
4. `fit.py`: fasteners (head seating, clamp-face contact, ISO 273 hole presence, edge
   distance, thread engagement from the screw/tapped-hole intersection volume, nut
   engagement and protrusion, tool access), interfaces (revolute/press/spline fits,
   D-flats, ball joints, adhesive area, straps, magnet air gap and field, header
   retention), global interference, joint axes, transmission paths, retention graph of
   verified attachments only.
5. `motion.py` + `kinematics.py`: steering linkage solved from the verified axes and
   ball centers; table written to `cad_model.json` (`kinematics.steering`); motion
   sweep (11 servo samples, 0.5 mm margin) and wheel-spin sweeps.
6. `assembly.py`: Assembly from verified interfaces; connectors corrected to coincide in
   the modeled pose; every joint checked against its mate (type, axis, origin, limits).
7. Headless renders and `cad_model.json` (+ microversion and mesh hashes pinned in
   `r3.lock.yaml`).

Negative tests (deliberately broken designs): `python -m r3_onshape.negative
examples/robots/rc-car` -> `build/cad/negative_tests.json`.

See [repository layout](../../docs/architecture/repository-layout.md).

## Headless use

On servers and in CI, use the official API keys: `ONSHAPE_ACCESS_KEY` /
`ONSHAPE_SECRET_KEY`, optionally `ONSHAPE_BASE_URL` and
`R3_ONSHAPE_TRANSPORT=api-keys`. Requests are HMAC-SHA256 signed
(`r3_onshape.client.ApiKeyTransport`); basic auth is available with
`ONSHAPE_API_AUTH=basic`. CAD images come from the REST shaded-view endpoints
(`r3_onshape.views`) as `build/cad/views/{iso,front,top,right}.png`, never from
a browser. The `onshape-native` bridge is a local-development convenience only.
See [headless operation](../../docs/architecture/headless.md).
