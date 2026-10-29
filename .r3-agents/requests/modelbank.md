# Model-bank integration notes for the coordinator

The model-bank brief explicitly requires catalog resolution and purchased CAD integration.
The small `load_catalog()` hook resolves the existing `ModelRef` through the new
`r3_catalog.modelbank` package; it uses `model.params.bank_id` without changing the
shared catalog schema or any component YAML/robot.yaml. Onshape edits add purchased
model metadata and dispatch to `purchased.py`; fabricated geometry is unchanged.

Lane A: the downloaded official Raspberry Pi 5 STEP has tessellated extents
88.500 x 57.630 x 18.976 mm. The catalog uses 87 x 56 x 17.6 mm. At the existing
1.5 mm tolerance the Y extent disagrees, so `model_datasheet_mismatch` is emitted.
Do not widen the tolerance: decide whether the catalog envelope includes connector
protrusions and correct the dimensions with vendor/geometry evidence. Mounting
holes and `interface_frames` also need independent review before a model may be
used in the native CAD builder. The source itself is real, downloaded and pinned.

No Onshape API-key credentials are configured in this worktree, so no live
translation/import/assembly endpoint probe was possible. Offline transport
contracts exercise the import/insert/mass/mate path. Do not claim a live CAD proof.

Shared model contract follow-up: `Project.fingerprint()` does not fingerprint
catalog/model-bank content, and release staleness only reads robot.yaml and lock
revision keys. The purchased CAD generator digest includes the complete resolved
bank record, so re-running CAD correctly invalidates its mesh cache. Please add a
model-bank/catalog revision to the shared project fingerprint so editing bank
validation evidence also marks already stored release evidence stale immediately.
This branch leaves `r3_model` unchanged per COMMON.md's ownership rule.
