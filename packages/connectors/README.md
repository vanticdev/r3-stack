# Connectors and pins (`r3_connectors`)

Exact connector geometry for R3: pin maps, mating faces, wire entries, 3D models,
mated-pair checks and renders.

- `derive.py` builds `r3_catalog/data/connector_geometry.yaml`. Board-side parts and
  the XT60 halves come from the official KiCad library: the pads are read from the
  footprint through `pcbnew`, the bodies from the KiCad STEP model, and the mating
  faces were measured on those models. Cable-side housings with no free vendor CAD
  are datasheet proxies, labelled `proxy`.
- `board.py` reads the connector poses of a KiCad board (`pcbnew.LoadBoard`) in the
  catalog component frame.
- `pins.py` gives robot-frame connector frames, contacts and conductor exits. The
  wiring router uses it to start and end every wire on its pin.
- `models.py` resolves models from four sources: the KiCad library, a vendor STEP
  (sha256-pinned and cached in `$R3_MODEL_CACHE`), the KiCad stage's `board.step`,
  and proxies. It converts STEP to GLB with cascadio, and the output is deterministic.
- `stage.py` is the `connectors` pipeline stage, which writes `build/connectors/`.
- `render.py` and `views.py` make headless MuJoCo close-ups (`MUJOCO_GL=egl` on Linux).

```
python -m r3_connectors.derive --write        # regenerate the catalog geometry (needs KiCad)
python -m r3_connectors examples/robots/robot  # models, connectors.json, renders
```
