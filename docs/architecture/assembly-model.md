# Native assembly model

Onshape owns parts, mate connectors, real assembly occurrences and mates. The agent models
and verifies them through onshape-native. R3 never builds an assembly from catalog interfaces,
YAML solids or authored local poses.

`r3 cad pull` reads an immutable Onshape version and records its solved occurrence transforms,
part-local mate-connector frames, mate endpoints/types, joint axes and limits. `robot.yaml`
is the complete local assembly index; `parts/<name>.yaml` holds read-only pulled evidence and
`attachments` records the native mates. Explicit `$include` values keep descriptions small.

The local geometry lives at `cad/pulled/parts/<name>.stl` and `.step`; `cad/pulled/pull.json`
records SHA-256 hashes, material/mass/inertia, poses and mates. `r3.lock.yaml` pins its manifest.
Every CAD consumer checks integrity first. Any described part, connector or attachment absent
from the pull, changed file/hash or changed Onshape-derived value is a stage-level error.

STEP is read with Open CASCADE for analytic face/fit/thread and interference checks. STL
provides the mesh used by wiring, fabrication, the viewer and simulation. Assembly positions
are the pulled solved poses, not a reconstruction from catalog interfaces. Physical checks
and tolerances stay unchanged; a datum absent from the pull must be named by a diagnostic.

Catalogs retain purchased-part electrical, software and physical qualification metadata.
They cannot replace native geometry. To correct a fit, shape, material, connector, mate or
placement, edit it in Onshape, verify geometry and viewport images, create/select a version
and pull again. Neither validation nor the viewer modifies the local pulled model.
