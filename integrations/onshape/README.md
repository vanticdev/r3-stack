# Onshape integration

Agents model parts, mate connectors and the real assembly directly in Onshape through
onshape-native. R3 never generates native features, parts or assemblies from YAML.
Purchased models come from the model bank/vendor STEP and are imported in Onshape.

`r3 cad pull --url https://cad.onshape.com/documents/D/v/V/e/A` downloads a pinned,
read-only local copy. `--version V` can pin a workspace URL. Pull reads real occurrences,
connectors, mates and limits, downloads selected-part STL (metres) and STEP, computes
mass properties with OCP and native material density, and captures viewport evidence.

`native.py` is the dedicated loopback transport. Downloads use `/native/api/v17` only;
model inspection uses local bridge routes/editor commands. Public `/api`, bodydetails,
massproperties, shadedviews, tessellatedfaces and public translations have no fallback.
The updated extension and restarted companion are required. Unsupported operations,
missing native metadata and incomplete constraints fail explicitly.

`cad/pulled/pull.json` and `cad/pulled/parts/*.{stl,step}` are immutable. The project
lock pins the manifest checksum; the manifest pins all geometry hashes and the native
version. `r3_model.pulled.load_pulled` is the integration-independent consumer contract.
Description files are refreshed by pull; validation rejects local modifications.

See [build a robot](../../docs/guide/build-a-robot.md) for the agent workflow. Tests use
synthetic STEP/STL and a fake transport; live browser validation is separate.
