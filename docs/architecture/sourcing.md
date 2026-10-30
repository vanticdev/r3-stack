# Spec-based part sourcing (`r3 source`)

A component can be declared by **requirements** instead of (or in addition to) a fixed catalog `type`.
R3 finds real, purchasable parts online, ranks them with the digital twin's checks, and turns the chosen
one into a project catalog entry with provenance per field. The choice is pinned in `r3.lock.yaml`,
and the pipeline then runs unchanged.

```yaml
components:
  pi:
    type: raspberry-pi-5-8gb          # optional: used until a sourced part is pinned
    mount: panel
    source:
      requires: {kind: computer, family: raspberry-pi-5, ram: ">= 4 GB"}
  receiver:
    source:                           # no type: the model stage reports component_unsourced until pinned
      requires: {kind: radio, protocol: crsf, band: 2.4 GHz, supply: [4.5 V, 5.5 V]}
  sensor:
    type: wheel-110x45
    source: &wheel
      group: sample_pair             # one choice and one lock entry for both wheels
      requires: {kind: wheel, diameter: [100 mm, 115 mm], width: [40 mm, 50 mm], hub: 12 mm hex}
      prefer: {price: low}
      budget: 40 USD                  # total for `quantity` (default: the components in the group)
  sample_b: {type: wheel-110x45, source: *wheel}
```

Requirement values (`r3_catalog.sourcing.quantities`):
* a quantity (`2.4 GHz`), matched within 2 %;
* a range `[min, max]`. An operating range (supply) must *cover* it; a single value must lie inside it;
* a bound (`">= 4 GB"`);
* a tagged size (`12 mm hex`);
* a string, or a list of strings.

`kind` is the catalog kind. `prefer` takes `price: low`, `mass: low`, `vendor: <name>`, or a parameter
target. `providers` restricts the search.

## Pipeline

```
robot.yaml source: ──▶ providers ──▶ candidates (value + URL + datasheet|vendor|estimated per parameter,
                                          offers, 3D models)
                    ──▶ ranking: hard filters → twin checks → score
                    ──▶ build/sourcing/<slot>/candidates.json + ranking.png        (search: no project change)
choose N ──▶ catalog/components/sourced_<id>.yaml (+ sourced: provenance block)
         ──▶ r3.lock.yaml sourcing.<slot>: {catalog_id, vendor, url, mpn, price, retrieved_at, digest, ...}
Project.load ──▶ pinned slots resolve to the pinned catalog id; the model stage verifies the pins
```

### Providers (`packages/component-catalog/r3_catalog/sourcing/providers/`)

| id | Source | Keys | Returns |
| --- | --- | --- | --- |
| `raspberrypi` | Raspberry Pi Product Information Portal (`pip.raspberrypi.com`): sitemap → product category → product brief, mechanical drawing, official STEP | none | One candidate per RAM option: RAM, 5 V / 5 A input, list price, operating temperature, processor (product brief); outline and hole pattern verified against the drawing; the STEP model |
| `expresslrs` | Official `ExpressLRS/targets` (`targets.json` + `RX/<layout>.json`) | none | Every standalone receiver of the band: UART, CRSF, RF power table, PWM outputs, diversity, platform. Receivers integrated in an FC, AIO or VTX are skipped. Supply is an explicit estimate (not in the definitions) |
| `nexar` | Nexar / Octopart GraphQL (`supSearch`, `supSearchMpn`) | `NEXAR_CLIENT_ID`, `NEXAR_CLIENT_SECRET` | Specs, authorized sellers, stock, price breaks, lead time |
| `digikey` | DigiKey Product Information API v4 | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` | Parameters, pricing per variation, stock, lead weeks |
| `mouser` | Mouser Search API v1 | `MOUSER_API_KEY` | Attributes, price breaks, stock, lead time |
| `research` | Headless Claude Code (`claude -p --json-schema`, WebSearch/WebFetch only) or Codex (`codex --search exec --output-schema`, read-only) | the user's CLI login | Long-tail hobby parts (wheels, servos, ESCs, motors, ...) with a cited URL and quote per parameter |

* Without a key (or the CLI), a provider is skipped with `sourcing_provider_key_missing` /
  `sourcing_provider_cli_missing`. It never fails the search.
* GNSS modules have no keyless structured manufacturer source:
  * u-blox has no machine-readable index, and its `robots.txt` disallows its product-resources pages;
  * Adafruit's product API is disallowed for crawlers by its `robots.txt`.

  GNSS modules therefore come from the distributor APIs and the research provider.

**HTTP** (`sourcing/http.py`):
* Every response is cached in `$R3_SOURCING_CACHE` (default `~/.cache/r3/sourcing`).
* Pages are fetched only if `robots.txt` allows them; documented APIs are exempt.
* Nothing behind a login is fetched.
* Offline mode (`--offline`, `R3_SOURCING_OFFLINE=1`) serves only the cache.
* Keys and secrets are redacted from cache keys and recordings.
* PDFs are read with `pypdf`. Only their extracted text is cached, because a product brief can be 50 MB.

**Research validation.** R3 validates the agent's JSON against the schema and the `Candidate` model.
It then marks a parameter `estimated` when it:
* has no http(s) citation or no quote;
* is called an estimate by the agent;
* has a value that cannot be found on the cited page (the page is fetched through the same cache and
  robots policy).

A page that cannot be fetched is noted, not penalised.

### Ranking (`sourcing/ranking.py`, twin checks in `r3_orchestrator/sourcing.py`)

1. **Hard filters.** Each requirement passes, fails or is unknown.
   * Any failure, or a budget overrun, makes the candidate `rejected`.
   * An unknown requirement makes it `unverified`.
   * Otherwise it is `eligible`.
2. **Twin checks**, for the 30 best non-rejected candidates. The candidate's built catalog entry is
   substituted into the robot, and the following run:
   * the model stage's checker: connectors, bindings, pin roles, rail and logic voltages, I2C,
     envelope interference, mass budget;
   * the assembly compiler, when the spec has `assembly:`;
   * an interface-feature comparison with the slot's template part. Example: a 12 mm hex wheel hub
     replaces the 6 mm bore that `hub_fl` runs on.

   Only findings the current part does not already produce count against a candidate.
3. **Score.** It starts at 100:
   * −30 per new twin error, −8 per new twin warning;
   * −12 per required parameter without a value;
   * −8 per required parameter that is only an estimate, −1 per other estimate (at most −5);
   * up to −15 for the price premium over the cheapest part in the tier;
   * stock and lead-time penalties;
   * preferences;
   * +2 for the slot's current part, which breaks ties in favour of a working design.

### Build-up (`sourcing/build.py`)

The entry's contract comes from a **template**:
* the slot's current catalog part, or
* the catalog part of the same kind that provides every connector the robot's harnesses, direct mates
  and bindings reference.

This contract covers connector names, pin maps and mounting features. The candidate's sourced values
then overwrite what they describe:
* envelope and mass;
* hub feature;
* supply range on the `power_in` pins;
* protocol, band and RF power;
* board outline and hole pattern;
* input voltage and current.

Template values inherited for a *different* product are downgraded to `estimated`. The mass budget
reads `mechanical.provenance`, which follows the mass source. The file's `sourced:` block (ignored by
the catalog loader) records:
* the candidate, its offers and its models;
* the requirement matches;
* the twin results;
* `fields`: provenance per catalog field path.

### Lockfile and model-stage checks

`r3.lock.yaml` `sourcing.<slot>` pins:
* `catalog_id`, `catalog_file`, `digest` (sha256 of the generated file);
* `provider`, `candidate_id`, `name`, `manufacturer`, `mpn`, `vendor`, `url`;
* `price` (amount, currency, quantity) and `retrieved_at`;
* `requirement_sha256`, `components`, and `cad` (`vendor_step` | `missing`).

Loading a project never re-sources a slot. The model stage reports:
* `component_unsourced`: a slot with no `type` and no pin;
* `sourcing_catalog_missing` and `sourcing_digest_mismatch`: the sourced file was deleted or edited;
* `sourcing_pin_stale`: the requirements changed after pinning;
* `source_requirement_unmet`: an authored `type` or a pinned part violates its own requirements;
* `source_requirement_unverified`;
* `sourcing_estimated_parameters` (info).

The project fingerprint gains `sourcing_sha256`.

## CAD of sourced parts

Search the model bank first and retain vendor model provenance. The agent imports the
exact STEP in Onshape with onshape-native, adds real mate connectors, and inserts and
mates the part in the native assembly. Catalog dimensions are sourcing metadata and
cannot create geometry. Missing vendor geometry remains missing until modeled or imported
in Onshape. Verify the actual geometry and viewport, pin a version, and run `r3 cad pull`.
The local copy is immutable and all physical qualification uses that pull.

## Interfaces

* **CLI:**
  * `r3 source` lists the slots and providers;
  * `r3 source <component> [--provider ID] [--offline] [--refresh]` searches;
  * `--choose N|ID` pins a candidate;
  * `r3 source --all [--choose 1]` handles every slot; already-pinned slots are skipped (re-source
    them explicitly by name);
  * `--unpin`, `--bom` (prices the PCB BOM MPNs through the distributors).
* **MCP:**
  * `r3_source_slots` (read);
  * `r3_source_search` (design tier, open world; writes only `build/sourcing/`);
  * `r3_source_choose` (edit tier).
* **HTTP:** see [api.md](api.md). Searches are SSE jobs.
* **Web:** the *Parts* page (`apps/web/src/pages/PartsPage.tsx`) shows:
  * the slots and provider availability;
  * a live search log;
  * a ranked candidate table with tier, score breakdown, price, stock and twin result;
  * per candidate: the requirement matches, the parameters with provenance chips and source links,
    the twin checks, offers and models;
  * a *Choose* button.

## Limitations

* The distributor providers are tested against cassettes built from each API's documented response
  schema. No keys were available to record live traffic.
* The Raspberry Pi drawing is used to *verify* the B-series dimensions against the drawing's labels,
  not to measure them. pypdf extracts the labels as text without geometry. Component heights and mass
  are template estimates.
* The Onshape STEP import and mate-connector requests are tested against a fake transport. They have
  not run against a live Onshape account.
* The twin checks are the model stage's static checks with catalog envelopes. The CAD fit stage,
  wiring, electrical simulation and sim still run after the part is chosen.
