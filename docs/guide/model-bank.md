# Purchased-part model bank

Install conversion dependencies with `pip install ".[models]"`. VRML also needs the
Assimp CLI (`assimp export`); KiCad VRML library units are converted from 0.1 inch
to metres. STEP is preferred for exact native CAD.

Search the bank before searching the web or creating CAD for a bought component.
The bank indexes real model sources and explicit gaps. An entry with no downloaded
source is a proxy record, not proof that a model exists. A downloaded model still
needs geometry validation before it can be used for fabrication.

```bash
r3 models search 'Pi 5'
r3 models search motor --manufacturer Pololu --specs '{"mass": [0, 0.25]}'
r3 models show raspberry-pi-5-4gb
r3 models fetch raspberry-pi-5-4gb
r3 models validate raspberry-pi-5-4gb
r3 models add --name 'My motor' --manufacturer Vendor --mpn M123 --category motor \
  --url https://vendor.example/M123.step --license 'Vendor CAD terms'
r3 models add --name 'Public motor' --category motor --onshape 'https://cad.onshape.com/documents/<did>/v/<vid>/e/<eid>'
r3 models ingest pololu --url https://www.pololu.com/product/4091/resources --name D36V50F5 --mpn 4091 --category regulator
r3 models ingest kicad --query XT60
r3 models ingest onshape --query 'Raspberry Pi 5'
```

For ZIPs use `--format stp.zip --member path/in/archive.step`. An ambiguous archive
is rejected. `--json` works on each command. KiCad ingestion reads the installed
library through its normal installation paths, maps STEP names to library
footprint identifiers, and stores portable upstream URLs. It does not open a GUI.

Resource-page plugins are `raspberrypi`, `arducam`, `pololu`, `ublox`, `sparkfun`
and `adafruit`. Supply an official public resource-page URL with direct STEP or
STEP ZIP links. These plugins observe robots.txt, stay on the provider's public
hosts, and do not crawl behind logins. A source with no CAD links returns no
models. `traceparts` and `3dcontentcentral` retain account-gated locations as
`requires_login: true` pointers; humans fetch those under the source's terms.
Onshape public search uses the official REST API and ignores private documents and
unversioned workspaces. Onshape operations need API-key credentials on a server.

## Dataset and cache

`packages/component-catalog/r3_catalog/modelbank/data/*.yaml` is the human-editable,
git-tracked source of truth: one model per record. RAM variants may share a single
model through `catalog_aliases`; physically different parts must not be conflated.
Records contain identity, category, aliases/tags, SI specs with units and sources,
ranked model locations, licensing, checksums, acquisition dates, search history,
validation evidence and an optional `catalog_component` link.

Set `R3_MODEL_BANK` to a dataset directory to maintain a separate personal or
project bank. Set `R3_MODEL_CACHE` to change the default `~/.cache/r3/models`.
The content-addressed directories contain original bytes, extracted STEP, GLB in
metres and STL in metres. Downloads and every cache reuse check SHA-256. CAD
archives are never extracted to archive-supplied paths. Large downloads and
uncompressed members are bounded. Binary models, caches and SQLite indexes are
not committed. The FTS5 index rebuilds transactionally when dataset content changes.
Search combines full text with normalized/fuzzy MPN ranking, manufacturer/category
filters and scalar SI spec ranges. Unknown scalar specs do not satisfy a filter.

`local` sources are supported through file URLs for CLI use; keep machine-local
records in your personal bank. HTTP add accepts HTTP(S) sources only. A license
marked unspecified is not redistribution permission. Preserve the actual vendor
terms; private model sources and credentials must never enter the shared dataset.

## Geometry and catalog resolution

`catalog_component`/`catalog_aliases` link a record to existing catalog types.
Alternatively, set `model.params.bank_id` in the existing typed catalog model
block. The loader resolves a downloadable STEP source and its placement into a
`ModelRef`; connector conversion and the CAD builder use that same bank ID.

Validation records compare axis-specific bounding boxes with datasheet dimensions
(default tolerance 1.5 mm). A bbox match alone stays `unverified`: inspect real
holes, shafts and mounting faces, record `validation.key_features`, the reviewer
and date, and geometry-derived `interface_frames` keyed by native mate connector
names (`mc_mount`, etc.). `placement` expresses the model frame in component SI
coordinates. `r3 models validate` refreshes bbox measurements and preserves feature
checks; it never invents hole evidence. A false feature check or bbox discrepancy
emits `model_datasheet_mismatch` and blocks the CAD stage.

The purchased CAD path follows [Onshape’s documented native translation workflow](https://onshape-public.github.io/docs/api-adv/translation/): it imports a STEP through Onshape's native translation,
retains its rigid imported body, applies a standard Transform for frame alignment,
adds named standard mate connectors on reviewed geometry frames and applies the
catalog mass through part metadata. It never calls the envelope builder for a
bank-backed model. Multi-body vendor files must import as one rigid composite;
an ambiguous translation, unreviewed frame or feature quota failure stops the
build. Native Transform support currently covers translations; rotated imports
need a reviewed orientation before use. These guards are intentional evidence
requirements, not fallback permission to sculpt a replacement.

A validated Onshape public source can instead be inserted by immutable
`did/vid/eid/part_id`. To participate in the mated pipeline it must provide existing
`onshape_connectors` (name → native feature ID), reviewed `interface_frames` and
`validation.key_features.datasheet_mass: true`; the public version cannot be edited
by R3. Otherwise supply a downloadable STEP for the native import path. The separate
`insert_public` helper also supports insertion/placement of a whole Part Studio.

A purchased part with no exact available source remains a datasheet proxy, built
only with standard features, and carries `cad_purchased_proxy` with the searched
sources. Screws/nuts are included. Nothing turns a similar package or a bare GNSS
module into an exact breakout-board model. See [seed audit](model-bank-seed-audit.md)
for the current per-part status and source URLs.

## Interfaces

The workspace **Model bank** page provides search, a manual add form, provenance,
validation status and a GLB viewer after fetch. Parts sourcing candidates link to
matching bank models. CLI and HTTP use the same Python services as MCP tools
`r3_models_search`, `r3_models_add`, `r3_models_fetch`.

HTTP: `GET /api/models?q=...&category=...&manufacturer=...&specs=...`,
`GET /api/models/{id}`, `POST /api/models`, `POST /api/models/{id}/fetch`,
`POST /api/models/{id}/validate`, `POST /api/models/ingest/run`, and
`GET /api/models/{id}/assets/glb|stl`. Ingest JSON contains `provider` and resource
options (`url`, `query`, `name`, `mpn`, `category`). OpenAPI documents each route.

On a bank miss, search public Onshape versions, the official manufacturer STEP,
then permitted open databases/KiCad libraries, in that order. Add every find back
to the bank with provenance and checksum so the next robot reuses the evidence.
Record failed searches and an honest proxy reason when an exact model is absent.
Do not sculpt a bought component to hide a sourcing or validation gap.

The CAD generator digest includes bank source, validation and frame records, so a
new CAD run rebuilds when they change. The shared release fingerprint currently
covers robot.yaml and pinned lock revisions; the coordinator follow-up in
`.r3-agents/requests/modelbank.md` adds catalog/bank revisions for immediate release
staleness after dataset-only edits. Re-run CAD and validation after editing bank
evidence; an old release report is not new geometry evidence.
