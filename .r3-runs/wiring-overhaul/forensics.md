# Why the review packet reported esc_motor -> knuckle_fl at -236.7 mm

Reproduce: `python .r3-runs/wiring-overhaul/forensics.py <project>` (needs `build/cad`).

## Cause 1 (the sign): nearest-sample-normal SDF, fixed in 85f2fb6

The packet's run (`result.json` inputs: commit `9c33004`) took the sign of a mesh
distance from the outward normal of the nearest surface sample. Seen from a far
point, the nearest point of a part is almost always an edge or corner, where the
samples of two faces coincide. When the KD-tree tie-break returns the face whose
plane the point is *behind*, the sign flips and a true distance is reported as a
penetration. Evaluated at every point the packet reported, on the archived
28-part CAD the packet ran against (`r3-stack/examples/robots/rc-car/build/cad`):

| harness / part | packet | legacy rule | winding number | exact (point-triangle) |
|---|---:|---:|---:|---:|
| esc_motor / knuckle_fl | -236.7 | -235.1 | +232.8 | +232.8 |
| power_y / knuckle_fl | -203.0 | -199.0 | +196.3 | +196.3 |
| battery_lead / knuckle_fl | -70.3 | -66.3 | +59.7 | +59.7 |
| servo_lead / gearbox | -181.1 | -179.4 | +179.4 | +179.4 |
| esc_motor / wheel_rr | -2.2 | -0.6 | -0.6 | -0.6 (real) |

(all 26 rows in `forensics_old_cad.txt`). The legacy rule reproduces the packet to
within 1-4 mm (the remainder is the 20 mm body-pose bug, also fixed in 85f2fb6).
24 of the 26 packet clearance errors are this sign artefact; the knuckle is ~200 mm
away at the front of the car. The meshes were fine: all 28 are watertight,
consistently wound, positive volume, volume = CAD volume (audit in the same file).

## Cause 2 (the magnitudes): stale CAD

The packet routed against a 28-part CAD from an older design: knuckle_fl bbox
x 245-275 mm (now 229-268), motor y -60..-10 mm (now -42..20), 28 parts (now 119).
That shifts every distance by 10-30 mm but does not change the sign. The current
119-part CAD is itself one robot.yaml revision behind (`cad_stale`:
CAD from robot.yaml `e52de1e4...`, current `e149f840...`).

## What prevents a repeat

- `r3_wiring.witness`: every clearance finding is re-measured with exact
  point-to-triangle distance + winding sign and with MuJoCo `mj_geomDistance` on
  the part's convex pieces; nearest-point witnesses are attached; disagreement ->
  `geometry_uncertain` instead of an error.
- CAD audit (`cad_mesh_invalid`, `cad_stale`) on every mesh the stage loads.
- Fixtures (`tests/test_wiring_witness.py`): analytic box/cylinder distances
  inside/outside, rotated/translated meshes, inward winding, open meshes, mm-vs-m
  units, misplaced meshes, swept shapes, MuJoCo agreement, and the legacy sign flip.
