# Model data bank: a searchable index of purchased-part 3D models (user request)

User: "There needs to be some sort of data block or dataset that a model can search, a search thing
that points to STEP files that I can add manually or procedurally... If it's something specific, let's
say a motor, it can just pull from the data bank. The data bank just points to the STEP file location,
like a URL to the download, so the model doesn't have to go searching for it."

Policy context: `.r3-agents/COMMON.md` "Purchased-part model policy". Bought parts are never
sculpted; they use real models. The bank is the first place every agent looks.

## Data bank (`packages/component-catalog/r3_catalog/modelbank/`)
- **Source of truth:** a git-tracked, human-editable dataset of entries, one record per model, as JSONL
  or a folder of YAML files. Each record has:
  - `id`, `name`, `manufacturer`, `mpn` (and aliases/variant MPNs), `category` (a controlled
    vocabulary: computer, camera, motor, gearbox, esc, servo, battery, gnss, imu, receiver, connector,
    wheel, fastener, …), `tags`;
  - `key_specs`: dimensions, mass, shaft, hole pattern, voltage, … in SI with units;
  - `sources[]`: the ranked locations of the model, each with
    - `kind`: `vendor_step` | `onshape_public` (did/vid/eid/partId) | `kicad_3d` | `database`
      (TraceParts, 3D ContentCentral, SnapEDA, …) | `local`;
    - `url` (direct download where possible), `format` (step / stp.zip / wrl / glb), `license`,
      `sha256`, `retrieved_at`, `requires_login: bool`;
  - `validation`: bbox vs datasheet, key features found, the date, and who validated it;
  - an optional `catalog_component` link (the R3 catalog type id it realizes).
- **Search index:** SQLite with FTS5 (Python stdlib `sqlite3`) built from the dataset, with full-text
  plus structured filters (category, spec ranges, manufacturer), ranked results and fuzzy MPN match.
  Rebuild it automatically when the dataset changes.
- **Manual add:** `r3 models add --name … --mpn … --category … --url <STEP url>` (or
  `--onshape <url>`). It validates, downloads to the cache, records the checksum, and appends a record. The
  web UI gets the same as a form.
- **Procedural add:** ingestors that populate the bank from authoritative sources, each a small
  plugin with recorded fixtures for tests:
  - the KiCad 3D model library installed locally (connectors, headers, packages), mapped to footprints;
  - official Raspberry Pi products (Product Information Portal STEP downloads);
  - Arducam board STEP downloads;
  - Pololu product CAD (motors, gearmotors, wheels, regulators);
  - u-blox / SparkFun / Adafruit breakout models where public (respect robots.txt and terms);
  - Onshape public-document search (REST, filtered to public) for named parts;
  - TraceParts / 3D ContentCentral only through permitted public access. Skip any source that
    requires login, but record it with `requires_login: true` as a pointer a human can fetch.
- **Fetch:** `r3 models fetch <id>` downloads to `~/.cache/r3/models/<sha>`, verifies the checksum,
  converts it to GLB for the web viewer, and STL for sim/wiring.
- **Interfaces:** CLI `r3 models search|show|add|fetch|ingest|validate`; MCP tools
  `r3_models_search`, `r3_models_add`, `r3_models_fetch`; API endpoints; a web "Model bank" page
  (search, preview in 3D, add form). Integrate it with the existing Parts/sourcing page, so a sourcing
  candidate shows its bank model.

## Using the bank in the pipeline
- **Catalog:** catalog components reference a bank `id`, and the catalog `model:` block resolves through
  the bank.
- **CAD:** the Onshape build uses the bank model for every purchased part. It uses Onshape Import of the
  STEP (a translation into a Part Studio), or inserts it directly from the `onshape_public` version. It
  adds named mate connectors on the real geometry and applies the datasheet mass override. Today
  `integrations/onshape/r3_onshape/parts.py` builds purchased parts as R3-modelled envelopes; replace
  that path. `r3_onshape/imports.py` (Import → Insert → place) exists but has never run live.
- **Validation:** a model whose bbox or key features disagree with the catalog datasheet gets a
  `model_datasheet_mismatch` diagnostic. A purchased part without a bank model gets a
  `cad_purchased_proxy` warning, listing the searched sources.
- **Agent workflow:** search the bank first. On a miss, search the sources above, then ADD the find to
  the bank (with provenance), so the bank grows. The outsider guide (`docs/guide/build-a-robot.md`) and
  the scaffolded project `AGENTS.md` say this explicitly.

## Seed the bank now
Add every purchased part in the RC car (`examples/robots/rc-car`) and in lane D's off-the-shelf catalog
(`packages/component-catalog/r3_catalog/data/components/offtheshelf_rc.yaml`, `camera.yaml`,
`raspberry_pi.yaml`):
- Raspberry Pi 5 4 GB (the official STEP, already pinned in the catalog);
- the Arducam B0445 / UC-717 camera;
- MPU-6050 breakout;
- u-blox GNSS modules;
- Traxxas Slash/Titan parts;
- the servo, ESC, receiver, LiPo, XT60/XT30, JST, and so on.
Report, per part, which source was found, with URLs, and which remain proxies and why.
