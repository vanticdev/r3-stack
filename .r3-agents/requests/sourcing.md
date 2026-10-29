# Sourcing (branch r3/sourcing): contract changes and merge notes

## Additive contract changes made (allowed by the brief)
- `r3_model/spec.py`:
  - new `SourceSpec`;
  - `ComponentInstance.source`;
  - `ComponentInstance.type` now defaults to `""` (a component needs `type` or `source`);
  - `RobotSpec.source_slots()`;
  - components sharing a `source.group` must declare the same `requires`/`prefer`.

  Existing specs validate unchanged.
- `r3_model/project.py`:
  - `Project.apply_sourcing()` (called by `load`) resolves pinned slots from `r3.lock.yaml`
    `sourcing.<slot>.catalog_id`;
  - `fingerprint()` adds `sourcing_sha256` when slots are pinned.
- `r3_model/graph.py`:
  - `component_unsourced` for a source-only component without a pin;
  - a mate to a component whose type is unknown no longer raises, and is reported once.
- `r3_catalog/schema.py`: `ProvenanceStatus` gains `vendor` (a value from a seller's listing).
- `r3_orchestrator/model_stage.py` runs `sourcing.check_pins` when the spec has `source:` slots.

## Web app (`apps/web`, branch r3/web): route registration to apply at merge
The page and its API module are self-contained new files: `src/pages/PartsPage.tsx` and
`src/api/sourcing.ts`. Registering them is 3 lines. They were verified with `tsc -b` and `vite build`
on r3/web b95d9ce:

```diff
--- a/apps/web/src/App.tsx
+++ b/apps/web/src/App.tsx
 import { DeployPage } from "./pages/DeployPage.tsx";
+import { PartsPage } from "./pages/PartsPage.tsx";
@@
           { path: "software", element: <SoftwarePage /> },
+          { path: "parts", element: <PartsPage /> },
--- a/apps/web/src/components/Layout.tsx
+++ b/apps/web/src/components/Layout.tsx
   { to: "software", label: "Software", icon: "code" },
+  { to: "parts", label: "Parts", icon: "target" },
```

The dev mock layer (`apps/web/mock/`) has no sourcing routes. With the mock, the page shows the API
error. Against `r3 serve`, it works.

## Suggested follow-ups (not done here: robot.yaml is owned by the coordinator)
- Add `source:` blocks to `examples/robots/rc-car/robot.yaml` for `pi`, `receiver` and the front
  wheels, in addition to their `type`. `tests/test_sourcing_pipeline.py` does this on a copy.
  Caveat: with `hub: 12 mm hex` (the brief's example), the current 6 mm plain-bore front wheels fail
  `source_requirement_unmet` until a hex wheel is pinned. A hex wheel then needs a hub adapter, which
  the twin check reports as `sourcing_interface_feature_changed` on `hub_fl`/`hub_fr`. For the
  current stub axles, use `hub: 6 mm bore`.
- New dependencies: `pypdf` and `jsonschema`. They are in the pyproject `sourcing` extra and in
  `docker/r3-worker/requirements.txt`. `pypdf` was installed into the shared venv.
