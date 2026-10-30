# Connection points and partial header mates

`ConnectionSite` is a derived catalog model. `Catalog.connection_sites(instance)`
computes its options from the current connector contact geometry and housing
catalog; sites are never authored per pin. No component id, connector name,
header length or odd/even numbering is built into the derivation. A regular
square-post header of any length can expose fitting cable housings. Keyed
connectors, integral leads and ports expose one `full` site.

A site contains:

- `id`: `full`, or `<housing-type>@<start-pin>`; stable within its connector.
- `start_pin`, `pins` and `pin_map`: covered component connector pins, with
  housing contact numbers mapped to their component connector counterparts.
- `frame`: `origin`, `x`, `z` in the **component frame**. The origin is on the
  mating face, x follows the contact row and z points toward the cable exit.
  The cable half has the same origin/x and opposite z.
- `envelope_min`, `envelope_max`: body bounds in the site's frame. Cable bodies
  extend toward +z. These are occupancy envelopes, not manufactured CAD.
- `compatible_types` and `blocks`: other option ids blocked by shared contacts
  or positive-volume body overlap. Touching faces alone do not block.

For each compatible housing the derivation translates its mated contact grid
onto each header pin and keeps the placements whose contacts all coincide with
header contacts. This uses actual KiCad pad-derived geometry, including row
spacing and numbering. There are 40, 38 and 36 single-row Dupont placements of
length 1, 2 and 3 on a 2×20 header, and 19 dual-row length-2 placements. Across
all N=1…20 there are 630 Dupont placements, plus the legacy full site and any
other fitting cable housings (including the existing servo crimps). Complete
housing placements merge into `full` rather than appearing twice; `at` with
pin 1 and plain `mates` select the same physical placement.

## Spec syntax

```yaml
connectors:
  B: {type: dupont-1x3, mates: pi.J8, at: "8"}
```

`at` names the component header pin receiving housing pin **1**, with the
header's x axis retained. This example maps housing pins 1/2/3 to Pi pins
8/10/12. A `dupont-2x2` starting at `"7"` covers 7/8/9/10. A dual-row housing
cannot start on the opposite row. Reversing a housing is not an implicitly
selected alternative.

Omitting `at` retains the existing full-connector `mates` semantics, pin n to
pin n, with the original wire-entry positions. `at` requires `mates` and a
component header. Direct component mates occupy the complete connector, so a
HAT on J8 blocks every smaller J8 site. Reciprocal declarations of the same
mate do not create a second occupation. Invalid mates do not reserve sites.

The graph emits stable errors:

| Code | Meaning |
| --- | --- |
| `connection_site_does_not_fit` | Housing runs beyond the header or cannot align at the start pin |
| `connection_site_pin_outside_header` | Start pin is absent from the header geometry |
| `connection_site_incompatible_type` | Type cannot use partial header sites, or target is not a component header |
| `connection_site_occupied` | This site is already occupied |
| `connection_site_blocked` | Another occupied site's contacts or body block it |

Existing full-mate type errors retain `connector_mate_mismatch`.

## Housings and provenance

`python -m r3_connectors.derive --write` regenerates `connector_geometry.yaml`
and the generated Dupont section of `connectors.yaml`. Contact grids are read
through KiCad's footprint API, never typed per pin. Generic 1×N and 2×N types
cover N=1…20; a type's `header_pitch` opts it into partial placement. Existing
cable types mated to headers also participate when their contact geometry
matches. `mates_with` continues to describe known complete connector mates.

Single-row occupancy uses the [Harwin M20-106 drawing](https://content.harwin.com/asset/b9496f9a-d89f-420f-aea9-abb4534be02f/DRG-00376-Technical-Drawing-Datasheet-M20-106-pdf.pdf):
nominal width 2.54N + 0.20 mm, thickness 2.50 mm, body length 14 mm. This means
adjacent end walls can block disjoint pin selections. Double-row dimensions
come from [Harwin M20-107](https://www.harwin.com/products/M20-1070300):
2.54N × 5.10 × 14 mm. The generic N>12 envelopes extrapolate those formulas and
are explicitly **estimated**; they do not assert a stocked Harwin part number.
Contact ratings and the wire-entry plane also require verification against the
chosen terminal. Nominal dimensions do not account for manufacturing tolerance.

The Harwin site requires TraceParts registration to download housing CAD. The
models attached to these generic types are explicitly labelled existing KiCad
**PCB socket contact-grid references**, not crimp housing CAD. The vendor body
envelope drives occupancy; no purchased housing was sculpted. A pinned real
vendor model can replace the reference without changing site derivation.

## Inventory and read APIs

The `connectors` stage writes `build/connectors/connection_points.json`, schema
`r3/connection-points@1`. Each `connection_points` entry includes a project-wide
`id` (`component.connector/site`), component/connector/site ids, pin-to-signal/net
maps, housing-to-header pin map, origin/x/exit direction in the robot frame,
body bounds in the site frame, compatible types, `blocks`, `occupied_by` and
`blocked_by` (`site` + occupant `by`). Status is `free`, `occupied` or `blocked`.
Unmapped component signals/nets are null; the physical contacts are still listed.

The inventory is rebuilt from the current loaded project and catalog each build;
component poses transform every site and its attached conductors together.
Floating harness-to-harness mates appear once per pair, at their solved mating
face centre when routing data exists, otherwise their authored centre. An
unplaced pair remains listed with null origin/direction and `placement: unplaced`.
An authored centre has `placement: authored` and null direction/reach until
routing supplies its orientation (`placement: routed`).

`free_access_reach` is the distance along the site's exit direction before a
centre ray hits another part's collision **envelope**. It uses existing CAD
AABBs, falling back to posed catalog AABBs, and excludes the owning component.
`access_blocker` and `access_geometry` identify the limiting part and source.
A start inside another envelope gives zero. Null means no envelope intersects
that infinite ray, or a floating pair is unplaced. This is a conservative access
estimate; it does not certify swept housing insertion clearance. Wire routes are
defined and checked separately by the wiring stage.

Agents can read fresh options, including before a connectors build:

```python
r3_connection_points(project="path/to/project", component="pi",
                     free_only=True, fits_type="dupont-1x3")
```

HTTP: `GET /api/projects/{pid}/connection-points` with the same optional
`component`, `free_only` and `fits_type` query parameters. Filters combine;
`free_only` excludes both occupied and blocked options. Both interfaces
recompute in memory, return graph diagnostics, and do not run a stage or write
project files. Their inventory is not read from a potentially stale artifact.
