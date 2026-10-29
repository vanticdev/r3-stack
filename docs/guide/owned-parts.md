# Parts you already own

Mark a component in `robot.yaml` as owned:

```yaml
components:
  pi: {type: raspberry-pi-5-4gb, owned: true}
  camera:
    type: arducam-b0445-imx296
    owned: {note: "Already on the workbench"}
```

Use the catalog ID of your actual hardware. Owned parts remain fully modelled in
CAD, electrical checks and simulation. They appear as **Owned** in the Parts page
and purchase BOM, contribute zero to the to-buy total, and are never searched by
sourcing providers. Omit `owned` (or set it to false) for parts to buy. An owned
component cannot also have a `source:` block (`owned_source_conflict`).

The CLI/MCP sourcing listing, HTTP sourcing endpoint, report and release output
include `purchases`: per-component rows, currency-separated known to-buy totals,
and the number of unpriced parts. Unknown prices are not estimates or zeroes;
these totals are partial until every to-buy part has a price. Harness wire,
cable-side connectors and consumables remain purchases even when the connected
component is owned. Owning a board does not imply owning its mating harness.
