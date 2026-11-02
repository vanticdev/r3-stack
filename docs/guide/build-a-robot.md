# Build a robot from nothing

Model the parts and real assembly in Onshape. R3 pulls an immutable local copy and checks
its geometry, wiring, electronics, software bindings, simulation and release evidence.
`robot.yaml` is the complete local description and index into the split description files. A robot may
be a stationary instrument, arm, drone, legged mechanism or mobile platform. Start with
an empty project and add only the components and capabilities your robot needs.

## 1. Create the project

```sh
r3 create sensor-rig --template blank
cd sensor-rig
r3 status
```

The default template is also `blank`. Creating a local project does not create or push
a GitHub repository. The scaffold includes `robot.yaml`, a local `catalog/`, CAD and PCB
folders, a `backend/` for deployable software, agent instructions and a validation workflow.
No reference robot or previous build directory is required.

An Agent-tab Codex session has a network-enabled shell, live web search, `gh`,
normal git and the R3 MCP tools. The user message is passed unchanged. Use
`r3_get_schema(domain="robot"|"component"|"device"|"model")` for the complete
typed authoring reference, including field descriptions and nested definitions
(also `connector` and `wire` for project catalog additions).
Use these tools and this guide; reading platform source is not required.
The session exposes R3 and onshape-native tools directly and excludes unrelated account
plugins and skills. Use its browser-session REST feature tools for CAD; no other project context is needed.
Temporary work belongs in the session's `TMPDIR`, under this project's `build/agent/`.
Use it for downloads and extraction, or use another directory inside this project.
Never search shared `/tmp`, `/private/tmp`, unrelated checkouts, other robots or old transcripts.
The Codex shell enforces project/platform read scope, with the runtime and authentication
access normal tools need. Its caches are project-local. Use `"$R3_PYTHON"` for Python
helpers; a login shell's `python3` can select a different interpreter.
If the native MCP connection reports `Transport closed`, inspect this session's
`build/agent/<session>/mcp/onshape_native/process.json` and `stderr.log` for its
exit and bounded error evidence. The companion is a separate process; use R3's
native bridge client to inspect its health and supported HTTP operations. A lost
MCP connection alone does not prove that the extension disconnected. Never replay
a mutation with an unknown outcome; inspect the native state first. Bundled schema
discovery also works through `r3_onshape_native(operation="api_catalog",
arguments={"schema": "getDocument", "detail": "full"})`, or
`NativeCadTransport().local("api_catalog", {...})` in the Python client.
This uses the same bundled catalog with zero Onshape calls; browser-session
operations remain available through `operation="api_request"` with their usual
guards and export restrictions.
Run native pipeline stages, scenarios and project tests with the R3 MCP tools;
they execute in the worker's probed environment. On macOS, the Codex shell sandbox
can deny CoreGraphics access even when native offscreen MuJoCo rendering works in
the worker. Put independent simulator experiments under `tests/` and use
`r3_run_project_tests`. Keep camera and perception requirements enabled.
For a source checkout without an installed `r3` executable, the CLI entry point is
`python -m r3_orchestrator` using the platform environment.

When the user explicitly requests GitHub publication, initialize a repository
inside the project (`git init -b main`) before staging files, then use `gh repo
create <owner>/<name> --private --source . --remote origin --push`. Verify
`git rev-parse --show-toplevel` names this project, especially when it lives
inside a checkout of R3. Follow the scaffold's `.gitignore`; never stage `build/`
or another repository. The session permits networking and writes to its own
`.git`; other workspace protections remain in effect. Creating the local
scaffold alone does not authorize publication or physical deployment.

For the web interface, build `apps/web` and run `r3 serve --projects-root <parent-directory>`.
An empty project root displays an empty project list. Create a project before adding parts.
The MCP equivalent is `r3_create_project(name="sensor-rig", template="blank")`.

## 2. Describe the task and choose a base

Write down the robot's purpose, workspace, loads, power source, environmental constraints
and measurable success conditions. Requirements belong in `requirements`; shapes, dimensions, attachment frames and
assembly placements are authored only in Onshape. Select `robot.mobility: stationary`
for a fixed instrument or arm. Mobile robots must declare their actual joints and drives.

When no human is available, make routine engineering choices independently. Choose
conservative, clearly labelled assumptions for unspecified screw lengths, printer build
volume, nozzle, material and process settings. Validate the design against those settings
and include an assumptions list in the final report. Select unspecified purchased parts,
mounting arrangements, power/wiring options and software architecture independently.
For an ambiguous owned-part variant, choose a documented variant as an explicit assumption,
finish the design against that assumption, and list the physical confirmation still needed.
These choices are engineering work, not reasons to ask the user or stop early.
Unknown physical dimensions and
unconfirmed purchased-part interfaces remain measurement blockers; never label them verified.
These block final qualification, rather than all other engineering work: finish the
independent parametric design, software and simulations under labelled assumptions,
then list the physical measurements still needed. A missing bank entry or simulated
device is a project research/authoring task, using the capabilities below.
Keep work inside the project and R3 platform sources; do not consult other local robot
projects, old robot git history, worktrees or other agents' session files.

The scaffold starts empty. Add objects and catalog metadata for the components you choose.
Create fabricated parts directly in a Part Studio; author sketches, dimensions, standard
features, material and mate connectors there. Never put shape programs, solids, features,
connector frames or assembly poses in the local description.

The project uses an explicit include index in `robot.yaml` to keep large descriptions
readable: `parts/<name>.yaml` holds each part's Onshape reference and pulled evidence;
`electrical/`, `software/` and similar folders hold domain descriptions. Use
`r3_get_schema` and `r3_get_spec` for the exact schema. `r3_patch_spec` applies typed patches
across the include files with optimistic concurrency. A change to any included file changes
the spec revision. Includes must stay inside the project; unknown fields, cycles and
conflicting keys are errors. Pulled fields are refreshed only by `r3_cad_pull`.

## 3. Find or source components

Use `r3_list_catalog` and `r3_get_catalog_component` to inspect purchased parts, connectors,
fasteners and materials. Entries retain manufacturer dimensions, model provenance and
explicitly labelled estimates. A part's model, electrical behavior, interfaces and pin
functions must match the exact SKU you will use.

For every catalog category, use `r3_list_catalog(kind=...)`, then
`r3_get_catalog_entry(kind, entry_id)` for full entries and
`r3_put_catalog_entry(kind, entry)` to add or override one in the project.
Kinds are `component`, `connector`, `wire`, `fastener`, `material`, `printer`
and `print_fit`; `r3_get_schema(kind)` describes each entry. Wire identities
are AWG numbers, print-fit identities are processes (`fdm` or `sls`), and
other identities are `id` strings. Use actual catalog ids when declaring joins.
Writes preserve other entries and metadata and reject invalid catalogs.
No platform catalog file needs editing.

For a part absent from the catalog, declare a requirement-based source slot:

```yaml
components:
  computer:
    source:
      requires: {kind: computer, ram: ">= 4 GB"}
```

Search with `r3 source computer` or `r3_source_search(component="computer")`. Choose a
candidate with `r3 source computer --choose <candidate>` or `r3_source_choose`. The chosen
entry and provenance are written to the local catalog and lock file. Inspect estimated
values and measure any uncertain mechanical interfaces before fabrication.

Use `owned: true` on parts already on hand. They remain in the model but are excluded
from purchasing and sourcing; `components.<id>.source` is a purchasing request, never CAD
provenance. Keep owned parts typed with `type` and record CAD sources in the project
catalog/model bank instead of `source`; see [owned parts](owned-parts.md).

For public research, use the session's live web search and shell downloads
(`curl --fail --location URL -o project-file`). Check that a download succeeded
before opening it. For PDF text, the worker Python has `pypdf`: use
`PdfReader(project_pdf_path)` with `"$R3_PYTHON"` and each page's `extract_text()`.
For drawings that need visual inspection, use Poppler when installed:
`pdftoppm -f 1 -singlefile -scale-to 1800 -png project-file.pdf build/drawing`,
then open `build/drawing.png` with `r3_view_artifact`. `pdftotext` is another
text-reader option. PyMuPDF's `fitz` module is not a required dependency; an
unavailable optional reader does not mean the datasheet is inaccessible.
Find official datasheets and exact
vendor CAD, not a visually similar SKU. Record the source URL, license, checksum,
dimensions, mass and which values are estimated in the project catalog. Add a real
download with `r3_models_add(name, category, url, license, catalog_component)`;
for an archive also supply `format="stp.zip"` or `"stp.rar"` and its exact `member`.
For a public Onshape model, supply its immutable `/documents/D/v/V/e/E` URL as
`onshape` instead of `url`. Use onshape-native to import/insert the exact vendor model in your working document,
keep its source unchanged and add the required mate connectors in Onshape.

`r3_models_search(query)` searches the shared bank plus project records. On a miss,
use `r3_models_search_public(query, limit=10)` to discover public Onshape Part Studios
through the browser session. Try appropriate manufacturer/product aliases yourself;
web indexing alone may miss public models. Results pin an actual version and retain
provenance but do not imply dimensional correctness or redistribution permission.
Inspect the exact source and license, then explicitly add the chosen source.
The search does not write catalog entries or export CAD. API rejections are findings,
not retry loops. Continue independent engineering work while external data is pending.

`r3_models_fetch(model_id)` downloads a pinned source and reports geometry or a
specific acquisition error. In a project session, added records live under
`catalog/modelbank/` and override the read-only platform bank. These human-editable
R3 records travel with the project. Use the `model` schema when recording reviewed
datasheet specs, model placement and interface frames. Never invent a passing
validation status: measured geometry must support the review. Unverified or
mismatched models remain explicit in CAD diagnostics.

Model-bank categories are exposed in the tools' enum/schema. Use `other` for a
category absent from that list; a component's catalog `kind` is a separate field.

## 4. Model and assemble in Onshape

Use the onshape-native plugin's `api_catalog`, `api_request` and `api_write` through
the authenticated browser-session bridge. Create sketches, extrudes, holes and mate
connectors as standard Part Studio features at
`/api/v9/partstudios/d/D/w/W/e/E/features`; create instances and standard mates at
`/api/v9/assemblies/d/D/w/W/e/A`. Inspect live schemas with `api_catalog` and feature
defaults with `inspect_model` → `feature_template` → `feature`. Preserve source
microversion/rejectMicroversionSkew guards and inspect regeneration after writes.
When an assembly mate selects a Part Studio mate connector, use the schema
`BTMPartStudioMateConnectorQuery-1324` with its occurrence path, featureId and
`partStudioMateConnectorQuery: true`; a generic feature query does not resolve it.
Inspect mate regeneration before versioning.
No FeatureScript is permitted. Shapes stay in Onshape, never in local YAML.

The native editor bootstrap is currently unavailable on some Onshape builds.
`native_state`/`native_write` failures do not block the tested REST path. Log an
unavailable editor-only operation and continue using working REST calls. Never
retry failed operations in a loop or replace CAD with fabricated local data.

The companion's `extension_connected: true` proves the browser bridge is connected,
not that Onshape still has a signed-in session. An upstream HTTP 401 with
`Unauthenticated API request` requires signing in to Onshape in that browser;
R3 reports `cad_native_authentication_required`. A local bearer-token rejection
or a document permission error is a different failure. Preserve work and continue
independent offline tasks while browser authentication is restored. Never replay
a failed write automatically; inspect the saved revision and state first.

Use `document_tree` to read tabs/folders and `api_catalog`/`api_write` for
`createPartStudio`, `createAssembly` and other tab operations. `document_edit` is
optional when its native editor dependency works. Create documents in the
project's designated folder through the native integration's supported workflow. For
purchased parts, search `r3_models_search` first, download the exact vendor STEP with
provenance, then import it into Onshape. Add mate connectors on its real seating faces,
holes and axes. Electrical and software catalog metadata stays useful; catalog boxes do
not become CAD. Use the browser-session bridge API tools for document creation and versions, as below.
If an API operation is rejected, inspect the result and use a native tool instead; never
retry in a loop or use an unqualified substitute.

Build the real assembly in Onshape using its native insert and mate operations. Define
attachments there, including joint type, axis and limits. Use full occurrence paths for
nested subassemblies and repeated parts. R3 reads solved REST occurrence transforms and connector
frames; it does not derive placements from catalog interfaces or synthesize assemblies.

Verify the design with geometry and images before pulling: body count, dimensions,
closed solids, material density, connector frames, mate status, solved placements and
motion limits. Use REST geometry/mass measurements and pinned `shadedviews` to inspect the model.
Native editor tools are optional when available. An image alone
does not establish fit or interference clearance.

### Assign native material through REST

An unsupported native editor does not block material assignment. Use
`onshape_native.api_request` through the browser-session bridge. Discover
`getWMVEPMetadata` and `updateWVEPMetadata` with `api_catalog`; their `iden`
path parameter is `p` for a part ID. Read the selected workspace part's metadata
at `/api/v9/metadata/d/D/w/W/e/E/p/P`, find the property named `Material`, and
retain its returned `propertyId` and `editable` flag. Never guess the property ID.

For a custom material, POST to that same metadata endpoint with this native
payload (Python notation below; the MCP tool accepts the equivalent JSON object):

```python
payload = {
    "jsonType": "metadata-part", "partId": part_id,
    "properties": [{"propertyId": material_property_id, "value": {
        "id": project_material_id, "displayName": material_name,
        "properties": [{"name": "DENS", "displayName": "Density",
            "type": "REAL", "category": "Physical",
            "value": density_kg_m3, "units": "kg/m^3"}]
    }}]
}
```

Choose the material identity and density from sourced engineering data; record
their provenance and uncertainty in the project catalog. This sets density only,
not strength, measured mass or a fabrication approval. For an existing Onshape
library material, use its actual material definition instead of inventing one.
Check the update's per-property status, then read `/api/v9/parts/d/D/w/W/e/E`
and selected-part `/partid/P/massproperties` below that path. Verify the assigned
material and `hasMass`, mass and volume. Apply the appropriate material to each
physical body of an imported multi-material model; grouping it as a composite
does not assign missing constituent materials. Create a new version after the
changes and verify the pinned read before pulling. Never substitute local mass
or density fields for native evidence.

## 5. Pull CAD and qualify fabricated parts

Use `onshape_native.api_catalog` to discover `createDocument`, `createVersion` and
`deleteDocument`, then `api_write`; `api_request` also accepts the browser-session routes:
`POST /api/v9/documents` with `{name, parentId, isPublic}`, and
`POST /api/v9/documents/d/{did}/versions` with
`{name, documentId, workspaceId, microversionId}`. Read the current workspace
microversion first; the version pins that exact state.
An operator can persist already-granted visibility consent when starting `r3 serve`:
set `R3_ONSHAPE_DOCUMENT_VISIBILITY=public` (or `private`) and
`R3_ONSHAPE_DOCUMENT_FOLDER_ID=<approved-folder-id>`. This adds scoped consent to
session instructions without altering the user prompt. It applies only to new documents
in that folder, never to changing existing document visibility. A private GitHub
requirement does not change that separate Onshape authorization.

Keep `parentId` in the designated project folder. Choose visibility compatible with the
account and user requirements; never change an existing document's visibility silently.
Record created IDs. To trash a run's document, verify its name and folder first, then
`DELETE /api/v9/documents/{did}`. These requests go through the companion's browser
session. No geometry export uses this route. `r3 cad pull` consumes the selected version;
it deliberately does not create or modify the design or document.

Create or select an immutable Onshape version, then pull the assembly:

```sh
r3 cad pull --url https://cad.onshape.com/documents/D/v/V/e/A
# or supply a workspace URL together with its explicit pinned version:
r3 cad pull --url https://cad.onshape.com/documents/D/w/W/e/A --version V
r3 validate --stage assembly,model,cad,fabrication
```

An explicit `/v/` URL selects its own immutable version; an explicit workspace
URL requires `--version` (MCP: `version_id`). Stored `cad.source` and
`cad.version_id` defaults are used when the URL argument is omitted. Explicit
conflicting URL/version arguments remain errors. Pull records its actual pin in
the immutable manifest and lock, without rewriting the authored source defaults;
update those defaults through the spec tools when changing the default target.

The MCP equivalent is `r3_cad_pull(url=..., version_id=...)`. `r3_run_stage("cad")`
checks the current pulled copy; it never creates geometry or refreshes the native model.
The pull downloads each exact part's STL in metres and STEP, reads native mate connectors,
mates, joint axes/limits and solved poses, computes volume/mass/centre of mass/inertia with
Open CASCADE plus the assigned material density, and captures pinned REST shaded-view evidence. A
missing material, unresolved occurrence, unsupported constraint or missing geometry is an
error. Pull never substitutes an envelope or drops a part.

Files live at `cad/pulled/pull.json`, `cad/pulled/parts/<name>.stl` and `.step`, with
pinned shaded-view evidence alongside them. All are read-only. After a fresh Git checkout, restore their mode with
`find cad/pulled -type f -exec chmod a-w {} +` (Git does not store read-only bits);
this changes no bytes and validation still checks every hash. The scaffolded CI does this.
`r3.lock.yaml` pins the manifest's
SHA-256 and Onshape version; the manifest pins every file's SHA-256. The pull refreshes
Onshape-derived fields in `parts/<name>.yaml` and the top-level attachments. Editing
pulled bytes, hashes, connector frames, material, mass, physical pose/mount mirrors or attachments locally is an
integrity error. Fix the model in Onshape and pull again.

The only export backend is the authenticated loopback companion at
`http://127.0.0.1:8766/native/api/v17`. It supports selected-part STL and STEP through
UI translations, polling and the translation's `resultDocumentId` external-data download.
STEP and glTF use asynchronous jobs. R3 records each exact pin, part, options and
translation ID under `build/cad/native-exports/`, then polls that existing job for up
to 30 minutes per invocation. If it is still pending or the client is interrupted,
call the same R3 operation again: it resumes that job and reuses completed,
checksum-verified bytes. Never resubmit an export just because a direct download
timed out. These are native export IDs, not the public import translation IDs.
R3 retains terminal failures and uncertain submissions rather than replaying them.
Inspect the reported source and job; correct the model in Onshape and pin a new
version when necessary. Do not edit the job cache or fabricate an export.
Public export endpoints are never used as fallbacks. Document management and other
working operations may use the authenticated browser-session `/api/...` bridge; no API keys
or expired saved sessions. A rejection is a finding, not a reason to retry in a loop. Only STL supports
microversions; STEP requires a pinned version. Install the updated extension and restart
the companion to expose `/native` routes. Unsupported operations fail with a clear code.
The desktop native integration needs its signed-in browser/companion on the worker;
a server must provide that native session or consume an already pulled copy.

All checks and consumers use the pull: fit/interference/thread checks read STEP with OCP;
wiring, simulation, viewer and fabrication read STL. No checks or tolerances are relaxed.
For native Composite Parts, the pull also downloads glTF and uses its separate native
Body nodes for mesh consumers. STL triangle soup loses body ownership at coincident
edges; do not union or reshape an otherwise valid purchased assembly merely to make
that soup manifold. R3 preserves every glTF facet and joins exact coordinates only
within each native body. Every body must still be finite, closed, consistently wound
and positive-volume. STEP verification likewise preserves its distinct native solids.
The original STL remains pinned for inspection, and glTF is pinned with its own SHA-256;
neither file may be edited. Unsupported or failed glTF exports remain errors, with no
public export or local geometry fallback. Only files exported from Onshape count as the model.
Do not enable `exportCompositeConstituentsWithOriginalBodyIds` for glTF: the native
exporter collapses composite Body groups with that option. R3 uses the default
native grouping and does not reconstruct missing ownership from face data.
If a check needs native thread annotations, preload, a material property or another datum
absent from the pull, the diagnostic names that missing evidence rather than inventing it.
Run fabrication validation for printed parts and inspect wall thickness, overhangs, loads,
strength margins, orientation and manufacturing artifacts. Test real retention under the
required loads. A successful pull is data acquisition, not a fabrication release.

## 6. Add power, electronics and wiring

Declare a supply compatible with every connected device, including transient demands,
voltage limits, protection and regulator margins. Model the actual board behavior and pin
maps in the catalog. Do not infer electrical connections from component proximity.

Model each nonintegral cable half and splice as an explicit native part named `<harness>.<node>`.
Add a native mate connector at its electrical mating plane; for a splice it marks the
sleeve centre and wire axis. Onshape REST commonly returns only its generated `featureId`
and coordinate system, even when its feature has a human name. Select that existing frame
with `mate_connector` on the harness connector or splice. For example, an unrelated fixture
may declare `connectors: {A: {type: fixture-contact, mate_connector: F_native_port}}`.
Record `feature.featureId` from the successful standard-feature creation response.
Assembly reads with `includeMateConnectors: true` also expose each part's
`mateConnectors` records, including `featureId` and `mateConnectorCS`, before a first pull.
Use the actual returned ID, not a fabricated ID or a local frame matrix. The selector is
scoped to the physical part `<harness>.<node>` and is recorded locally as an object reference.
After pull, its available keys are in that part's read-only `pulled.mate_connectors` metadata.
When the native response includes names, an actual `mating` or `origin` frame remains a
legacy default. An explicit missing selector fails rather than falling back or guessing.
Recreated native features need updated selectors and a fresh pinned pull. Do not query
throttled feature lists merely to recover names. Pull real assembled poses; routing keeps
those physical parts fixed and optimizes only the conductors between them. Integral leads
belong to the component and cannot use a separate cable-half frame selector.

Each harness declares connectors, wires, optional splices and routing constraints. Connector
`mates` fields name exact component or harness endpoints. Wire `from` and `to` references
name connector pins or declared splices. Choose catalog wire gauges and connector types
with compatible current and termination ratings. Direct board-to-board mates are explicit.

For every declared component connector, author its mating-plane connector in Onshape.
Map its logical catalog name to that existing native key through `r3_patch_spec`, for
example `components.fixture.onshape.mate_connectors: {socket: F_native_port}` on an
unrelated fixture. Use the actual returned feature ID or pulled connector key, never
a local frame matrix. When native data supplies the same logical name it remains the
default; an explicit missing reference is an error. Repeated instances can select their
own keys without changing shared catalog data. The pulled graph, connector checks,
partial mating sites and wire endpoints use those native frames and solved poses.
Catalog connector positions remain unqualified research metadata before a pull; they
cannot replace an absent native frame in a pulled design. Missing mappings are reported
with the available native keys. Changing CAD or references makes older evidence stale.

Run `r3 validate --stage connectors,wiring,electrical`. Inspect pin maps, conductor exits,
route witnesses, bend radii, dynamic clearances, supported spans, cut lengths, ampacity,
voltage drop, fuse coordination, thermal results and runtime assumptions. The manufacturing
package comes from the routed geometry; a rendered picture alone is not release evidence.

For a custom PCB, add its component to `pcb.boards` and author a `BoardIntent` in the board
source folder. Use official KiCad symbols and footprints. Declare converter designs only
when that board contains them. The headless flow captures the schematic, runs ERC, builds
the board with `pcbnew`, routes with Freerouting, fills zones, runs DRC/parity and exports
fabrication files. `board.json` reports that board's actual connectors, geometry and checks.

## 7. Add firmware and bindings

Deployable source belongs under `backend/<language>/<module-folder>`. One module owns one
complete source folder. Use the scaffolded BLITZ hooks and typed module/process APIs.

Add `software.modules` and bindings between computer pins and the devices actually reached
through the electrical graph. Binding `expects` fields describe intended reachability;
they cannot substitute for a wire. Pin functions, device paths, channels and bus addresses
must agree with the chosen computer and peripherals.

Use `r3_hal` for hardware access. The same firmware can run against physical hardware or
the twin. Simulation time advances through the HAL clock; avoid wall-clock sleeps in control
loops. Configuration belongs in the project, rather than hardcoded component IDs or robot
dimensions in platform code. Firmware can run as a host process or in a BLITZ-shaped container. A missing Docker
daemon blocks container and target-build operations; it does not prevent a supported
host-process firmware run against the native twin. Keep the same real firmware, HAL
bindings and checks, and report target-build qualification separately.

## 8. Define and run scenarios

Start with a short observation scenario, then add task-specific motion, loads, environmental
conditions and fault cases:

```yaml
simulation:
  timestep: 1 ms
  seed: 11
  scenarios:
    - id: observe
      duration: 2 s
      initial: {battery_soc: 0.9}
      expect: {brownouts: 0}
```

Use `r3 sim run --scenario observe` or `r3_run_scenario`. Mandatory mechanical load cases
are generated for the declared robot; unsupported robot-type-specific cases emit explicit
diagnostics. Inspect `build/sim/<scenario>/summary.json`, recordings, logs and plots.
MuJoCo, ngspice, native elastic cables, radio propagation and the real firmware advance
in the coordinated twin. Estimated models and fallbacks remain visible in the evidence.

Choose expectations that measure your task: position or joint motion, sensor behavior,
retention, clearance, supply limits, fault response or other supported quantities. Add
requirements as the project grows; do not weaken checks to fit a failing design.

### Project-owned devices and tests

When a device is absent from the platform, implement its bridge in this project's
`simulation/` directory. Do not edit R3 source. Declare it with `r3_patch_spec`:

```yaml
simulation:
  devices:
    instrument:
      component: fixture
      source: simulation/instrument.py
      factory: create_device
      params: {}
```

`factory(context)` returns an object with `step(dt)` and
`request(operation, payload)` methods. `step` runs after each native physics and
circuit step. `request` returns a JSON object, or `None` until data is ready.
`context` provides `t` (simulation seconds), `component`, authored `params`, the
real MuJoCo `model` and `data`, `powered` (from the circuit), and `bindings` (only
electrically reachable interfaces for that component and computer). Use MuJoCo's
public APIs for physical observations and vendor SDK semantics for register or
packet logic. Keep state and clock timing inside the device object. A source path
or symlink that escapes this project's `simulation/` directory is rejected.

For example, a synthetic fixture position readout can use native rigid-body state:

```python
import mujoco

class Instrument:
    def __init__(self, context):
        self.context, self.ready = context, False

    def step(self, dt):
        self.ready = self.context.powered

    def request(self, operation, payload):
        if operation != "position":
            raise ValueError("unknown operation")
        if not self.ready:
            return None
        c = self.context
        body = mujoco.mj_name2id(c.model, mujoco.mjtObj.mjOBJ_BODY, c.component)
        return {"t": c.t, "xyz": c.data.xpos[body].tolist()}

def create_device(context):
    return Instrument(context)
```

Firmware calls `hal.device_request("instrument", "position", timeout=0.1)`.
Waiting advances the same simulation clock as normal HAL sleeps. Missing devices,
unpowered circuits, unreachable software pins and timeouts remain errors. This
generic extension is a virtual-device API: physical operation requires your
project's real vendor-driver implementation. Never substitute simulated data on
physical hardware.

An electrical device may also declare `electrical_factory` in the same device
entry, and use catalog `behavior.model: project.instrument`. That factory has
signature `(component_id, component_type, params, build_context)` and returns an
`r3_sim.electrical.models.BehaviorModel`. For a measured constant-current load,
reuse `r3_sim.electrical.models.ConstantLoad(..., model="project.instrument")`
with the catalog's sourced `current` parameter; for complex circuits integrate
ngspice through that public model API. Do not implement another domain solver.
Device logic cannot replace the native rigid-body, circuit or cable solver, or
change standard checks. Calibration limits and approximation assumptions stay
visible in the report. Device models default to `provenance.status: estimated`;
datasheet or measured provenance requires a nonempty source/calibration reference.
Unqualified behavior stays estimated in release evidence.

Write Python design tests under `tests/test_*.py`. They can assert properties of
the authored spec, run their own native-simulator experiments, exercise the device
factory, or inspect the twin's `build/sim/<scenario>/recording.npz` and summary.
Test real physical quantities and the actual firmware's behavior, not a
hand-authored success flag. Run them with `r3_run_project_tests`,
`r3_run_stage("tests")`, or `r3 validate --stage tests`. Full validation runs this
stage after simulation. Logs and JUnit results are in `build/tests/`; an empty or
entirely skipped suite cannot pass. Standard pipeline checks remain in force.
Firmware, device-source and test edits invalidate older release evidence.

To check a custom requirement that is not a numeric stage metric, retain its
description and bind it to an actual project test:
`requirements.extra.fixture: {description: "The fixture has a declared root", test: "tests.test_design::test_root_declared"}`.
Use the exact case ID from the test stage's `metrics.cases` result. The test must
assert the stated requirement against the design or native-twin evidence.
Release uses the runner's actual JUnit outcome; missing or skipped cases remain
pending, failures block release, and editing the test makes older evidence stale.
Never replace the requirement with a hand-authored passing flag.

## 9. Iterate to release evidence

The usual loop is `r3_get_spec` → `r3_patch_spec` → `r3_validate` → `r3_diagnose` → fix.
CLI equivalents are editing `robot.yaml`, `r3 validate` and `r3 diagnose`. Build a small
working assembly first, then add power, actuators, sensors, harnesses, firmware and scenarios.

`r3 status --release` (MCP: `r3_release_status`) lists requirements, scenario expectations
and stages with `verified`, `estimated`, `failed`, `stale` or `not_run` evidence.
`fabrication_ready` requires current verified evidence for every applicable item. Review
assumptions and unresolved diagnostics before manufacturing or deployment. Physical
fleet deployment and GitHub publication are separate actions requiring an explicit request.

Custom numeric requirements can bind to a metric that a pipeline stage actually produces:
`requirements.extra.payload_limit: {stage: model, metric: total_mass, op: "<=", value: 2.0}`.
The metric path is relative to that stage's `result.json` metrics, values use SI, and
supported comparisons are `<`, `<=`, `==`, `>=` and `>`. Choose a metric the stage documents
and reports; an absent metric stays `not_run`. Failed, stale or estimated stage evidence
cannot become verified custom evidence. Free-text requirements remain visible and pending
until they have an automatic check; never replace one with a hand-authored passing flag.


### Purchased-model search misses

After a real search of the model bank, public Onshape, vendor sources and permitted
libraries finds no downloadable exact model, author a simple proxy **in Onshape**
from sourced datasheet dimensions, with standard features. This is the sole exception
to the purchased-part sculpting prohibition. Name it `PROXY`, record the searched
sources/queries/results and datasheet references, and keep model provenance `estimated`.
Set the local component metadata through `r3_patch_spec`, for example on a synthetic fixture:
`components.fixture.params.cad_proxy: {searches: ["source/query/result"], datasheet: "source reference"}`.
These are provenance notes, never local shape instructions. Pull the proxy from a pinned
Onshape version normally; never generate geometry locally or edit the pulled copy.
Release status keeps a separate estimated item for each declared proxy. Continue the
entire design, wiring, software and scenarios; estimates do not justify ending the run.
List every proxy and measurement needed at the end. Remove the proxy declaration only
after recording real measurement qualification or replacing it with a qualified model.
No standard fit, physics, fabrication or release checks are waived.

### Project checkpoints

For scaffolded robot projects, commit and push work-in-progress frequently to the
project's own user-requested repository, even while checks fail or engineering is
unfinished. Include source, provenance, assumptions and an honest status report; exclude
secrets and generated `build/` artifacts. This project-specific checkpoint policy overrides
parent repository guidance forbidding incomplete commits. A pushed checkpoint is not a
fabrication release or permission to deploy hardware.

For STEP imports use the uncompressed `.step`/`.stp` file, not a ZIP containing STEP.
The companion's chunked upload path transfers large files to the authenticated browser,
which sends one normal Onshape multipart import. Inspect the result before another write.

For an unattended build without a user deadline, a long runtime, perceived time budget
or uncertain wait estimate is not a stopping condition. Checkpoint and continue. Scope
operation failures narrowly: a held feature-list read does not prohibit other working
inspection routes, guarded feature writes, material assignment, assembly, wiring or
firmware work. Before reporting a project-wide external blocker, identify the exact
unavailable operation, supported alternatives tried and the tasks that actually depend
on it. Finish independent work while that constraint remains.

A checkpoint is a recovery point, not the end of an implementation session. Keep working
through unassembled parts, unconfigured harnesses/power and firmware integration. Check
asynchronous imports at sensible intervals while doing independent work; an ACTIVE
translation is not a final blocker report. Ordinary recoverable errors require another
iteration. Conclude with completed integrated work or a demonstrated external constraint
that cannot be resolved under the research, assumption and proxy policies above.

If acquisition or geometry validation fails, `r3 cad pull` retains the unchanged
exports for inspection. Check a candidate's pinned files before building the
complete assembly: use `r3_cad_export` for its STEP and glTF, then call
`r3_onshape.step_geometry.part_from_step("source-check", exported_step_path)` and
`r3_model.mesh_io.load_exported_mesh(exported_gltf_path)` from the platform Python
environment or a project test run through `r3_run_project_tests`. These read-only
geometry preflights raise on invalid source geometry. Complete the native assembly
and run `r3 cad pull` and the pipeline for full qualification.

Failed pulls retain the unchanged
STL/STEP/glTF downloads under `build/cad/failed-pulls/<attempt>/`. `failure.json` records
the pin, file hashes, error and diagnostic details. A glTF body failure reports
finite-vertex, winding, watertight and positive-volume flags, plus counts of
boundary edges and edges shared by more than two faces. The pull response's
`component` and `mesh` fields identify the failed body and these topology results.
Inspect these files to diagnose the failure; they
are not a valid pull and cannot replace `cad/pulled/`. Repair CAD in Onshape and
pull again. Mesh decoding joins only exactly identical coordinates, without
rounding nearby vertices, filling holes or removing failing geometry.

On HTTP 429, record the response's `Retry-After` and stop requests to that resource
until the reported deadline; do not retry to see whether it has cleared. R3 native
transport failures expose `retry_after`, `retry_after_seconds` and `resume_at` when
the server supplies a valid header (seconds or an HTTP date). Missing/invalid
headers remain explicit; no reset time is guessed. Continue independent offline
engineering and retain the quota observation in the project report.

For an unattended long build, keep the same session alive through a finite server
backoff: checkpoint, do independent work, then use bounded local sleeps and resume
after the deadline. Do not poll the throttled API or discard the unfinished build
solely because a finite wait is needed. Stop if the user cancels.


A rate limit applies to the observed operation; do not infer an account-wide
outage. Avoid helpers that internally repeat that operation, and continue with
known working operations outside every listed hold, including native exports only
when they are unthrottled. Direct standard-feature REST
calls are supported; preserve fresh revision guards and inspect returned feature
status and actual geometry. Do not bypass checks to avoid an inspection failure.
Operators may supply known transport backoffs as a JSON list in
`R3_ONSHAPE_BACKOFFS`, with `method`, `resource` (API path glob), and `until`
(timezone-aware ISO timestamp), or `until: null` when no reset was reported.
An unknown reset remains a scoped no-retry instruction for the session until the
operator supplies new context; it is never converted into a guessed wait.
Sessions receive current backoffs as platform context, independently of the
unchanged user prompt; expired timestamped entries are ignored.

If Part Studio feature-list reads are throttled, standard-feature authoring can
still work without that read. Obtain a fresh workspace microversion from
`GET /api/v9/documents/d/D/w/W/currentmicroversion`, then submit the complete,
schema-discovered standard feature to
`POST /api/v9/partstudios/d/D/w/W/e/E/features` with
`sourceMicroversion` set to that returned `microversion` and
`rejectMicroversionSkew: true`. Use `api_request` or the discovered `api_write`
operation. This route was verified with a synthetic native composite while the
feature-list read was throttled. Check the returned feature status, parts,
geometry, material/mass and actual images through working endpoints; never
treat missing validation as a pass. A conflict requires a fresh read and an
intentional edit. Do not edit unknown feature definitions or use inspection
helpers that silently repeat the throttled read.


### Physical hardware and typed catalog categories

Use component `kind: fastener` for screw, nut and other physical fastener
occurrences, and `kind: connector` for physical connector bodies. They can be
included in a native assembly and indexed locally without being called structural
parts. `mechanical.fabricated` still declares whether each part is printed; the
category never substitutes for sourcing, geometry, fit, strength or fabrication
qualification. The separate `catalog.fasteners` table holds joint-rating data;
a component model is not proof of a fastener's grade or load capacity.
`r3_list_catalog(kind="fastener")` returns physical types in `components` and
rating records in `fasteners`. Connector bodies and mating families similarly
remain separate `components` and `connectors` collections.

### Pinned source exports and cached geometry inspection

Use `r3_cad_export(url=..., part_id=..., format="step")` to download one part from
an immutable Onshape Part Studio version. A workspace URL also needs
`version_id`. `format="stl"` downloads the native mesh in metres;
`format="gltf"` preserves the exported native body groups. All formats use
only the native export transport. The result supplies a local `file`, a manifest,
the Onshape pin and SHA-256 under `build/cad/inspection-exports/`. These are
unqualified inspection/source files: import purchased models into your Onshape
document and assemble them there. Only `r3_cad_pull` installs the read-only,
verified model used by the viewer and simulation. Never edit exported geometry.

Native mesh accuracy can be tightened through `r3_patch_spec`, for example on an
unrelated precision fixture:

```yaml
cad:
  mesh_export:
    chord_tolerance: 0.01 mm
    angle_tolerance: 0.5 deg
    maximum_chord_length: 1 mm
    minimum_facet_width: 0.001 mm
```

These are Onshape export settings, never local geometry instructions or check
thresholds. Inspection exports and assembly pulls use the same project settings.
Defaults preserve the existing 0.05 mm chord deviation, 2 degree angular deviation
and 10 m maximum glTF chord length; only tighter values are accepted.
`minimum_facet_width` optionally requests a finer STL facet width (at most
0.05 mm); omission preserves the native default. `maximum_chord_length` applies
to glTF. STEP exports are unchanged. The manifest records the selected settings,
and native export caches distinguish different requests while reusing the same
pinned STEP. Finer settings can increase file size and computation; they do not
make invalid geometry acceptable. Repair actual source defects in Onshape and
retain every standard closure, volume, fit and release check.

Qualify a representative source before repeating it throughout an assembly: check
closed solid bodies, assigned material density, native STEP and the mesh export
needed by that source. A composite groups bodies; it does not heal open surfaces.
An export failure identifies a source to investigate with supported Onshape import
options or another real model. One failed export does not establish that no exact
model exists, and does not waive the recorded-search policy for proxies.

For a composite's independent source test, export `format="gltf"` and use R3's
public reader, which checks every exported native body without welding bodies
together. Check exact STEP separately with the supplied native material density:

```python
from pathlib import Path
from r3_model.mesh_io import load_exported_mesh
from r3_onshape.pull import mass_properties

mesh = load_exported_mesh(Path(native_gltf_file))
properties = mass_properties(Path(native_step_file), native_density_kg_m3)
```

These inputs are the unchanged pinned files returned by `r3_cad_export`, not
locally generated geometry. Retain their hashes. A global STL vertex join loses
body ownership and cannot qualify a composite; do not union a valid purchased
assembly to satisfy that lossy test. Both readers still reject invalid geometry.
Add independent dimensional, interface and source checks needed by the design;
inspection exports remain unqualified until the assembled `r3_cad_pull` succeeds.

For browser-session imports, pass `encodedFilename` with the actual format suffix,
for example `source.step`. Use uncompressed STEP. The upload tool derives a
missing import filename/suffix from the local file, but you must still check the
returned translation and element type: `DONE` with a BLOB is not a translated
solid Part Studio. Inspect its parts before qualifying the import.

For an import into editable native CAD, use `formatName: ONSHAPE`,
`storeInDocument: true` and `translate: true` through the plugin's discovered
upload/import operation. `encodedFilename` describes the input file;
`formatName` selects the **output**, so `formatName: STEP` does not request an
editable Onshape model. Record the returned translation ID, inspect that job
until it completes, then inspect its `resultElementIds`, element types, actual
parts and shaded views. Do not repeat an upload whose outcome is unknown.
An empty parts list or missing expected bodies needs investigation of the source,
import options or another real model; a `DONE` translation alone proves none of
those geometry checks. Preserve the original downloaded file and provenance.

Likewise, OCP `ReadFile`/`TransferRoots` success proves that STEP can be parsed,
not that it contains valid closed positive-volume solids. Use the documented STEP
reader below for source checks, and inspect the actual translated native bodies.
A readable source with an empty or invalid native import is still unqualified.
Do not infer that the whole CAD path is unavailable from one failed source. Local
source inspection is research only; import, author and assemble the model in Onshape
and use a verified pull for every viewer and twin model.

### Distinguish assembly preparation from pull failures

`cad_pull_missing` with `phase: assembly_selection` means the pinned assembly
does not contain every described component, or occurrence selectors do not match.
`matched_components` and `missing_components` identify the difference. This check
runs **before any geometry download**. Complete and mate the occurrences in Onshape,
verify their selectors, create a new version and pull again. Do not remove required
components to get past this check. Imported Part Studio tabs are not automatically
assembly instances; a populated document can still contain an empty assembly.

`r3_cad_export` succeeding proves an individual source can be downloaded, not that
the robot has been assembled or pulled. Only a successful `r3_cad_pull` installs
`cad/pulled/`; until then the viewer and twin have no verified model. For a later
download or geometry failure, use the returned native job/error and retained
failed-pull artifacts to investigate that operation. Keep unrelated engineering
moving while investigating one source. Separate an unfinished task from a
demonstrated external constraint in checkpoint reports.

Public discovery preserves usable candidates when another candidate fails.
Read `complete` and `warnings` as well as `models`; an empty incomplete result is
not evidence that no model exists. Rate-limited candidate reads stop without a
retry and preserve the server deadline.
Its limit applies to returned models, including when a matching document is a
large library. Matching Part Studio names rank before unrelated tabs, using
local metadata only. Relevance ranking does not qualify geometry or provenance.

Cached native artifacts can be inspected in bulk without repeating API calls or
paging thousands of individual faces. The client retrieves only the exact returned
content-addressed handle through the authenticated companion and checks its SHA-256.
It needs no read access to the companion cache directory, and exposes no cache listing.
The companion must advertise `artifact_download_v1` in its authenticated `/health`;
update/restart it if that route is unavailable. Use the public Python client in the R3
worker environment (no token handling or source-code discovery is needed):

```python
from r3_onshape.native import NativeCadTransport
artifact = NativeCadTransport().artifact("<64-hex artifact id>.json")
# Query the returned JSON in memory to identify real topology references.
# Do not construct model geometry from those faces.
```

For a saved shaded-view response, decode its image unchanged to a PNG under
`build/`, then open it with `r3_view_artifact`:

```python
import base64
from pathlib import Path
image = artifact["images"][0]
if isinstance(image, list):
    image = image[0]
path = Path("build/inspection/native-view.png")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(base64.b64decode(image.split(",")[-1], validate=True))
```

`r3_guide(topic="project_code")` documents custom device factories. A missing or
non-callable project electrical factory fails with
`project_device_configuration_invalid`; define it in the project's `simulation/`
code and keep the native circuit engine and checks in place.

Native assembly snapshots retain both raw connector frames (`mateConnectorCS`)
and effective constraint frames (`matedCS`). Both are part-local; the latter
already includes Onshape's evaluated mate alignment and offsets. R3 preserves
the raw authored connector for port selection, and checks each mate using its
separate effective frame transformed by the unchanged native occurrence pose.
A failed closure is reported; R3 never solves or adjusts CAD placements locally.

Native source identity includes the document, element, part ID, configuration
and immutable source revision. Different revisions of one part keep their own
connector and material records. Missing or ambiguous revision matches fail
before export; R3 never substitutes another revision's metadata.

If catalog loading reports `catalog_model_selection_required`, inspect its
`candidate_ids` with `r3_models_get` and choose the qualified source yourself.
Use the returned component metadata to set `model.params.bank_id` through
`r3_put_catalog_component`. The error does not choose a model or qualify it;
stages remain failed until the ambiguity is resolved. No CAD is changed by
this metadata selection.

Catalog updates preserve the existing project file that owns an entry, including
other entries and family metadata. If `catalog_entry_ambiguous` lists multiple
project owners, resolve the duplicate definitions before updating; the tool
refuses to choose or rewrite an arbitrary owner. Catalog owner lookup and writes
remain confined to the project.

### Native mass properties and overrides

Author mass, center-of-mass and inertia changes in Onshape, then version and pull.
Pull reads each unique source part's pinned `/partid/P/massproperties` through
the browser-session REST bridge with `useMassPropertyOverrides=true`; the API
default is false. Complete native mass is required for every constituent body.
The pull preserves Onshape's calculated values and calculation bounds, using
its center-of-mass inertia in the unchanged source axes. Missing, incomplete or
invalid native properties identify the source occurrence before geometry exports.
Fix them in Onshape; no local density-based replacement is used for physics.
STEP still provides exact geometry verification and retained uniform-density
integrals for inspection. Neither this read nor its validation authors geometry,
and it adds no Part Studio feature-list calls. Repeated occurrences share one
pinned mass read per unique source/version/configuration/part.
