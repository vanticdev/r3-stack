# Robot descriptions and pulled CAD

`robot.yaml` is the description index. It keeps robot identity, requirements,
the component instance list and assembly attachments. Part records and domain
records live in separate YAML files. Every stage still consumes one resolved
`RobotSpec`; stages do not implement their own include handling.

```text
robot.yaml
parts/base.yaml
parts/link.yaml
electrical/system.yaml
software/system.yaml
simulation/system.yaml
cad/pulled/pull.json        # immutable local CAD, pinned in r3.lock.yaml
cad/pulled/parts/*.stl
cad/pulled/parts/*.step
```

The only include syntax is a mapping with exactly one `$include` key:

```yaml
schema: r3/robot@1
robot: {name: mechanism, mobility: stationary}
requirements: {max_mass: 4 kg}
components:
  base: {$include: parts/base.yaml}
  link: {$include: parts/link.yaml}
cad:
  source: https://cad.onshape.com/documents/D/w/W/e/ASSEMBLY
  # version_id: V  # optional explicit version selection
attachments: []  # populated by pull; never patch this field
```

Domain sections use the same syntax at the root:

```yaml
electrical: {$include: electrical/system.yaml}
software: {$include: software/system.yaml}
```

For example `parts/link.yaml` contains a complete component value:

```yaml
type: project-link
onshape:
  occurrence_path: [instance-id]
  configuration: ""
label: Moving link
# pulled, pose and mount: populated by CAD pull, never authored
```

Includes may replace any value, including a list element. A file contributes its
whole value at that position; there are no implicit merges, directory scans or
globs. Paths are normalized, literal and relative to the project root, including
when the declaring file is nested. Includes must remain inside the project.
A file may be included only once; cycles, duplicate YAML keys, unknown schema
keys, duplicate identities and undefined local references are errors. Existing
single-file descriptions remain supported, with the same strict validation.
External catalog connector and pin checks remain in the graph/model stage.
Physical `pose` and `mount` compatibility fields are refreshed from the native
assembly alongside `pulled`; local changes are integrity errors. They never
position a physical part independently of the pull.

`r3_model.spec_files.load_spec_files(root)` retains the source file and source
path for every resolved value. `SpecError.errors` carries stable `code`, `file`,
logical `path`/`loc`, `source_path` when available, and `msg`. Codes include
`spec_include_missing`, `spec_include_invalid`, `spec_include_outside_project`,
`spec_include_cycle`, `spec_duplicate_definition`, `spec_unknown_key` and
`spec_reference_undefined`. YAML syntax errors use `spec_yaml_invalid`.

`r3_get_spec` and HTTP `GET .../spec` return the merged authored spec, every
source file's YAML and SHA-256, ownership paths, and read-only paths. Their
`sha256` covers every included filename and its exact contents. Project
fingerprints use this revision too, so editing a part, wiring file or software
file invalidates prior stage evidence. Unreferenced files are not spec inputs.

`r3_patch_spec` accepts an RFC 7386 merge patch of the merged spec. It routes
changes through includes, preserving comments and untouched files. Adding a
component to a split project creates `parts/<id>.yaml` and its index pointer.
Include pointers are managed by this layout; direct file editing can reorganize
an existing description. The complete prospective description validates before
any file is written. A project-wide lock serializes tool writers and readers;
all changed files are staged, flushed and individually atomically renamed, with
rollback if a replacement fails. As with ordinary multi-file edits, a power loss
during several renames is not a filesystem-wide atomic transaction. External
editors should respect `.robot.yaml.lock` when writing concurrently.

A stale revision returns `spec_conflict`; an invalid patch returns `spec_invalid`
with file/path diagnostics. Deleting an object that is still referenced is
rejected, including in a single-file project. `dry_run` validates and reports
proposed hashes without writing files.

## CAD authority and the pull hooks

The agent authors parts, mate connectors and mates in Onshape using standard
editable features. R3 pulls a read-only local copy; no shapes are authored in
the description. `cad.source` selects an assembly URL; optional `cad.version_id`
selects a version. Each component's `onshape.occurrence_path` and `configuration`
select its occurrence. `RobotSpec.parts` is a read-only alias of `components`.

Each component's `pulled` value has `owner: onshape-pull`, pinned `document_id`
and `version_id`, the full `onshape` reference (`document_id`, `version_id`,
`element_id`, `part_id`, `occurrence_path`, `configuration`), `stl`/`step` paths
relative to `cad/pulled`, `sha256: {stl, step}`, local 4x4 `mate_connectors`, a
solved 4x4 `pose`, `mass`, `com`, `inertia`, `material`, `density`, `volume` and
`purchased`. Values and SI units match `r3/pull@1`. Top-level `attachments`
contains native mates with `owner: onshape-pull`, `feature`, `id`, `kind`,
`a`/`b` pairs `[part, connector]`, `axis`, and `limits`.

These blocks are marked `readOnly` in the schema. `r3_patch_spec` refuses edits,
creation, or deletion through their parents with `spec_read_only`.

After publishing `cad/pulled/pull.json`, its read-only files, and the
`r3.lock.yaml: cad_pull.manifest_sha256` pin, the pull path calls:

```python
from r3_model.description import refresh_pulled
refresh_pulled(root, manifest)  # root: Path, manifest: dict
```

This mirrors the verified manifest into the owning part files and attachments.
The pulled instance set must exactly equal the described components. Missing
or extra instances fail before any file is changed; the hook never invents
types, requirements or catalog records. Lower-level `refresh_pulled_spec(root, expected_sha256=None)`
provides optimistic concurrency when needed. No general spec editing tool can
invoke these hooks.

Geometry consumers use the shared pulled contract and check its local mirror:

```python
from r3_model.pulled import load_pulled
from r3_model.description import check_pulled
model = load_pulled(root)
check_pulled(root, model)
```

`validate_pulled_spec(root)` combines both steps and returns the verified model.
The helpers verify manifest pinning, file permissions and checksums, then compare
resolved derived metadata, local frames, solved poses and attachments exactly.
Tampering or stale values raise `PullError` with `cad_pull_modified`; missing or
invalid manifests retain `load_pulled`'s stable diagnostic codes. Editable YAML
mirrors never replace the immutable manifest as geometry authority.
