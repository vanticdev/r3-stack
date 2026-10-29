# Raspberry Pi 5 dimensional review (2026-10-29)

The **85 × 56 mm** dimension is the PCB outline, not the complete purchased
assembly. Keep `mechanical.board_outline` for that outline, `mechanical.size`
for the **88.500 × 57.630352 × 18.976 mm** overall envelope, and
`mechanical.envelope_offset` for its asymmetric position. Hole coordinates are
measured from the PCB frame; enlarging the envelope must not move them.

Sources:

- [Official mechanical drawing](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf).
- [Official RP-010083-CA-1 STEP ZIP](https://pip-assets.raspberrypi.com/categories/892-raspberry-pi-5/documents/RP-010083-CA-1-rpi-5%203D%20STEP%20-%20No%20Graphics%20small%20file.zip),
  SHA-256 `6841637b4cfa97637bf34b529bfe896f24d9634b2c40b57a156781f55d88f06f`.
  The archive's MIT licence and guidance-only accuracy statement remain in the bank.

Measurements used both the cascadio tessellation (40 µm linear / 0.5 rad angular
settings already used by the bank) and OpenCASCADE's native STEP B-rep optimal
bounds, with no added shape tolerance. Native assembly bounds in source mm are
`[-0.450000, -1.6303501, -1.44999997]` to
`[88.0500001, 56.000000, 17.52600009]`; the mesh agrees to much less than 1 µm.
The original **1.5 mm validation tolerance is unchanged**.

| Geometry / source mesh object | Source bounds or measurement (mm) | Meaning |
| --- | --- | --- |
| PCB `OBJ8572` | X 0..85; Y 0..56; Z 0.030..1.306 | Board outline 85 × 56; represented laminate thickness 1.276 |
| Ethernet shell `OBJ4358` | X 66.45..88.05 | 3.05 mm beyond the PCB's +X edge |
| Tall USB block `OBJ3726` | X 70.47..88.00; Z −0.164..17.526 | Tallest represented component; top 16.220 mm above the represented PCB top |
| Micro-HDMI shells `OBJ5054`, `OBJ5086` | Y −1.6303501..6.869650 | Connector protrusions explain the previous Y mismatch |
| −X-edge feature `OBJ4922` | X −0.450..0.600 | Included in the overall X extent; it is outside the board outline |
| Underside component `OBJ8550` | Z −1.450..−0.050 | Overall height also includes components below the board |
| Four PCB mounting holes | centres (3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5); diameter 2.7 | **58 × 49 mm**, independently confirmed by a PCB section at Z = 0.7 |

The component frame remains the PCB's bottom-centre convention: subtract
`[42.5, 28, 0]` mm from the source frame. The resulting overall local bounds are
`[-42.95, -29.630352, -1.450]` to `[45.55, 28.000, 17.526]` mm. Consequently the
bottom-face centre of the overall envelope is offset by
`[1.300, -0.815176, -1.450]` mm. Mounting holes remain at X = −39 / +19 mm,
Y = ±24.5 mm. No consumer should derive their location from the enlarged box.

Envelope consumers in assembly/model checks, wiring obstacles, radio geometry,
simulation fallback geometry/mass centres, native feature-envelope plans and
fabrication fallback geometry now preserve that offset. The source placement and
RC-car assembly joins remain in the board frame. The RC-car needs no change to
its authored join offsets or deck-hole pattern.

## Fit evidence and limits

`r3 validate -C examples/robots/rc-car --stage assembly,model,cad --json` passes
assembly and model checks with **zero interferences** (132 plan solids, 83
objects, 105 joins). A separate FCL triangle-surface collision check placed the
**entire downloaded Pi tessellation**, including its open shells, at the compiled
Pi pose and compared it with every other existing RC-car part plan: **zero
collisions**. Thus the corrected protrusions do not reveal an interference against
those current counterpart plans.

The STEP includes open surfaces as well as 2,689 native solids. A volume-only
mesh boolean initially rejected some shells, so it was not used as proof of full
clearance. FCL's surface check includes them. Generated evidence is under
`build/modelbank/pi-surface-fit-evidence.json`, `pi-fit-evidence.json` and
`pi5-dimensions.png` (build artifacts, not committed).

This is not a live Onshape import/assembly proof. No API-key transport was
configured. Nor does the envelope review certify every mounting face: the
catalog's existing **nominal 1.6 mm mounting stack** differs from the STEP's
0.030..1.306 mm PCB faces. That discrepancy is explicitly recorded in
`key_specs.pcb_faces_z` / `pcb_thickness`; it needs a physical-board/seat review
before geometry-derived native mate frames can be approved. `interface_frames`
remain empty, and the native import guard still rejects unreviewed frames.
The nominal offline Pi solids are approximations, not newly sculpted bought-part
geometry. No tolerance or interference exclusion was expanded to obtain a pass.
