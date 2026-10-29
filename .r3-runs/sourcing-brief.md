# Spec-based part sourcing from online sources (user request)

User: "somehow, i want some parts to be sourced from online ... like the wheels and radio and pi5,
those are all specification based."

Goal: a robot can declare a component SLOT by requirements, not only a fixed catalog id. R3 then
finds real purchasable parts online, ranks them with the digital twin's checks, and turns the chosen
one into a catalog entry with full provenance, and the vendor's 3D model where available. Then the
pipeline runs unchanged. This must work headlessly on a server, for an AI agent through MCP, and from
the web UI.

## Spec
- `robot.yaml`: a component may carry `source: {requires: {...}, prefer: {...}, quantity, budget}`
  instead of, or in addition to, `type`. Examples:
  - `wheel: {requires: {kind: wheel, diameter: [100 mm, 115 mm], width: [40 mm, 50 mm], hub: 12 mm hex}}`
  - `receiver: {requires: {kind: radio, protocol: crsf, band: 2.4 GHz, supply: [4.5 V, 5.5 V]}}`
  - `pi: {requires: {kind: computer, family: raspberry-pi-5, ram: ">= 4 GB"}}`
  The resolved choice is pinned in `r3.lock.yaml` (`sourcing.<component>: {catalog_id, vendor, url,
  mpn, price, retrieved_at, digest}`), so builds stay reproducible. Re-sourcing is an explicit command.
- `packages/component-catalog/r3_catalog/sourcing/`:
  - **Provider interface:** `search(requirement) -> list[Candidate]`. A candidate carries its
    parameters, each with a provenance URL and a `datasheet|vendor|estimated` status, plus price,
    stock, URLs and 3D-model URLs.
  - **Providers:**
    1. **Electronic distributors with official APIs:** Nexar/Octopart (GraphQL), DigiKey API v4,
       Mouser Search API. Keys come from the environment (`NEXAR_CLIENT_ID/SECRET`,
       `DIGIKEY_CLIENT_ID/SECRET`, `MOUSER_API_KEY`); without a key the provider is skipped with a
       clear diagnostic. Use the T3 `request_secret` tool, if your environment has it, ONLY to ask the
       user whether they want to supply a key; never invent keys. The PCB BOM's MPNs can be priced
       and checked for stock through these.
    2. **Manufacturer/vendor adapters with stable structured data:** the official Raspberry Pi product
       pages and datasheets (Pi 5 dimensions, power, and its official STEP/mechanical drawings); the
       ExpressLRS hardware target definitions (the official `ExpressLRS/targets` JSON on GitHub,
       which lists receivers and their UART and power parameters); and similar for GNSS modules.
       Use HTTP with caching, respecting robots.txt and terms. No scraping behind logins.
    3. **Agent-assisted web research** for long-tail hobby parts (wheels, servos, ESCs, motors):
       a provider that runs the R3 agent runner (`r3_orchestrator.api` agent sessions, Claude/Codex
       headless) with a strict JSON schema for candidates. It must cite a URL for every parameter;
       R3 validates the schema and marks unsupported values `estimated`.
  - **Ranking:** hard requirement filters, then a score built from twin checks. Run `r3_model`
    graph/pin voltage/current compatibility against the robot's nets. Check the mechanical envelope
    against the robot's assembly interfaces (the compiler in `r3_model/assembly.py`). Weigh mass
    budget, price, stock and lead time, and penalise estimated parameters.
  - **Build-up:** the chosen candidate becomes `catalog/components/sourced_<id>.yaml` in the project,
    with interfaces (when the 3D model or the drawings define them) and provenance per field.
- **CAD of sourced parts, the human way:** download the vendor STEP when available and import it into
  the project's Onshape Parts Library with Onshape's standard import (translation API; headless; no
  FeatureScript), then add mate connectors at the interfaces. With no model, generate a parametric
  proxy with standard features, labelled `proxy`.
- **Interfaces:**
  - CLI: `r3 source <component> [--provider ...] [--choose N]` and `r3 source --all`.
  - MCP: `r3_source_search`, `r3_source_choose`.
  - API: `GET /api/projects/{pid}/sourcing`, `POST .../sourcing/{component}/search` (as an SSE job),
    `POST .../sourcing/{component}/choose`.
  - UI: a "Parts" page in `apps/web` listing slots, candidates with parameters, provenance links,
    price and the check results, plus a choose button. Coordinate with the existing web code:
    keep it additive.
- **Tests:** recorded HTTP fixtures for every provider (no live network in tests), ranking logic,
  lockfile pinning, catalog generation, and an end-to-end run with fixtures that sources the RC car's
  wheel, receiver and Pi 5 and passes the model stage.
- **Live proof (allowed, small):** run the keyless providers live once (the Raspberry Pi and ELRS
  targets), and report the candidates found for the Pi 5 and an ELRS 2.4 GHz CRSF receiver.
