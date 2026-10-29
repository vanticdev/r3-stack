# Component catalog

Reserved for a small supported set of component descriptions: mechanical and
mass properties, connector pinouts, electrical ratings, software interfaces,
and references to behavioral models. Record provenance and model limitations.

Documentation only. No supported components or compatibility claims exist yet.
Catalog metadata must distinguish verified properties from estimates.

See [repository layout](../../docs/architecture/repository-layout.md).

## Sourcing (`r3_catalog.sourcing`)

`r3_catalog.sourcing` finds real parts for components declared by requirements (`source:` in
robot.yaml):
* providers for the Raspberry Pi PIP and ExpressLRS targets (keyless), Nexar, DigiKey and Mouser
  (keys from the environment), and agent web research;
* requirement matching;
* ranking (the orchestrator adds the twin checks);
* the build-up of a chosen candidate into `catalog/components/sourced_<id>.yaml`, with provenance per
  field (`datasheet`, `vendor` or `estimated`, plus a source URL).

See [sourcing](../../docs/architecture/sourcing.md).
