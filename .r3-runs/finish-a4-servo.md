# DS3218 official source measurements

BOM remains **DSSERVO DS3218**, to be bought; no user measurement is required.
Authority: https://www.dsservo.com/down.asp?id=25, archive SHA256
`47c8ff34f7657c769ae645acb670d2eacd326f3600986931e70dcc4c6bf3ab8b`,
member `DS3218.stp`. Inspect ear height on receipt.

The six complete supplied solids are housing middle (0), bottom (1),
output gear/shaft (2), cable strain relief (3), housing top/ears (4), and
detachable horn (5). Retain 0,1,3,4 for the stationary housing and 2 for
the rotating output. The custom horn replaces accessory 5. No solid is
cut, scaled or sculpted. Source Y is the height axis; the case bottom
is Y = -20 mm.

Measurements from native Open CASCADE faces:

- Housing including strain relief: raw-axis bbox 62.196936 × 41.500000 ×
  20.039128 mm. This is not the bare-case datasheet envelope.
- Complete output gear/shaft: raw-axis bbox 17.300001 × 15.799999 ×
  17.300000 mm. Its top Y = 25.5 mm is 45.5 mm above the case bottom.
- Output journal: diameter 6.0 mm, Y = 17.8 to 21.6 mm (37.8 to 41.6 mm
  above the case bottom).
- Ear slot axes: source X = ±24.75 mm, Z = ±5 mm, diameter 4.5 mm.
  The ear surfaces have mould draft: underside Y spans approximately
  7.59–7.76 mm and upper surface 10.74–10.90 mm. The catalogue's old
  26.5 mm underside and 2.5 mm thickness were estimates, not measurements.
  At each slot axis the measured underside is **27.643739 mm**, top is
  **30.793155 mm**, and thickness is **3.149416 mm** above the case bottom.
- The source's actual 25T spline major diameter is 6.0 mm; tooth faces
  extend Y = 22.4 to 25.0 mm. The previous catalogue used 5.9 mm.

The lower-ranked public **DS3218MG** source (ccmcleod/robotic-arm-model
commit 537632cc026d0f8abf46315724a79684b44dad83, source SHA256
`a36b44b1d40239e6128a30887fb77b5713dc90d2a95d4503b320bab79faebeff`)
measures 54.674 × 45.385 × 20.200 mm overall, with flat ears at 27.7–30.9
mm and an output shaft top at 45.385 mm. It has a different label and
is not used.

Raw detailed measurements are in `/tmp/r3-a4-official-servo.log`.
The source selection and model-bank expected dimensions are updated;
assembly-interface reconciliation is still in progress.


## Source internals and mounting reconciliation

The source axes are retained (X long case, Y height, Z width), with translation
only. This avoids inventing a rotated purchased body. Geometry-derived interface
frames and the three mounting-height values are recorded in the model bank.
The BOM part number remains DS3218.

Native Open CASCADE common-volume measurements on the original, unmodified STEP
show solid 2 (output gear/shaft) overlaps solid 4 (upper housing) by
**1054.320808 mm³**; its common volume with solids 0, 1 and 3 is zero. The supplied
upper housing has no matching analytic bearing bore. This is a limitation of
using the manufacturer's presentation assembly to verify its hidden internal
bearing, not evidence of the wrong DS3218 external dimensions. No supplied solid
has been cut or reshaped to resolve it. An explicit policy clarification for
manufacturer-verified internal joints was requested; external mounting work
continues independently.

The isolated external-mount fixture now has **0 fit errors**: two aluminum posts
with drafted seats, two raised PA12 clamps with relief around the ear ribs,
and four M3x16 clamp screws. This is a fixture result, not a full assembly pass.
The model-bank/selection/interface tests report **19 passed**.

The final external horn trial raises its hub 0.5 mm clear of the housing, keeps
the ball-stud centers fixed, and adds 0.5 mm local underside bosses for the
6 mm stud threads. Its M3x12 retaining screw has 4.700 mm material-equivalent
engagement versus 4.500 mm required. CAD-plan and assembly regression tests:
22 passed. Hidden factory internals remain diagnosed separately.
