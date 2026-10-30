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

Negative tests: `python -m r3_onshape.negative_organized examples/robots/robot` runs a
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

## Quota planning and resume

Inspect the complete write plan without credentials, transport discovery or Onshape calls:

```sh
python -m r3_onshape.organized rebuild examples/robots/robot --dry-run
python -m r3_onshape.organized organize examples/robots/robot --dry-run
python -m r3_onshape.negative_organized examples/robots/robot --dry-run
```

`R3_CAD_DRY_RUN=1` exposes the rebuild plan through the normal `cad` stage and saves
`build/cad/rebuild_dry_run.json`. The plan lists Part Studios, named standard features,
assembly mates and estimated `/features` calls. Feature creation/renaming is one request
per feature in Onshape's official API; `quota.update_features` batches existing parameter
and suppression updates. Hole points and reference-geometry visibility are grouped already.
Editor organization reads are estimated separately from REST construction and verification.

Every HTTP 429 stops immediately, including reads and cleanup. The result reports
`onshape_quota_blocked`, the original `retry_after` and an absolute UTC `reset_at` when
Onshape supplies it. After the reset, rerun the same command (the actual rebuild command
is `python -m r3_onshape.organized rebuild <project>`). Successful construction requests
are journaled under `build/cad/`; replaying their responses locally preserves feature IDs,
parts and imports without sending those requests again. Organization checkpoints skip
completed folder/visibility tasks. Keep the lock and build journals together when resuming.
Changing inputs during an unfinished build stops with an explicit error; archive that
attempt before clearing its `cad_build_pending` / `cad_library_build` lock entry.

Catalog `extends` and `cad_library.aliases` share a CAD identity only when mechanical,
interface and model inputs agree. Library `part_signatures` include the solid and mate
frames. An unchanged inherited SKU can keep its existing immutable document/version/part
reference while other library families rebuild; an unknown or changed signature requires
a rebuild. `organize` always operates on the pinned documents and never rebuilds a library.

### Incremental rebuilds of pinned CAD

`python -m r3_onshape.organized rebuild <project>` updates the existing robot and
parts-library workspaces whenever `r3.lock.yaml` contains a CAD document. Use
`--full` to request a fresh robot document explicitly (the matching library can
still be reused). The stage also accepts `R3_CAD_FULL=1`.

`--dry-run` uses no authentication or transport. Its incremental report lists
pending studios, feature candidates, affected instances, mate additions/deletions,
and estimated `/features` calls including rollback operations and verification
reads. These are conservative estimates before the live diff, rather than a
promise that every candidate feature will be written. Solid parameter changes
batch; sketches with changed entities/constraints use the single-feature update
API. Unchanged features, connector IDs and unaffected assembly IDs survive.
The report also lists `import_blockers`: missing/unverified model-bank interface
evidence and incompatible placement frames. A successful dry run is a write plan,
not approval to bypass these checks. Native import rejects known prerequisites
before uploading; translated geometry still receives its normal independent
bounding-box validation.

The lock must match `build/cad/build_state.json`. Restore that pinned build if it
is missing; incremental mode fails closed rather than silently starting a full
rebuild. Legacy builds use `pending_rebuild`/recorded plan evidence; successful
incremental builds additionally pin complete part signatures. A pending Pi SKU
alias with a reviewed identical geometry signature does not rebuild its studio.

Every acknowledged operation is journaled. `build/cad/incremental_state.json`
checkpoints completed studios, the library version, and assembly construction.
After a 429, rerun the same command with the same inputs after the reported reset.
Completed studios and writes are skipped, including when verification was blocked.
Changing the inputs or target lock during an unfinished attempt is rejected.
Native geometry, bounds, volume and mass are compared with changed local plans;
the shared solved-pose, exact fit and motion checks still run. Library/CAD pins are
published only after successful verification. Existing documents are never
trashed by incremental mode, even after failure.

Vendor/model-bank parts are translated into separate Part Studios in the pinned
workspace using the same import, geometry/frame validation, connectors and mass
override as full builds. Verified public versions use direct insertion. Replaced
bodies are labelled `Retired` and removed from active part/connector lookup; an
empty source studio is also renamed `Retired`. Shared source studios keep their
unaffected siblings and editable history. Only affected instances and mates are
replaced. Uploads, translation polling and retirement writes are journaled too,
so resuming does not repeat an acknowledged import.

Mirrored native families update their standard Mirror features and retain body
and connector IDs when topology permits; changed body IDs trigger instance and
mate replacement. New subassembly levels use native assembly creation/insertion;
removed levels are emptied, detached and labelled `Retired`. Instances moving
between levels are reinserted only at their new level, with affected mates
recreated against the new occurrence paths. These operations share the assembly
journal and checkpoint, including resumes after quota failures.

Folder creation/movement remains an Onshape API limitation: the public REST API
can read tab folders but cannot create/move tab folders or feature/mate folders.
Parameter edits preserve existing membership. New features, tabs and recreated
mates record `cad_incremental_organization_pending`; they need the existing
native organizer to finish folder membership. Headless verification does not
pretend those organizational checks passed, and a loose tab is still an error.
This limitation also applies to full rebuilds. The explicit `organize` command
and its `--dry-run` can read unpublished incremental checkpoints; estimate its
editor feature-tree reads separately before running it. No browser is opened by
the incremental path. See [headless capabilities](../../docs/architecture/headless.md).
