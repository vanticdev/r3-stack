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
- Recomposition reports 17 CAD errors. Fresh wiring adds routing/support/bend and
  dynamic-clearance failures after the endpoint moves. Detailed before/after
  codes and per-wire messages are in `.r3-runs/part-accuracy-audit.md`.

The general mesh check finds no HAT battery connector geometry at its declared
mating volume, no resolved bullet contacts at the motor's two terminals, and
closed-solid interference in the floating XT60 pair. These parts are outside
this lane's correction scope. Many other bought parts/plug halves have no
sourced contact mesh; each is named as `connector_unverifiable`.

Pi J8 has 40/40 physical contacts and passes the 5.5 mm insertion gate; open model
surfaces leave the housing completeness unverified. The four active GY-521 lead
ends land on their measured pads. USB/HDMI numbering/mating-contact datums and the
UC-717 FPC position remain explicitly estimated where exact supplier evidence
could not be obtained. See `docs/guide/part-accuracy.md` for sources and the full
caliper checklist, including GY-521 sensor pin-1/axis orientation.

Implementation commits: `3bf3444`, `baf7103`; isolated harness edit: `c6ef763`.
Full validation: 734 passed before; 775 passed / 3 failed after. The obsolete FPC
type expectation was corrected and its whole 26-test file passed on rerun. The
two mechanical cleanliness failures above remain for the mechanical lane.
Final rerun of the three failures: 1 passed, 2 failed; only those two mechanical tests remain.
