# Pi 5 stack: queue item 20

The four joins now clamp the official STEP's 1.276 mm laminate through four
purchased norelem 07522-0306030 DIN 988 shims (3 mm bore, 6 mm OD, 0.3 mm thick)
under the existing 11 mm male/female standoffs. The board and purchased header
are not modified. The catalog's approximate PCB/header solids now agree with
the measured vendor laminate and header heights.

All dimensions below are relative to the Pi PCB underside, in mm:

- PCB top: 1.276; shim top/standoff shoulder: 1.576.
- HAT underside: 1.576 + 11 = 12.576.
- Existing 8.5 mm socket mouth: 12.576 - 8.5 = 4.076.
- Official Pi header plastic top: 3.880; pin tip: 9.880.
- Plastic/socket gap: 0.196; pin insertion: 5.804 (required >= 5.5).
- Contact overlap past the modeled 1.1 mm socket contact recess: 4.704.
- Nominal pin-tip/cavity-ceiling clearance: 6.2 - 5.804 = 0.396.

The mechanical interface must measure insertion from the **socket mouth**.
The previous implementation measured from the header base and required the
mouth to coincide with it, which both overstated insertion and rejected a
legitimate seating gap. The three-line datum correction does not change any
fit, clearance, or insertion limit.

Offline recomposition: 0 errors. GPIO fit: 5.804 mm insertion, 0 lateral offset,
pins inside cavity, zero Pi/HAT boolean intersection. Removing the shims gives
0.104 mm plastic intrusion and 0.951 mm^3 intersection (negative test).
Assembly, model and connectors each have 0 errors. The mechanical stack tests,
assembly compiler tests and CAD fit tests: 36 passed.

The full CAD stage independently stops at the shared model bank's existing
`servo-digital-20kg` datasheet mismatch: measured 65.1917 x 48.1000 x 20.0391 mm
versus expected 20 x 54 x 45.5 mm (1.5 mm tolerance). This is not a Pi fit failure;
no check has been bypassed. The CAD/model-bank lane must resolve the purchased
servo model orientation/dimensions before the stage can validate the assembly.

Sources: official Raspberry Pi STEP RP-010083-CA-1 (pinned catalog SHA-256);
[norelem DIN 988 dimension table](https://norelem.co.uk/medias/07522-Datasheet-32778-Shim-washers-DIN-988-en.pdf)
for the shim; existing KiCad socket model for the contact recess and cavity.
[Harwin M20-7832042](https://www.harwin.com/products/M20-7832042) confirms the
40-contact, 2.54 mm pitch, 8.5 mm socket height and 0.64 mm square mating pin.
The generic KiCad socket's internal contact geometry remains a model assumption;
this is nominal fit evidence, not a manufacturer tolerance-stack certification.
