# Purchased-part connector accuracy

The connector stage now measures mating geometry **during each robot build**. It
uses the component's own placed STEP mesh (or a current, checksum-checked Onshape
CAD export), and the harness connector's sourced mesh. An `embedded: true`
connector must appear in its owner mesh. A non-embedded connector is supplied
separately: the check searches its placed connector-type model **and** the
component mesh. It keeps the component body for interference checking and
collapses duplicate coincident section loops when an export contains both.
Procedural connector proxies remain useful for rendering but cannot verify
purchased contacts.
No model-bank pass/fail cache is used by this check.

`build/connectors/connectors.json`, `checks.mesh_mates`, records every pair that
`mated_pairs()` enumerates, including integral terminations, floating pairs and
direct board mates. It includes physical contact centres, axial intervals, shape,
size, count, pitch, rows, compatible catalog labels, measured lateral error,
insertion, solid overlap and diagnostics. Several electrical connector families
can share the same geometric signature; the labels are deliberately a list.

The implementation sections welded mesh bodies with trimesh, resolves section
loops with Shapely, assigns contacts one-to-one with SciPy, and checks solid
intersection/difference with the existing manifold engine. Slotted bullet-pin
petals are recognized as a circular contact within one connected solid. Socket
openings are measured from section loops and axial rays. Contact corridors are
excluded from housing overlap. Open surfaces make the completeness of the housing
check explicitly unverifiable, even when the contact alignment succeeds.

The original frame checks remain. The new error codes are `connector_empty`,
`connector_type_mismatch`, `connector_pin_unmated`, `connector_insertion_short`
and `connector_body_interference`. Every error blocks the existing release gate;
regression tests exercise that gate for all five codes. The lateral limit stays
0.1 mm. Insertion uses the catalog's nominal contact overlap, overridden by the
matching mechanical join's `verify.min_insertion`. Missing source geometry produces
`connector_unverifiable`, naming the side and owner. The same warning applies
when a model has surfaces but no resolvable contacts. `connector_empty` requires
an actually empty search location with no missing/proxy model supplier that
could account for it. The check never replaces that
warning with an inferred pass. Supplied physical measurements must be recorded in
catalog provenance and reviewed; no override is silently generated.

A soldered lead uses its routed conductor ends, not a purchased plug proxy. Each
occupied GY-521 pad must have a unique measured hole under the conductor end;
unoccupied pads need no conductor. This checks geometric termination, not solder
wetting or electrical continuity.

## Generality

The mesh check has no robot ID, component ID, component-kind gate or fixed
purchased-part placement. Pair discovery comes from the project spec; frames,
contact arrays, envelopes, insertion requirements and model suppliers come from
the catalog and current build. Embedded post checks apply to any component kind
and accept arbitrary pin names. Pad/conductor dispatch uses `pin_shape: pad`, a
harness owner and the partner's `gender: none`, not a connector-name prefix.
Unsupported or simplified contact geometry produces a named unverifiable warning.

Synthetic tests exercise posts, sockets, pads, empty locations, insertion,
interference, duplicate exports and nested housings without loading a robot
example. Additional stage-level fixtures verify a custom actuator with named
contacts and a custom sensor's permanent termination with arbitrary type IDs.

## Raspberry Pi 5 evidence

Primary sources:

- [Official RP-010083-CA-1 STEP archive](https://pip-assets.raspberrypi.com/categories/892-raspberry-pi-5/documents/RP-010083-CA-1-rpi-5%203D%20STEP%20-%20No%20Graphics%20small%20file.zip),
  SHA-256 `6841637b4cfa97637bf34b529bfe896f24d9634b2c40b57a156781f55d88f06f`.
- [Official mechanical drawing](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf).
- [Raspberry Pi hardware documentation and GPIO pinout](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html).
- [PCIe connector standard](https://datasheets.raspberrypi.com/pcie/pcie-connector-standard.pdf)
  and [debug connector specification](https://datasheets.raspberrypi.com/debug/debug-connector-specification.pdf).
- [Camera connector documentation](https://www.raspberrypi.com/documentation/accessories/camera.html),
  [JST SH series drawing](https://www.jst-mfg.com/product/pdf/eng/eSH.pdf),
  [Molex micro-HDMI drawing](https://www.molex.com/content/dam/molex/molex-dot-com/products/automated/en-us/salesdrawingpdf/467/46765/467650301_sd.pdf).

STEP coordinates use the PCB lower-left at (0,0); the catalog subtracts
(42.5,28,0) mm. Forty distinct 0.64 mm J8 posts establish the 2.54 mm grid. Pin 1
is STEP (8.37,51.23) mm, or catalog (-34.13,23.23) mm. Four 2.7 mm clearance holes
are at STEP (3.5,3.5), (61.5,3.5), (3.5,52.5), (61.5,52.5) mm: exactly 58 × 49 mm
for M2.5 fasteners. The drawing's 29 mm header-centre dimension and 2.54 mm grid
imply pin 1; **the drawing does not independently dimension every pin centre**.

J8 connector origin changes from (-10,24.5,3.880) to (-10,24.5,3.876) mm. The
mechanical join datum changes from Z=4.100 to 3.876 mm, so the HAT follows downward
by 0.224 mm. Its layout and the wiring router were not edited. STEP PCB thickness
is 1.276 mm (Z=0.030..1.306), versus the retained nominal catalog 1.6 mm. That
existing discrepancy stays visible as `component_model_dimension_mismatch`.

The vendor-family file has a strict `refinements` mapping applied before
`extends`. This updates every Pi 5 RAM variant without editing the mechanically
owned `robot.yaml`. GPIO identifiers used by bindings stay stable; their `net`
labels include the official alternate-function names (for example GPIO2/SDA1).

All 16 physical interfaces are present: J8, four separate USB-A ports, Ethernet,
USB-C, two micro-HDMI ports, CAM0/CAM1, PCIe, fan, UART, battery and PoE. Connector
models are cropped **from downloaded vendor surfaces**, never reconstructed
boxes. The frame origin is on the opening plane, z is cable exit, x follows the
pin row. USB/Ethernet use measured frontmost spring-end centres projected onto the opening plane. HDMI uses projected spring-region centres;
those are explicitly estimated until the fitted connector's numbered mating
contact drawing is available. The USB-C STEP contains a shell, tongue and solder
tails but **no mating contact solids**; its standard 16-contact USB2 layout cannot
be claimed verified. PoE CT signal-to-number association is also unverified.
The table in `.r3-runs/part-accuracy-audit.md` separates measured face error from
contact uncertainty rather than presenting false zero-error pin claims.

Regression tests fetch the pinned model and section its actual surfaces. Gates
are 0.05 mm for J8 and resolved contact centres, 0.1 mm for every opening plane,
1 degree for opening direction, and the exact 58 × 49 mm catalog hole pattern.
The general detector currently resolves posts, pad bores, socket openings,
slotted round contacts and repeated finger sections. Complex multi-row curved
spring contacts may be incomplete or ambiguous and do not receive a verified
contact claim. Shell geometry alone never proves that the contacts exist.

## GY-521 variants and owned-board measurements

The previous catalog-built purchased-part boxes are removed. The replacement is
the pinned, downloaded [RSX CANSAT GY-521 STEP](https://github.com/adam-kabbara/RSX-CANSAT-2026/blob/6e7b39f8cbd07e0290e0925f2fbab3cb3e0e376f/electrical/DO_NOT_EDIT__KICAD_LIBRARIES/Custom_3D_Models.3dshapes/GY-521_%28MPU-6050%29.step).
Its third-party redistribution rights are unspecified; no model binary is
committed. Model-bank validation ran successfully and remains `unverified`.

| Independent source | Outline, mm | Holes, mm | Header / sensor evidence |
|---|---|---|---|
| Pinned STEP | 20.35 × 15.65; total Z 2.50 | Ø3.00, pitch 16.35; centres 2.00 from top/side edges | Eight Ø1 bores, 2.54 pitch; first centre 1.285 from left, row 1.27 from bottom. IC-like feature centre (10.60,-6.75) in source coordinates; no pin-1 mark. |
| [Flux Workshop](https://fluxworkshop.com/products/bdaa100044-gy-521-mpu-6050) | 21 × 16 × 3 | M3, pitch 15.5 | 2.54 pitch; supplies straight and right-angle eight-pin strips. |
| [JK EMC GY-521 V1.1 drawing](https://www.jkelec.co.kr/img/sensors/manual/mpu6050_gy521/mpu6050_pcb.jpg) | 22.05 × 19.03 | Ø3, 2.5 edge offsets → pitch 17.05 | 2.54 pitch; 2.13/2.14 end margins. Different maker's board, distinct IC position and axis markings. |
| [OpenELAB](https://openelab.io/products/wheeltec-gy-521-mpu-6050) | Approx. 20.5 × 15.5 | Approx. Ø3.2; pitch not specified | Photograph establishes the familiar eight-pin variant, not a dimensioned row datum. |

Sources agree on an eight-position 2.54 mm row and disagree on the board outline,
hole diameter, hole pitch and header edge offsets. Hole-pitch spread is **1.55 mm**,
far beyond 0.2 mm. The catalog keeps the common 21 × 16-class vendor nominal
15.5 mm as **estimated**, while retaining the STEP's measured 16.35 mm separately.
There is no statistical proof that one spacing dominates all unbranded GY-521
boards. The owned board must decide the fabrication dimensions.

J1 now exposes all eight bare pads, in order VCC, GND, SCL, SDA, XDA, XCL, AD0,
INT. A loose straight or right-angle 2.54 mm strip may be fitted. The four-wire
project lead uses pads 1,2,4,3; only its B connector type changes in `robot.yaml`.

The STEP's IC-like feature is 4.5 × 4.5 × 0.25 mm and has no trustworthy package
pin-1 mark. [TDK's actual MPU-6050 package specification](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet.pdf)
is a 4 × 4 × 0.9 mm QFN. This is further evidence that the third-party STEP is
simplified; it cannot certify the sensor die axes or package height. The recorded
feature centre is a model measurement, and sensor-axis orientation stays estimated.

Measure the owned GY-521 with the component side facing you and the eight-pad row
at the bottom:

1. PCB width, height and thickness; highest component height above the PCB.
2. Both mounting-hole diameters. Measure each centre's X from the left edge and
   Y from the bottom edge, and the centre-to-centre spacing. Centre = edge of hole
   plus half its measured diameter; do not mix inner-edge and outer-edge spans.
3. Pad 1/VCC centre X from left edge and row Y from bottom edge; pad 8 centre X;
   first-to-last centre span (expected 17.78 mm), and drilled-hole diameter.
4. IC package width/height, package centre X/Y from the same edges, and which
   corner has the pin-1 dot. Record the printed X/Y arrows relative to that dot
   and the header. Calipers alone cannot establish sensor-axis polarity.
5. State whether the board uses bare pads, a straight strip, or a right-angle
   strip, and measure installed pin height/cable exit if a strip is fitted.

## Arducam UC-717

[Arducam's IMX296/B0445 documentation](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/Global-Shutter/1.58MP-IMX296/)
identifies the product but does not provide a dimensioned FPC mating-face origin.
The [UC-717 drawing request](https://forum.arducam.com/t/10317) has no supplier
mechanical drawing in its retrieved response. Therefore the catalog FPC origin
(0,-17,0) mm and -Y cable exit remain **estimated**, not vendor-confirmed. All four
mounting-hole provenance records are explicitly estimated; none is upgraded.

Measure PCB width/height/thickness, each mounting-hole diameter and centre X/Y,
the connector opening plane's distance from the nearest board edge, its lateral
centre, height above the PCB and cable-exit direction. Identify pin 1 and which
side of the ribbon exposes copper. A supplier STEP/drawing for the exact board
revision is preferable to inference from a photograph.
