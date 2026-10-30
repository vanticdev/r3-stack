# XT90H drawing correction

AMASS's XT90H V1.2 drawing gives **11.00 mm contact spacing** on both halves and **30.70 mm capped length** on XT90H-M. The previous catalog misread the 9.90 mm narrow cross-section dimension as contact spacing and used the female 29.70 mm length for both halves.

Corrected the generator, generated pin/entry geometry and male body envelope. The model remains an explicitly estimated, conservative envelope proxy: mating recess and wire-exit details are not resolved by these dimensions. No collision check or clearance was reduced.

Source: https://www.electrokit.com/upload/product/41020/41020964/XT90H.pdf (manufacturer drawing, pages 1–2).

Validation: the new dimension regression and catalog-wide mated-contact alignment check both pass (2 tests, 1.79 seconds). Power routing must be regenerated against the wider contact spacing and longer male body.
