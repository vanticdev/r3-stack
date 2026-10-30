# Pi 5 stack: queue item 20

The four joins now clamp the official STEP's 1.276 mm laminate through four
purchased norelem 07522-0306030 DIN 988 shims (3 mm bore, 6 mm OD, 0.3 mm thick)
under the existing 11 mm male/female standoffs. The board and purchased header
are not modified. The vendor STEP datum is 0.030 mm below the laminate underside; the lower
mount interfaces preserve that datum. The compiler consequently places the Pi
0.030 mm lower, while the shim and HAT heights remain unchanged.

All dimensions below are relative to the Pi PCB underside, in mm:

- PCB top: 1.276; shim top/standoff shoulder: 1.576.
- HAT underside: 1.576 + 11 = 12.576.
- Existing 8.5 mm socket mouth: 12.576 - 8.5 = 4.076.
- Official Pi header plastic top: 3.846; pin tip: 9.846 (vendor Z minus 0.030).
- Plastic/socket gap: 0.230; pin insertion: 5.770 (required >= 5.5).
- Harwin M20-783 contact point: 3.41 +/- 0.25 mm from the socket mouth.
  Insertion past that point is 2.360 mm nominal, or 2.110 mm at the deepest
  contact-point tolerance. This replaces the generic KiCad 1.1 mm assumption
  for the engagement proof.
- Nominal pin-tip/cavity-ceiling clearance: 6.2 - 5.770 = 0.430.

The mechanical interface must measure insertion from the **socket mouth**.
The previous implementation measured from the header base and required the
mouth to coincide with it, which both overstated insertion and rejected a
legitimate seating gap. The three-line datum correction does not change any
fit, clearance, or insertion limit.

Updated after the part-accuracy merge: the measured J8 join datum is 3.876 mm,
so the seating offset is now 0.230 mm. This preserves the shim/standoff closure.
Offline recomposition has **0 errors**, including every HAT/standoff joint.
The two unchanged CAD assembly-cleanliness tests pass (17.20 s), and both Pi
stack tests pass (3.59 s). The negative no-shim model still has 0.677 mm^3 of
plastic interference. The legacy offline header solid rounds its top by 4 um,
so its fit report reads 5.774 mm insertion; the measured source datum gives
**5.770 mm**. This distinction is retained in the proof test, without changing
the fit tolerance. Zero Pi/HAT boolean intersection is verified in that offline
fit model. An independent Open CASCADE read of the official STEP finds all 40 square
posts at Z = 9.876000 mm (spread below 0.000001 mm); the compiled socket mouth
is Z = 4.106000 mm in the same frame, confirming 5.770000 mm engagement.
The manufacturer's full tolerance stack remains unavailable.

Sources: official Raspberry Pi STEP RP-010083-CA-1 (pinned catalog SHA-256);
[norelem DIN 988 dimension table](https://norelem.co.uk/medias/07522-Datasheet-32778-Shim-washers-DIN-988-en.pdf)
for the shim; existing KiCad socket model for the cavity envelope.
[Harwin M20-7832042](https://www.harwin.com/products/M20-7832042) confirms the
40-contact, 2.54 mm pitch, 8.5 mm socket height and 0.64 mm square mating pin.
The [manufacturer socket drawing](https://content.harwin.com/asset/d6e23765-84b2-4c9c-8c92-6f718b2b03f9/DRG-00386-Technical-Drawing-Datasheet-M20-783-pdf.pdf)
locates its contact point from the mouth. The generic KiCad cavity ceiling remains a model assumption;
this is nominal fit evidence, not a manufacturer tolerance-stack certification.
