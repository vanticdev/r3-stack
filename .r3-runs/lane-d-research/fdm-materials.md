# FDM materials, printers and design rules: sourced datasheet values

Researched 2026-10-29. Every number below is copied from the cited source. Where a
source does not publish a value it says **unknown**. Nothing is interpolated.
"±" values are the manufacturer's stated spread. "Derived" marks a ratio I computed
from published numbers; the inputs are cited.

## Conventions and caveats

- **Bambu Lab TDS orientation.** The sheets give "X-Y" (in-plane) and "Z"
  (across layers) values for tensile, bending and impact. All specimens are 100 %
  infill, 0.4 mm nozzle, printed at 200 mm/s, then annealed and dried before testing
  (conditions per material below). Test methods are listed as "ISO 527, GB/T 1040",
  "ISO 178, GB/T 9341", "ISO 179, GB/T 1043", "ISO 75", "ISO 306, GB/T 1633" and
  "ISO 1183".
- **Prusament TDS orientation.** "Horizontal" and "Vertical xz" are both
  in-plane orientations, so they are **not** a Z (layer-direction) value. Prusament
  publishes a separate "Interlayer Adhesion [MPa]" figure, measured by an in-house
  Prusa Polymers method. That figure is the nearest thing to a Z strength.
- **eSUN TDS** uses Chinese GB/T methods (GB/T 1040, 9341, 1843, 1634, 1033, 3682).
  Its IZOD values are not directly comparable with Bambu's Charpy (ISO 179) values.
- **Tensile modulus vs Young's modulus.** Bambu calls it "Young's Modulus" (ISO 527).
  Prusament calls it "Tensile Modulus" and gives it in GPa (here converted to MPa
  ×1000). eSUN does not publish a tensile modulus.
- **"Max overhang angle" in Bambu sheets** (55° or ~70°) is a material-specific print
  capability. The angle reference is not defined in the sheet. Prusa's knowledge
  base uses the same number scale (45–60°, ~75° on Nextruder printers), measured
  from vertical.

---

## 1. PETG

### 1a. Bambu Lab PETG HF (TDS V1.0)

Source (official PDF linked from the Bambu store product page):
https://store.bblcdn.com/3a230e260a3a47c2b0db0156e07eef91.pdf
Product page: https://us.store.bambulab.com/products/petg-hf

| Property | Value | Method |
|---|---|---|
| Density | 1.28 g/cm³ | ISO 1183 |
| Melt index | 28.2 ± 2.7 g/10 min (210 °C, 2.16 kg) | — |
| Melting temperature | 214 °C | DSC, 10 °C/min |
| Glass transition (Tg) | 66 °C | DSC, 10 °C/min |
| Vicat softening | 70 °C | ISO 306, GB/T 1633 |
| HDT @ 1.8 MPa | 62 °C | ISO 75 |
| HDT @ 0.45 MPa | 69 °C | ISO 75 |
| Saturated water absorption | 0.40 % (25 °C, 55 % RH) | — |
| Young's modulus X-Y / Z | 1810 ± 190 / 1540 ± 130 MPa | ISO 527, GB/T 1040 |
| Tensile strength X-Y / Z | 34 ± 4 / 23 ± 4 MPa | ISO 527, GB/T 1040 |
| Elongation at break X-Y / Z | 8.6 ± 1.2 / 5.1 ± 0.8 % | ISO 527, GB/T 1040 |
| Bending modulus X-Y / Z | 2050 ± 120 / 1810 ± 140 MPa | ISO 178, GB/T 9341 |
| Bending strength X-Y / Z | 64 ± 3 / 48 ± 4 MPa | ISO 178, GB/T 9341 |
| Impact X-Y | 31.5 ± 2.2 kJ/m² (unnotched); 6.2 ± 1.8 kJ/m² (notched) | ISO 179, GB/T 1043 |
| Impact Z | 10.6 ± 1.2 kJ/m² | ISO 179, GB/T 1043 |
| Nozzle temperature | 230–260 °C | — |
| Bed temperature | 65–75 °C (Smooth / Textured PEI, glue) | — |
| Chamber temperature | 35–50 °C | — |
| Cooling fan | 0–60 % | — |
| Drying | Blast drying oven 65 °C, 8 h; X1 heatbed 75–85 °C, 12 h | — |
| Print and storage humidity | < 20 % RH (sealed, with desiccant) | — |
| Max overhang / bridge (sheet) | ~70° / ~30 mm | — |
| Specimen conditions | 255 °C nozzle, 70 °C bed, 200 mm/s, 100 % infill; annealed and dried 75 °C, 8 h | — |
| Z/XY tensile (derived) | 23/34 = **0.68** | — |

Notes from the sheet: annealing is "not recommended" (little gain, risk of
deformation). If you do anneal, use 75–80 °C for 4–8 h. Not resistant to acid,
alkali or some organic solvents. Resistant to most oils and greases. Does not
require an enclosure (the sheet makes no enclosure statement; chamber range is
35–50 °C).

### 1b. Prusament PETG (TDS v1.1, 16-02-2022), for cross-check

Source: https://storage.googleapis.com/prusa3d-content-prod-14e8-wordpress-prusament-prod/2023/10/9f8d2165-tds_prusament-petg_n_en.pdf

| Property | Value | Method |
|---|---|---|
| Density | 1.27 g/cm³ | ISO 1183 |
| HDT @ 0.45 MPa / 1.80 MPa | 68 / 68 °C | ISO 75 |
| Moisture absorption 24 h / 7 d | 0.07 / 0.10 % (24 °C, 22 % RH) | Prusa Polymers |
| Tensile yield, filament | 46 ± 1 MPa | ISO 527 |
| Tensile yield, printed Horizontal / Vertical xz | 47 ± 2 / 50 ± 1 MPa | ISO 527-1 |
| Tensile modulus Horizontal / Vertical xz | 1500 ± 100 / 1600 ± 100 MPa | ISO 527-1 |
| Elongation at yield | 5.1 ± 0.1 / 5.1 ± 0.1 % | ISO 527-1 |
| Elongation at break | unknown | — |
| Flexural strength | 66 ± 2 / 70 ± 1 MPa | ISO 178 |
| Flexural modulus | 1700 ± 100 / 1600 ± 100 MPa | ISO 178 |
| Charpy unnotched / notched | NB (no break) / 6 ± 1 and 3 ± 1 kJ/m² | ISO 179-1 |
| **Interlayer adhesion** (≈ Z) | **18 ± 4 MPa** | Prusa Polymers |
| Shore D | 74 | Prusa Polymers |
| Tg, Vicat | unknown | — |
| Nozzle / bed | 250 ± 10 °C / 80 ± 10 °C | — |
| Fan | 50 % | — |
| Drying | unknown (not in TDS) | — |
| Z/XY (derived) | interlayer 18 / horizontal 47 = **0.38** (different test methods) | — |

Specimens: Original Prusa i3 MK3, 0.20 mm layers, 2 perimeters, 100 % rectilinear,
250 °C nozzle, 80 °C bed.

---

## 2. PLA+ and PLA

### 2a. eSUN PLA+ (TDS "Feb.2026 Version1.0")

Source: https://www.esun3d.com/media/esun/catalog/certification/product/PLA_3KG1.75MM/PLA_-TDS-en.pdf
(The URL says "PLA" but the document title is "PLA+ Technical Data Sheet".)

| Property | Value | Method |
|---|---|---|
| Density | 1.24 g/cm³ | GB/T 1033 |
| Melt flow index | 3.7 (190 °C / 2.16 kg) | GB/T 3682 |
| HDT @ 0.45 MPa | 53.78 °C | GB/T 1634 |
| HDT @ 1.8 MPa | unknown | — |
| Tg | N/A (not published) | — |
| Vicat | unknown | — |
| Tensile strength X-Y / Z | 70.37 / 36.36 MPa | GB/T 1040 |
| Tensile modulus | unknown (not published) | — |
| Elongation at break X-Y / Z | 4.14 / 2.92 % | GB/T 1040 |
| Flexural strength X-Y / Z | 100.3 / 56.4 MPa | GB/T 9341 |
| Flexural modulus X-Y / Z | 3259.39 / 2873.94 MPa | GB/T 9341 |
| IZOD impact X-Y / Z | 7.27 / 2.22 kJ/m² | GB/T 1843 |
| UV resistance | NO | — |
| Nozzle / bed | 210–230 °C / 45–60 °C | — |
| Drying | 50 °C, 8–12 h | — |
| Fan | 100 % | — |
| Specimen conditions | Bambu P1S, 0.4 mm nozzle, 220 °C, 65 °C bed, 2 walls, 3 top/bottom, 100 % infill, OrcaSlicer 2.1.0 beta | — |
| Z/XY tensile (derived) | 36.36/70.37 = **0.52** | — |

Caution: retailer listings for older eSUN PLA+ batches give different numbers
(e.g. XY tensile ~53 MPa, elongation ~20 %). Use the TDS above. Those listings
are not cited as values here.

### 2b. Bambu Lab PLA Basic (TDS V3.0), plain PLA

Source: https://store.bblcdn.com/s1/default/58b85d0f3db94878854a28fdb8a0006e/Bambu_PLA_Basic_Technical_Data_Sheet.pdf
Product page: https://us.store.bambulab.com/products/pla-basic-filament

| Property | Value | Method |
|---|---|---|
| Density | 1.24 g/cm³ | ISO 1183 |
| Melt index | 23.2 ± 3.5 g/10 min (210 °C, 2.16 kg) | — |
| Melting temperature | 160 °C | DSC |
| Tg | 60 °C | DSC, 10 °C/min |
| Vicat softening | 57 °C | ISO 306, GB/T 1633 |
| HDT @ 1.8 MPa / 0.45 MPa | 54 / 57 °C | ISO 75 |
| Saturated water absorption | 0.43 % | 25 °C, 55 % RH |
| Young's modulus X-Y / Z | 2580 ± 220 / 2060 ± 170 MPa | ISO 527, GB/T 1040 |
| Tensile strength X-Y / Z | 35 ± 4 / 31 ± 3 MPa | ISO 527, GB/T 1040 |
| Elongation at break X-Y / Z | 12.2 ± 1.8 / 7.5 ± 1.3 % | ISO 527, GB/T 1040 |
| Bending modulus X-Y / Z | 2750 ± 160 / 2370 ± 150 MPa | ISO 178, GB/T 9341 |
| Bending strength X-Y / Z | 76 ± 5 / 59 ± 6 MPa | ISO 178, GB/T 9341 |
| Impact X-Y | 26.6 ± 2.8 kJ/m² (unnotched); 7.9 ± 1.2 kJ/m² (notched) | ISO 179, GB/T 1043 |
| Impact Z | 13.8 ± 0.9 kJ/m² | ISO 179, GB/T 1043 |
| Nozzle / bed | 190–230 °C / 35–45 °C | — |
| Chamber | 25–45 °C | — |
| Drying | Blast oven 50 °C, 8 h; X1 heatbed 60–70 °C, 12 h | — |
| Humidity | < 20 % RH sealed | — |
| Max overhang / bridge (sheet) | 55° / 30 mm | — |
| Specimen conditions | 220 °C nozzle, 35 °C bed, 200 mm/s, 100 % infill; annealed and dried 55 °C, 8 h | — |
| Z/XY tensile (derived) | 31/35 = **0.89** | — |

Note: Bambu's 35 MPa XY tensile for PLA Basic is much lower than eSUN's 70 MPa
for PLA+. The test methods and specimen geometry differ (ISO 527 vs GB/T 1040),
so do not rank the two brands from these sheets. Compare within one manufacturer's
sheets only. PLA needs open-door or low chamber temperature. The Bambu chamber
range is 25–45 °C.

---

## 3. ASA

### 3a. Bambu Lab ASA (TDS V3.0)

Source: https://store.bblcdn.com/ad7b08230c164e72856cffbe06bb7dc9.pdf
Product page: https://us.store.bambulab.com/products/asa-filament

| Property | Value | Method |
|---|---|---|
| Density | 1.05 g/cm³ | ISO 1183 |
| Melt index | 7.0 ± 0.8 g/10 min (260 °C, 2.16 kg) | — |
| Tg | N/A | — |
| Vicat softening | 106 °C | ISO 306, GB/T 1633 |
| HDT @ 1.8 MPa / 0.45 MPa | 92 / 100 °C | ISO 75 |
| Saturated water absorption | 0.45 % | 25 °C, 55 % RH |
| Young's modulus X-Y / Z | 2450 ± 270 / 2120 ± 260 MPa | ISO 527, GB/T 1040 |
| Tensile strength X-Y / Z | 37 ± 3 / 31 ± 4 MPa | ISO 527, GB/T 1040 |
| Elongation at break X-Y / Z | 9.2 ± 1.4 / 4.6 ± 0.8 % | ISO 527, GB/T 1040 |
| Bending modulus X-Y / Z | 1920 ± 130 / 1650 ± 120 MPa | ISO 178, GB/T 9341 |
| Bending strength X-Y / Z | 65 ± 5 / 40 ± 3 MPa | ISO 178, GB/T 9341 |
| Impact X-Y | 41.0 ± 2.3 kJ/m² (unnotched); 19.6 ± 1.8 kJ/m² (notched) | ISO 179, GB/T 1043 |
| Impact Z | 4.9 ± 0.6 kJ/m² | ISO 179, GB/T 1043 |
| Nozzle / bed | 240–270 °C / 80–100 °C | — |
| Chamber | 45–60 °C | — |
| Cooling fan | 0–80 % | — |
| Drying | Blast oven 80 °C, 8 h; X1 heatbed 90–100 °C, 12 h | — |
| Humidity | < 20 % RH sealed | — |
| Max overhang / bridge (sheet) | ~70° / ~40 mm | — |
| Specimen conditions | 260 °C, 80 °C bed, 200 mm/s, 100 %; annealed and dried 80 °C, 12 h | — |
| Z/XY tensile (derived) | 31/37 = **0.84** | — |

Enclosure: the Bambu store page says ABS and ASA have "the same warping tendency
and enclosed printer requirement when printing large-size and/or high infill density
models". Resistant to acid and alkali. Combustion gives a "pungent odor".

### 3b. Prusament ASA (TDS v1.1, 20-05-2024), for cross-check

Source: https://storage.googleapis.com/prusa3d-content-prod-14e8-wordpress-prusament-prod/2024/05/bc8551ef-tds_prusament-asa_2024_en.pdf

| Property | Value | Method |
|---|---|---|
| Density | 1.07 g/cm³ | ISO 1183 |
| MFR / MVR | 20–24 g/10 min / 19–23 cm³/10 min (10 kg, 220 °C) | ISO 1133 |
| HDT @ 0.45 / 1.80 MPa | 93 / 86 °C | ISO 75 |
| Moisture absorption 24 h / 7 d | 0.16 / 0.17 % | Prusa Polymers |
| Tensile yield Horizontal / Vertical xz | 42 ± 1 / 45 ± 2 MPa | ISO 527-1 |
| Tensile modulus | 1600 ± 100 / 1700 ± 100 MPa | ISO 527-1 |
| Elongation at yield | 3.4 ± 0.2 / 3.8 ± 0.2 % | ISO 527-1 |
| Flexural strength | 64 ± 1 / 69 ± 1 MPa | ISO 178 |
| Flexural modulus | 2000 ± 100 / 1900 ± 100 MPa | ISO 178 |
| Charpy unnotched | 25 ± 3 / 38 ± 11 kJ/m² | ISO 179-1 |
| Charpy notched | 12 ± 1 / 15 ± 3 kJ/m² | ISO 179-1 |
| **Interlayer adhesion** (≈ Z) | **11 ± 1 MPa** | Prusa Polymers |
| Shore D | 78 | — |
| Nozzle / bed | 260 ± 10 °C / 110 ± 5 °C | — |
| Fan | 30 % (0–50 %) | — |
| Drying, Tg, Vicat, elongation at break | unknown | — |
| Z/XY (derived) | 11/42 = **0.26** (different test methods) | — |

---

## 4. Carbon-fibre polyamide

Bambu store pages for both: "it is necessary to replace the nozzle with hardened
steel to prevent excessive wear" (for CF/GF filaments).

### 4a. Bambu Lab PAHT-CF (TDS V3.0)

Source: https://store.bblcdn.com/7166b20bc68f4c87b32c972b856eeb4b.pdf
Product page: https://us.store.bambulab.com/products/paht-cf

Composition per the sheet: "PA 12 and other long-chain PA, carbon fiber".
Mechanical values are "Dry state".

| Property | Value | Method |
|---|---|---|
| Density | 1.06 g/cm³ (as published) | ISO 1183 |
| Melt index | 14.4 ± 2.0 g/10 min (280 °C, 2.16 kg) | — |
| Melting / Tg / crystallisation | 225 / 70 / 140 °C | DSC, 10 °C/min |
| Vicat softening | 220 °C | ISO 306, GB/T 1633 |
| HDT @ 1.8 / 0.45 MPa | 170 / 194 °C | ISO 75 |
| Saturated water absorption | 0.88 % | 25 °C, 55 % RH |
| Young's modulus X-Y / Z | 3860 ± 230 / 2180 ± 130 MPa | ISO 527, GB/T 1040 |
| Tensile strength X-Y / Z | 92 ± 7 / 47 ± 5 MPa | ISO 527, GB/T 1040 |
| Elongation at break X-Y / Z | 8.4 ± 1.8 / 4.1 ± 1.2 % | ISO 527, GB/T 1040 |
| Bending modulus X-Y / Z | 4230 ± 210 / 1820 ± 170 MPa | ISO 178, GB/T 9341 |
| Bending strength X-Y / Z | 125 ± 7 / 61 ± 5 MPa | ISO 178, GB/T 9341 |
| Impact X-Y | 57.5 ± 3.4 kJ/m² (unnotched); 22.8 ± 1.8 kJ/m² (notched) | ISO 179, GB/T 1043 |
| Impact Z | 13.3 ± 0.8 kJ/m² | ISO 179, GB/T 1043 |
| Nozzle / bed | 260–290 °C / 80–100 °C | — |
| Nozzle size | 0.4, 0.6 (recommended), 0.8 mm | — |
| Chamber | 45–60 °C | — |
| Fan / speed | 0–40 % / < 100 mm/s | — |
| Drying | Blast oven 80 °C, 8–12 h; X1 heatbed 90–100 °C, 12 h | — |
| Humidity | < 20 % RH sealed | — |
| Max overhang / bridge (sheet) | ~70° / ~40 mm | — |
| Specimens | annealed and dried 80 °C, 12 h; part annealing 80–130 °C, 6–12 h suggested | — |
| Z/XY tensile (derived) | 47/92 = **0.51** | — |

Enclosure: the store page says it "has high thermal insulation requirements during
the printing process, making it unsuitable for use with open-frame printers".
The 1.06 g/cm³ density is low for a CF nylon, but it is the published value.
Confirm it by weighing a part before you rely on it for mass.

### 4b. Bambu Lab PA6-CF (TDS V3.0)

Source: https://store.bblcdn.com/b2abc59ac250492b979a52f1ce3e61b3.pdf
Product page: https://us.store.bambulab.com/products/pa6-cf

| Property | Value | Method |
|---|---|---|
| Density | 1.09 g/cm³ | ISO 1183 |
| Melt index | 6.8 ± 0.6 g/10 min (280 °C, 2.16 kg) | — |
| Melting / Tg / crystallisation | 223 / 68 / 185 °C | DSC |
| Vicat softening | 212 °C | ISO 306, GB/T 1633 |
| HDT @ 1.8 / 0.45 MPa | 164 / 186 °C | ISO 75 |
| Saturated water absorption | **2.35 %** | 25 °C, 55 % RH |
| Young's modulus X-Y / Z | 4430 ± 310 / 2170 ± 230 MPa | ISO 527, GB/T 1040 |
| Tensile strength X-Y / Z | 102 ± 7 / 48 ± 6 MPa | ISO 527, GB/T 1040 |
| Elongation at break X-Y / Z | 5.8 ± 1.6 / 3.7 ± 0.8 % | ISO 527, GB/T 1040 |
| Bending modulus X-Y / Z | 5460 ± 280 / 2240 ± 220 MPa | ISO 178, GB/T 9341 |
| Bending strength X-Y / Z | 151 ± 8 / 80 ± 7 MPa | ISO 178, GB/T 9341 |
| Impact X-Y | 40.3 ± 2.5 kJ/m² (unnotched); 13.4 ± 1.7 kJ/m² (notched) | ISO 179, GB/T 1043 |
| Impact Z | 15.5 ± 1.7 kJ/m² | ISO 179, GB/T 1043 |
| Nozzle / bed | 260–290 °C / 80–100 °C | — |
| Nozzle size | 0.4, 0.6 (recommended), 0.8 mm | — |
| Chamber | 45–60 °C | — |
| Fan / speed | 0–60 % / < 100 mm/s | — |
| Drying | Blast oven 80 °C, 8–12 h; X1 heatbed 90–100 °C, 12 h | — |
| Max overhang / bridge (sheet) | ~70° / ~40 mm | — |
| Z/XY tensile (derived) | 48/102 = **0.47** | — |

The sheet says the CF "provides significantly high stiffness and strength
properties **in dry environments**". PA6 absorbs about 2.7× the moisture of
PAHT-CF (2.35 vs 0.88 %), so expect a property drop when the part is conditioned.
Wet-state values are unknown (not published).

---

## 5. Z / XY strength ratio guidance

No manufacturer publishes a general design factor. These are the published
anisotropy data points (Z tensile / XY tensile, same sheet, same method):

| Material (source) | Z/XY tensile | Z/XY bending | Z/XY modulus |
|---|---|---|---|
| Bambu PLA Basic | 0.89 | 0.78 | 0.80 |
| Bambu ASA | 0.84 | 0.62 | 0.87 |
| Bambu PETG HF | 0.68 | 0.75 | 0.85 |
| eSUN PLA+ | 0.52 | 0.56 | 0.88 (flex) |
| Bambu PAHT-CF | 0.51 | 0.49 | 0.56 |
| Bambu PA6-CF | 0.47 | 0.53 | 0.49 |
| Prusament PETG (interlayer adhesion ÷ horizontal yield) | 0.38 | — | — |
| Prusament ASA (interlayer adhesion ÷ horizontal yield) | 0.26 | — | — |

Qualitative published guidance:
- Prusa KB: "Like wood, FFF prints are stronger in one direction than another";
  orient structural parts for structural integrity.
  https://help.prusa3d.com/en/article/modeling-with-3d-printing-in-mind_164135/
- Hubs: "FDM components are inherently weaker in one direction due to layer
  orientation." https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/

A conservative design default is to use the Z value from the TDS for any load
across layers. For CF nylons that is about 0.5 × XY. My recommendation (not a
published value): use 0.5 for unknown or CF materials and the per-material ratio
above otherwise. The 100 %-infill TDS values are upper bounds for real parts.

---

## 6. FDM design rules

### (a) Overhang

| Rule | Value | Source |
|---|---|---|
| General unsupported overhang | ~45°; "Try not to design your parts with angles over 45 degrees." | Hubs, https://hubs.com/knowledge-base/key-design-considerations-3d-printing |
| FDM overhang | "an overhang can usually be printed up to 45° without compromising quality"; "Above 45°, support is required" | Hubs, https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/ |
| Prusa | "between 45 and 60 degrees" depending on nozzle and settings; Nextruder / 360° cooling printers (MK4S, CORE One) ~75°; 0.25 mm nozzle may be < 45° | Prusa KB (above) |
| Bambu per material | PLA Basic 55°; PETG HF, ASA, PAHT-CF, PA6-CF ~70° | Bambu TDS (above) |
| Bridges | Hubs: "If a bridge exceeds 5mm, sagging or marks from support material can occur." Bambu TDS max bridge: PLA / PETG HF 30 mm, ASA / CF-PA 40 mm. Prusa: "Short horizontal bridges can be printed without supports" (no number). | Hubs; Bambu TDS; Prusa KB |

Design default: **45° from vertical**, hard limit. Material-specific relaxations
(55–70°) come from the TDS.

### (b) Minimum wall thickness

| Rule | Value | Source |
|---|---|---|
| Hubs | "All 3D printers can successfully print components with wall thicknesses greater than 0.8 mm." | Hubs key design considerations |
| Prusa, 0.4 mm nozzle | extrusion width 0.45 mm; 1 perimeter ≈ 0.45 mm, 2 ≈ 0.9 mm, 3 ≈ 1.35 mm, 4 ≈ 1.8 mm; "Walls thinner than one nozzle perimeter are not printable"; walls between 1 and 2 perimeters print poorly (the table wording is ambiguous) | Prusa KB |
| Wall around a thread | "the minimum wall thickness around a thread should match the diameter of the fastener" (e.g. M5 → 5 mm) | Hubs, https://www.hubs.com/knowledge-base/how-assemble-3d-printed-parts-threaded-fasteners/ |
| Pins | pins < 5 mm diameter are perimeter-only and weak | Hubs FDM design |
| Accuracy / clearance | "accurate to at least 0.2 mm"; moving parts ≥ 0.3 mm clearance | Prusa KB |

Design default: **0.9 mm minimum (2 × 0.45 mm), 1.2 mm or more for structural walls.**
Use a whole number of perimeters (0.45 mm each for Prusa, ~0.42 mm Bambu default).
The Bambu line width is not sourced here.

### (c) Heat-set inserts

CNC Kitchen (manufacturer table, reproduced by the 3DJake retailer page; units mm):
https://www.3djake.com/cnc-kitchen/threaded-inserts-set-standard
D1 = insert OD, D2 = centre tip Ø, D3 = recommended hole Ø, L = length, W = minimum
wall around the insert.

| Thread | L | D1 | D2 | **Hole D3** | **Min wall W** |
|---|---|---|---|---|---|
| M2 | 3.0 | 3.6 | 3.1 | **3.2** | **1.3** |
| M2.5 | 4.0 | 4.6 | 3.9 | **4.0** | **1.6** |
| M3 (standard) | 5.7 | 4.6 | 3.9 | **4.0** | **1.6** |
| M3 Short | 3.0 | 4.6 | 3.9 | **4.0** | **1.6** |
| M3 Voron | 4.0 | 5.0 | 4.25 | **4.4** | **1.3** |
| M4 (standard) | 8.1 | 6.3 | 5.5 | **5.6** | **2.1** |
| M4 Short | 4.0 | 6.3 | 5.5 | **5.6** | **2.1** |
| M5 | 9.5 | 7.1 | 6.3 | 6.4 | 2.6 |

Ruthex (same D1/D2/D3/L/W columns; manufacturer table reproduced by 3DJake):
https://www.3djake.ch/de-CH/ruthex/m2-m3-m4-m5-gewindeeinsatz-sortimentskasten
M2 3.6/3.1/**3.2**/L 4.0/W 1.3; M3 4.6/3.9/**4.0**/5.7/1.6; M4 6.3/5.5/**5.6**/8.1/2.1;
M5 7.1/6.3/6.4/9.5/2.6. Ruthex M3 short: L 4.0, hole 4.0, W 1.6. M2.5 is RX-M2.5x5.7
(L 5.7). Its D1–W values are unknown from accessible sources (the Ruthex product
page shows the table only as an image).
Ruthex product page (insert sizes, insertion temperature):
https://www.ruthex.de/en/collections/gewindeeinsatze/products/ruthex-komplett-set-m2-m3-m4-m5-sortimentskasten-m2-5-m6-m8-standard-gewindeeinsatz-m3s-m4s-m5s-short-1-4-zoll-gewindebuchsen-lotspitzen-set
"The ideal temperature for insertion is 10-15 degrees above the hotend temperature".
CNC Kitchen gives +10 to 20 °C. The column meanings are inferred from the drawing
layout, because neither page labels them in text.

Hole depth: no manufacturer publishes a minimum. Secondary guidance from a
DigiKey tutorial that credits CNC Kitchen: hole **at least 1 mm deeper than insert
length** for displaced plastic.
https://www.digikey.com/es/maker/tutorials/2025/intro-to-freecad-part-4-how-to-use-heat-set-inserts
Design default: depth = L + 1.0 mm (blind). Size the screw so it does not bottom out.
Hole Ø is CAD-nominal. Vertical printed holes come out undersize (Hubs).

### (d) Hex nut traps and counterbores

| Rule | Value | Source |
|---|---|---|
| Captive nut offset | "a 0.2 mm offset around the nut (0.1 mm on each side) should give a loose fit"; may need more above M12 | Hubs threaded fasteners |
| Nut insertion | "Add 0.2–0.3 mm to nut dimensions for easy insertion" | fb-library, https://fb-library.readthedocs.io/en/latest/bolt_fittings/ |
| M3 defaults (fb-library) | bolt hole Ø 3.3 mm (r 1.65); counterbore Ø 6.2 mm (r 3.1); square-nut trap 5.6 × 2.1 mm | fb-library |
| Counterbore head clearance | no authoritative published number besides the fb-library M3 default (6.2 mm for a 5.5 mm M3 socket head, i.e. +0.7 mm) | — |

Nominal ISO 4032 nut across-flats are M2 4.0, M2.5 5.0, M3 5.5, M4 7.0 mm. ISO 4762
socket head diameters are M2 3.8, M2.5 4.5, M3 5.5, M4 7.0 mm. These are standard
values from general knowledge and were not fetched in this research. Design
default: nut trap = AF + 0.2 to 0.3 mm (flat-to-flat); counterbore = head Ø +
0.5 to 0.7 mm; clearance hole M3 = 3.3–3.4 mm.

### (e) Self-tapping into printed plastic vs inserts

| Rule | Value | Source |
|---|---|---|
| Pilot hole | "A pilot hole size that provides 75% to 80% thread engagement is a good starting point"; exact size from the screw supplier | Hubs threaded fasteners |
| Printed threads | "Threads smaller than M5 printed via FDM should be avoided" | Hubs threaded fasteners |
| General pilot rule | between the minor and major diameter of the thread; trial and error | Accu, https://accu.co.uk/p/128-how-to-use-self-tapping-screws |
| Community test (Bambu PETG) | M3 pitch Ø 2.675 mm; PETG HF hole 2.90 mm horizontal / 2.72 mm vertical; PETG Basic 2.90 / 2.60 mm | MakerWorld post https://makerworld.com/en/community/post/1182854 (not fetched directly, HTTP 403; values from a search summary; treat as unverified) |
| Heat-set insert instead | M3 brass insert hole 4.0 mm | CNC Kitchen / Ruthex tables above |

Takeaway: an M3 machine screw driven into a printed hole needs about **2.6–2.9 mm**
(vertical holes print smaller, so use the smaller end; horizontal holes need the
larger end). The commonly quoted 2.5 mm is at or below the 2.675 mm pitch
diameter and risks splitting layers. Use inserts for anything removed more than a
few times or loaded in tension across layers. No manufacturer publishes a pull-out
comparison here; that is unknown.

---

## 7. Printers

| Printer | Build volume W×D×H (mm) | Enclosure | Max nozzle | Max bed | Chamber | Default nozzle | Source |
|---|---|---|---|---|---|---|---|
| Bambu X1 Carbon | 256 × 256 × 256 | Yes (aluminium and glass shell; chamber fan, no active heater) | 300 °C | 120 °C (third-party copy; X1E brochure gives 110 °C @220 V / 120 °C @110 V for the same platform) | no active heating | 0.4 mm hardened steel | Bambu spec table reproduced at https://3dprintbeginner.com/bambu-lab-x1-carbon-review (bambulab.com returns 403; X1C is no longer listed on the US store) |
| Bambu X1E | 256 × 256 × 256 | Yes | 320 °C | 110 °C @220 V, 120 °C @110 V | Active heating, max 60 °C | 0.4 mm (0.2/0.6/0.8 optional) | Bambu X1E brochure https://3dprintergear.com.au/assets/brochures/BL_X1E.pdf |
| Bambu P1S | 256 × 256 × 256 | Yes (plastic and glass) | 300 °C | 100 °C | no active heating | 0.4 mm stainless (0.2/0.6/0.8 optional) | https://us.store.bambulab.com/products/p1s (P1P/P1S comparison table) |
| Bambu P1P | 256 × 256 × 256 | No (open frame; printable modplates) | 300 °C | 100 °C | — | 0.4 mm stainless | same P1S page comparison table (P1P page redirects; discontinued) |
| Bambu A1 | 256 × 256 × 256 | No ("We don't recommend enclosing the A1") | 300 °C | 100 °C | — | 0.4 mm stainless | https://us.store.bambulab.com/products/a1 |
| Bambu A1 mini | 180 × 180 × 180 | No | 300 °C | 80 °C | — | 0.4 mm stainless | https://us.store.bambulab.com/products/a1-mini |
| Bambu H2D | single nozzle 325 × 320 × 325; dual nozzle 300 × 320 × 325; total for two nozzles 350 × 320 × 325 | Yes | 350 °C | 120 °C | Active heating, 65 °C | 0.4 mm hardened steel | https://us.store.bambulab.com/products/h2d |
| Prusa MK4S | 250 × 210 × 220 | No (optional Enclosure add-on, 45 °C inside) | 290 °C | 120 °C | 45 °C in Enclosure | High-flow Prusa Nozzle brass CHT 0.4 mm | https://www.prusa3d.com/product/original-prusa-mk4s-3d-printer-5/ |
| Prusa CORE One | 250 × 220 × 270 | Yes | 290 °C | 120 °C | 55 °C (enclosed, "heats up to 55 °C") | 0.4 mm (nozzle type not confirmed on the kit page) | https://www.prusa3d.com/product/prusa-core-one-kit/ |
| Prusa XL (single tool) | 360 × 360 × 360 | No (optional Enclosure for XL+: up to 50 °C; with optional external heater: up to 60 °C) | 290 °C | "up to 115 °C" (spec table; the comparison lists 120 °C for XL+) | see enclosure | 0.4 mm assumed; exact size not stated on page ("High-Flow Nozzles") | https://www.prusa3d.com/product/original-prusa-xl-assembled-single-toolhead-3d-printer/ |

Material–printer fit, from the sources above:
- PLA / PETG: any printer listed.
- ASA: enclosure required for large or high-infill parts (Bambu). Prusament ASA
  wants a 110 °C bed, which is above the P1S/P1P/A1 maximum of 100 °C. Bambu ASA
  is 80–100 °C. A1 mini (80 °C max bed) is outside Bambu ASA's 80–100 °C range
  except at the minimum, and Bambu lists ASA as "Not Recommended" for A1 mini.
- PAHT-CF / PA6-CF: hardened nozzle (X1C, X1E and H2D ship hardened; P1/A1 ship
  stainless) plus an enclosure (PAHT-CF is "unsuitable for use with open-frame
  printers"). Chamber 45–60 °C. Nozzle up to 290 °C fits every printer listed.
