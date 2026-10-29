# r3_fab: 3D printing as a manufacturing process

Stage `fabrication` (`r3_fab.stage.run(project) -> StageResult`). It covers every
fabricated component whose material is an FDM filament and writes its artifacts
under `build/fabrication/`.

```
build/fabrication/plan.json             material, printer, orientation, support area, height,
                                         print mass, filament length, checks, strength, files
build/fabrication/preview.png           each part type in its suggested print orientation
build/fabrication/<cid>/<cid>.stl|.3mf  print-ready mesh (mm, oriented, centred on the bed, z = 0)
build/fabrication/<cid>/<cid>_stress.png von Mises stress map of the worst load case
```

## Inputs

- `robot.yaml` `fabrication:` (`r3_model.spec.Fabrication`): `printer` (a
  `printers.yaml` id, default `bambu-x1c`), `material` (the default FDM material
  for parts whose catalog material is empty or generic, e.g. "3D printed"),
  per-part `parts.<cid>` overrides (`material`, `orientation: [rx, ry, rz]` deg
  or `auto`, `infill`, `perimeters`, `print: false`), `load_cases`,
  `min_safety_factor`, `max_overhang_deg` and `min_wall`.
- Catalog: `materials.yaml` (TDS values with sources), `printers.yaml`,
  `print_fits.yaml` (FDM clearances), `fasteners.yaml`.
- Compiled assembly (`project.assembly`): joins, derived fasteners and poses.
- CAD (optional): `build/cad/meshes/<cid>.stl`. The CAD stage writes these in
  the robot frame and in metres. They are moved back into the component frame
  with the compiled pose.

Material resolution uses whole words, so "plastic" is not PLA. Precedence:
`fabrication.parts.<cid>.material`, then the catalog `mechanical.material`,
then `fabrication.material`. Fabricated parts in metal, SLS nylon or other
materials are listed once as `not_printed`.

## What it checks

| Code | Severity | Meaning |
| --- | --- | --- |
| `not_applicable` | info | no part is FDM printed |
| `printer_unknown` | error | `fabrication.printer` is not in `printers.yaml` |
| `print_geometry_estimated` | warning | no usable CAD mesh; geometry built from the catalog solids (manifold3d) |
| `print_bed_exceeded` | error | the oriented part does not fit the build volume (z rotations tried in 5° steps) |
| `print_overhang` | warning | support area in the chosen orientation (faces steeper than `max_overhang_deg` from vertical) |
| `print_wall_thin` | error / warning | ray-cast wall thickness below 2 × nozzle (error) or `min_wall` (warning) |
| `printed_tapped_hole` | warning | a screw threads into printed plastic; heat-set insert hole, depth and boss from the CNC Kitchen table |
| `heat_set_insert` | info | the fastener chain already uses a heat-set insert |
| `printed_male_thread` | warning | a nut runs on a printed thread |
| `nut_trap` / `nut_trap_too_shallow` | info / warning | captive-nut pocket size, and whether the part is thick enough to hold it |
| `printed_counterbore` | info | screw head on a printed part: counterbore = head + 2 × cap clearance |
| `printed_hole_clearance` | warning | printed clearance hole outside the FDM print-fit range |
| `print_material_printer_mismatch` | warning | material needs an enclosure, bed or nozzle temperature the printer lacks |
| `print_strength_ok` / `_margin_low` / `_insufficient` | info / warning / error | safety factor on printed load paths (≥ `min_safety_factor` / < it / < 1) |
| `print_strength_not_applicable` | info | no support (no join leads to a wheel) or no carried mass |
| `print_strength_resolution_estimated` | warning | the voxel model misses more than 15 % of the part volume |
| `print_infill_strength_estimated` | warning | infill below 100 %; the strengths are 100 % infill TDS values |

## Orientation

The candidates are the six axis-aligned rest poses plus the most probable
`trimesh` stable poses. The score adds the support area, the height and a strength
penalty, and subtracts bed contact. The penalty is +100 when the part misses the
required safety factor in that build direction and up to +5 when the main stress
runs across layers. A per-part `orientation` overrides the choice.

## Strength

The part is meshed as voxel hexahedra in its own frame and solved with
scikit-fem linear elasticity, using 20-node serendipity `ElementHexS2` elements.
The element matrices are assembled once and scattered over the elements. The
solver is SuperLU for small systems and pyamg smoothed-aggregation CG for large
ones, with a grid shift and a SuperLU fallback when CG stalls. The join graph
gives the supports and loads. A join whose far side (this part removed) reaches
a wheel is a fixed patch on the interface. Any other join carries the mass on
its far side times the load-case acceleration. The part's own weight is a body
force.

Stresses are recovered at the element corners and skip corners inside support
or load patches, whose rigid edges are singular. The Z (interlayer) strength
applies where the major principal stress is within 45° of the build direction.
Accuracy against beam theory is in `tests/test_fabrication_strength.py`: a
single layer deflects about 97 % of Euler–Bernoulli, and the root stress is
within 10 %. The default load cases (5 g bump, 10 g frontal crash) are labelled
estimates.

## Run

```
r3 validate --stage assembly,fabrication         # from the project directory (orchestrator stage registry)
.venv/bin/python -m pytest tests/test_fabrication_*.py
```
