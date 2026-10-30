# Part-accuracy integration handoff

`r3/part-accuracy` adds fresh, general mesh mating checks in the connectors stage.
It does not change connection-site code, routing code or the mechanically owned
`rc_car.yaml`. Schema additions: connector-instance `provenance` (default
estimated) and `ConnectorGeometry.pin_shape: pad`. Existing connector names J8,
CAM0, CAM1 and imu.J1 stay intact. New connector types/pin maps are in the catalog.

The only `robot.yaml` edit is imu_lead.B's type from `integral` to
`solder-wire-1x8-2.54`, in a separate commit. Its existing to-pins 1,2,4,3 are
already VCC,GND,SDA,SCL in the corrected eight-pad row.

Mechanical lane action is required; checks were not weakened:

- Pi header connector origin (-10,24.5,3.880) → (-10,24.5,3.876) mm.
  Its assembly-interface Z changes 4.100 → 3.876 mm. The joined HAT moves down
  0.224 mm, exposing four standoff loop errors against the unchanged 0.050 mm
  limit. Reconcile the standoff stack; do not move the measured connector back.
- GY-521 nominal mounting holes are now ±7.75, +5.825 mm, Ø3, PCB top Z=1 mm,
  explicitly estimated pending the owned board's calipers. The imported STEP has
  ±8.175 mm holes instead. The existing IMU mount differs by 0.358 mm against a
  0.250 mm fit allowance. Do not fabricate from either unconfirmed pattern.
- `test_cad_phaseb::test_rc_car_compiles_with_every_object_joined` and
  `test_exact_interference_and_seats_are_clean` expose these assembly defects.
  They remain unchanged. No skips/xfails/tolerance changes conceal them.
- Identical recomposition commands report 0 CAD errors on both original base
  `13dc2ff` and current integration `c8802ea`, versus 17 here: all 17 are newly
  introduced by the accuracy data changes. The audit lists each one, distinguishing
  10 HAT/Pi/standoff findings, 6 IMU findings and 1 aggregate retention finding.
  Fresh wiring adds routing/support/bend and
  dynamic-clearance failures after the endpoint moves. Detailed before/after
  codes and per-wire messages are in `.r3-runs/part-accuracy-audit.md`.

The follow-up corrects the non-embedded model lookup. `hat.J_BATT` uses a separate
`xt30pw-male` proxy; `motor.J_POS` and `motor.J_NEG` use separate `bullet-3.5-male`
proxies. Their previous `connector_empty` errors were artefacts, now correctly
`connector_unverifiable`. Non-embedded sourced models are searched alongside
component geometry; true empty models remain blocking errors.

Corrected pair totals: **1 passed, 1 failed, 23 unverifiable**. The remaining
failure is actual housing-material overlap in the supplied floating XT60 models,
isolated to the central key/rib. Contact insertion passes (7.25 ≥ 6.30 mm).
Closed housing intersection is 7.468 mm³ in the robot frame; an independent
millimetre/local-coordinate Boolean and planar sections confirm the collision.
Legitimate hollow housing nesting is covered by a passing regression fixture.

Pi J8 has 40/40 physical contacts and passes the 5.5 mm insertion gate; open model
surfaces leave the housing completeness unverified. The four active GY-521 lead
ends land on their measured pads. USB/HDMI numbering/mating-contact datums and the
UC-717 FPC position remain explicitly estimated where exact supplier evidence
could not be obtained. See `docs/guide/part-accuracy.md` for sources and the full
caliper checklist, including GY-521 sensor pin-1/axis orientation.

Implementation commits: `3bf3444`, `baf7103`; isolated harness edit: `c6ef763`.
Final full validation: original base **734 passed**; current integration snapshot
**778 passed**; this branch **784 passed, 2 failed, 8 warnings** (786 collected).
Only the two unchanged mechanical cleanliness tests above fail. Targeted mesh
regressions: **22 passed**. Final model/wiring/connectors counts are **0/0**, **82/55**,
and **1/51** errors/warnings respectively. The follow-up changes only three empty
errors into three unverifiable warnings; all other stage counts are unchanged.

Generality follow-up: no computer-kind or robot/component-ID gate remains.
Solder mates use catalog metadata instead of a type-name prefix; embedded posts
accept arbitrary pin identifiers. Custom actuator/sensor fixtures and the other
synthetic mesh cases pass (22 tests). Unrecognized geometry remains explicitly
unverifiable for any robot rather than receiving an inferred pass.
