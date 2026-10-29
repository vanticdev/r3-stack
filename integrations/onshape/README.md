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

## Pipeline (`stage.run`, phase B: `organized.py`)

The robot is compiled from objects joined at interfaces (`r3_model.assembly`). Its fasteners,
fit interfaces and joints are derived from those joins (`r3_model.mechanics`). The CAD is then
built the way an engineer organizes Onshape:

1. `parts.py`: one local part per geometry variant. Every object type is modeled at its own
   origin (catalog frame); right-hand parts mirror left ones. Fabricated parts cut their holes,
   bores, sockets and slots from their interfaces; purchased parts keep only catalog solids and
   datasheet features. Each part gets `mc_<interface>` mate connectors, plus far-face seats
   `mc_<interface>_h<n>` for screw heads and nuts. Every plain fastener and nut is a part.
2. `studio.py`: one Part Studio per object type or family, using standard features on the
   Top/Front/Right planes only (start-offset extrudes, face-started Hole features, Mirror).
   Sketches are fully dimensioned (`WELL_DEFINED`), features are named and grouped into the
   feature-folder passes *Base shape / Bores & shafts / Cutouts / Mounting holes / Mirror /
   Mate connectors*, and key dimensions reference the Variable Studio (`#plate_thickness`,
   `#wheelbase`, ...). Part properties are set: name, appearance, material, mass override,
   part number, vendor.
3. `organized.py`:
   * the `R3 Parts Library` document (purchased parts and fasteners, versioned, reused while
     its digest is unchanged);
   * the robot document: `01 Variables`, `02 Fabricated parts`, `04 Subassemblies`,
     `05 Top-level assembly`;
   * subassemblies from `cad.subassemblies`, mated on interface connectors. Each mate
     reproduces the compiler's `Rz(clock)·Tz(offset)·Rx(π)`, and the solved transforms must
     equal the compiled poses;
   * organization through the onshape-native bridge when it is connected (`bridge.py`): feature,
     tab and assembly folders, hidden reference geometry;
   * the organization check (`cad_default_feature_name`, `cad_feature_not_in_folder`,
     `cad_tab_not_in_folder`, `cad_part_name_mismatch`, `cad_missing_mate_connector`,
     `cad_unconstrained_sketch`, `cad_assembly_unfoldered`, `cad_missing_variable_reference`,
     `cad_visible_construction_geometry`, `cad_excess_construction_planes`), REST first;
   * exact-geometry fit checks (`fit.py`) on the Part Studio geometry at the solved transforms,
     plus the steering motion sweep and calibration (`motion.py`);
   * meshes, mass properties, headless shaded views of every Part Studio, subassembly and the
     top level, and `cad_model.json`. The document is pinned in `r3.lock.yaml` (`cad`,
     `cad_library`), and the superseded document is trashed.

Negative tests: `python -m r3_onshape.negative_organized examples/robots/rc-car` runs a
misaligned hole pattern, a default-named feature, an unfoldered tab and an unconstrained
sketch, and writes `build/cad/negative_tests.json`. The legacy single-Part-Studio builder
(`direct.py`, `sync.py`, `plan.py`, `negative.py`) is kept for robot-frame specs without an
`assembly:` section.

## Headless use

On servers and in CI, use the official API keys: `ONSHAPE_ACCESS_KEY` /
`ONSHAPE_SECRET_KEY`, optionally `ONSHAPE_BASE_URL` and
`R3_ONSHAPE_TRANSPORT=api-keys`. Requests are HMAC-SHA256 signed
(`r3_onshape.client.ApiKeyTransport`); basic auth is available with
`ONSHAPE_API_AUTH=basic`. CAD images come from the REST shaded-view endpoints
(`r3_onshape.views`) as `build/cad/views/{iso,front,top,right}.png`, never from
a browser. The `onshape-native` bridge is a local-development convenience only.
See [headless operation](../../docs/architecture/headless.md).
