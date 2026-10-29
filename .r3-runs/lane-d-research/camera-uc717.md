# Arducam UC-717 — camera identification (lane D research, 2026-10-29)

Source-type legend: **OFFICIAL-DOC** = docs.arducam.com / raspberrypi datasheets; **OFFICIAL-STAFF** =
Arducam support staff ("Dion") on forum.arducam.com; **VENDOR** = product listing (Arducam store mirror on
uctronics.com, which is Arducam's parent store, or third-party retailers); **USER** = forum user post;
**estimated** = not published, derived with stated reasoning.
Note: www.arducam.com product pages return HTTP 403 to scripted fetches; their content was read via the
UCTRONICS mirror (same text, same SKU) and search snippets.

```yaml
identification:
  board_code: UC-717 (seen as "UC-717 Rev.D1")
  sensor: Sony IMX296 (1.58 MP colour global shutter)
  confidence: high
  evidence:
    - "Arducam staff: 'I can confirm your UC-717 Rev.D1 is using the Sony IMX296 sensor ... 2-lane MIPI CSI-2'"
      https://forum.arducam.com/t/10328  (OFFICIAL-STAFF, 2025-06-03)
    - "user lists 'imx296 (UC-717)'; staff: 'the imx296 (UC-717) is not currently supported by Jetson'"
      https://forum.arducam.com/t/12618  (OFFICIAL-STAFF, 2023-11-23)
  product_most_likely:
    name: "Arducam 1.58MP IMX296 Color Global Shutter Camera Module with M12 Lens for Raspberry Pi"
    sku: B0445
    why: >-
      B0445 is the only Arducam Raspberry Pi IMX296 SKU in the official IMX296 doc's product table; staff
      link UC-717 to that same doc page. Not stated verbatim anywhere that "B0445 PCB = UC-717".
    source: https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/Global-Shutter/1.58MP-IMX296/ (OFFICIAL-DOC)
  other_candidates_same_sensor:
    - sku: B0619   # mono IMX296, M12, listed with a 96 deg lens (search snippet of arducam.com mono page; VENDOR)
      url: https://www.arducam.com/1-58mp-imx296-mono-global-shutter-camera-module-with-m12-lens-for-raspberry-pi.html
    - sku: B0456   # IMX296 colour for DepthAI OAK (not a Pi board) (VENDOR snippet)
      url: https://www.arducam.com/1-58mp-imx296-color-global-shutter-camera-module-for-depthai-oak.html
  action: confirm with the user that the board is colour (Bayer) and the lens reads M2506ZH04 / 6 mm.

sensor:                       # all OFFICIAL-DOC unless noted
  part: IMX296 (Sony IMX296LQR-C colour per RPi GS brief)
  shutter: global
  effective_pixels: 1440 x 1080         # Arducam doc
  libcamera_full_mode: 1456 x 1088      # rpicam --list-cameras output, Pi 5 (USER log, forum t/9520, RPi GS cam, same driver)
  pixel_size_um: 3.45 x 3.45            # UCTRONICS listing + RPi GS brief; Arducam doc page says "3.4um" (rounding)
  optical_format: 1/2.9"  (6.3 mm diagonal)
  active_area_mm: 4.97 x 3.73 (effective 1440x1080) / 5.02 x 3.75 (1456x1088)   # estimated = pixels x 3.45 um
  cfa: Bayer RGGB (Arducam table says "Quad-Bayer RGB"; libcamera reports SRGGB10 = standard Bayer)
  output: RAW10
  sources:
    - https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/Global-Shutter/1.58MP-IMX296/ (OFFICIAL-DOC)
    - https://www.uctronics.com/1-58mp-imx296-color-global-shutter-camera-module-with-m12-lens-for-raspberry-pi.html (VENDOR, Arducam store)
    - https://datasheets.raspberrypi.com/gs-camera/gs-camera-product-brief.pdf (OFFICIAL-DOC, RPi GS camera, same sensor)

lens:                         # B0445 stock lens
  model: Arducam M2506ZH04 (M12 x0.5 / S-mount, replaceable)      # VENDOR (uctronics)
  focal_length_mm: 6                                             # OFFICIAL-DOC + VENDOR
  aperture: F2.0                                                 # VENDOR (uctronics "F.NO F2.0")
  fov_deg: {diagonal: 55, horizontal: 45, vertical: 33}          # OFFICIAL-DOC table "55°(D)x45°(H)x33°(V)"
  fov_check: "f=6 mm on 4.97x3.73 mm -> H 44.9, V 34.5, D 54.7 deg (estimated, pinhole) — consistent"
  fov_conflicts: some listings say 55 deg HFOV or 66 deg HFOV (search snippets); trust the doc's 45 deg H
  focus: manual (threaded M12 lens, locking ring)
  ir: integral IR-cut filter, visible only
  lens_holder_height_mm: 13                                      # VENDOR (uctronics "13mm Lens Holder")
  lens_mass_g: 5                                                 # VENDOR (RobotShop M2506ZH04 listing)
  lens_size_mm: 14 dia x 17.4 long                               # VENDOR (RobotShop M2506ZH04 listing)
  lens_sources:
    - https://www.arducam.com/product/m2506zh04-2/ (VENDOR, Arducam; 403 to scripts)
    - https://www.robotshop.com/products/arducam-1-25-m12-mount-6mm-focal-length-camera-lens-m2506zh04 (VENDOR)

video_modes_libcamera:
  - 1456x1088 SRGGB10_CSI2P @ 60.38 fps (full-res, the only sensor mode)   # USER log, Pi 5, forum t/9520
  - 1440x1080 @ 60 fps RAW10                                              # OFFICIAL-DOC / VENDOR spec
  - lower resolutions: ISP-scaled from the single sensor mode; higher fps only via cropping (not documented by Arducam)

interface:
  csi: MIPI CSI-2, 2 lanes, ~594 Mbps/lane quoted by staff (libcamera log shows 1188 Mbps link rate)  # OFFICIAL-STAFF t/10328; USER t/9520
  onboard_connector: 22-pin 0.5 mm pitch FPC ("mini")   # estimated-high: kit ships 15-22 and 22-22 cables
                                                       # (only meaningful for a 22-pin board); B0445 user calls it
                                                       # "the 22-pin FPC connector on the back side" (USER t/10317)
  included_cables:                                      # VENDOR (uctronics)
    - 150 mm 15pin(1.0mm)-22pin(0.5mm) FPC   # for Pi 4/3 (15-pin standard CSI)
    - 150 mm 22pin-22pin FPC                 # for Pi 5 / Zero / CM (22-pin mini CSI)
  pi5_note: >-
    Pi 5 has two 22-pin 0.5 mm 4-lane "mini" CAM/DISP ports. Because the board itself is 22-pin, use the
    INCLUDED 150 mm 22-22 cable on Pi 5 — no 15->22 cable needed. (For 15-pin boards like the official GS/HQ
    cameras, the official "Raspberry Pi Camera Cable Standard–Mini", 22-way 0.5 mm to 15-way 1 mm, exists in
    200 mm SC1128 / 300 mm SC1129 / 500 mm SC1130 — part numbers from VENDOR URLs:
    https://www.pakronics.com.au/products/raspberry-pi-camera-cable-standard-mini-200mm-rpi-sc1128 ,
    https://www.adafruit.com/product/5818 ). Mind the 22-22 cable orientation (contacts face the PCB);
    Arducam staff note a "straight" vs "opposite" 22-22 cable matters (OFFICIAL-STAFF t/12618).

mechanical:
  board_outline_mm: 38 x 38                                   # VENDOR (uctronics "Camera Board Size 38 x 38mm")
  official_drawing: NOT PUBLISHED — a B0445 user requested drawing + STEP on 2026-08-09, unanswered
                    https://forum.arducam.com/t/mechanical-drawing-for-b0445/10317 (USER)
  step_model_url: unknown (none found on docs.arducam.com / product page)
  mounting_holes: unknown (official)
    estimated: 4x dia 2.5 mm (M2.5 clearance, or M2 screws), 30 x 30 mm square pattern centred, 4 mm from edges
    reasoning: >-
      Arducam 38x38 Pi boards copy the Raspberry Pi HQ/GS 38x38 footprint; the RPi GS drawing shows 4x dia 2.5
      holes on a 30 mm pitch on a 38 mm board. MEASURE before printing the bracket.
    reference: https://datasheets.raspberrypi.com/gs-camera/gs-camera-mechanical-drawing.pdf (OFFICIAL-DOC, different board)
  pcb_thickness_mm: unknown; estimated 1.6 (standard Arducam PCB)
  lens_height_above_board_mm: unknown; estimated ~20-23 (13 mm holder + lens protruding ~7-10 mm, focus-dependent)
  lens_axis: estimated board-centred (typical M12 holder layout; unverified)
  mass_g: unknown; estimated ~12-18 g (PCB ~5-8 g + M12 holder ~2-3 g + lens 5 g [VENDOR])
  connector_side: back of PCB (USER t/10317)

power:
  supply: 3.3 V from the Pi CSI connector (no separate power input)   # estimated: standard for Pi CSI cameras
  current_or_power: unknown (not published by Arducam or RPi for IMX296 boards)
  estimated: <0.5 W, ~100-150 mA @ 3.3 V — reasoning: small global-shutter sensor + regulators, comparable Pi
             camera boards are in this range; budget 1 W to be safe

software:
  raspberry_pi_os: Bookworm (rpicam-apps / libcamera / Picamera2) on Pi 5; Bullseye/Buster also on Pi 4 and older
  driver: in-kernel imx296 (no extra driver)       # "natively supported by Raspberry Pi" (VENDOR + OFFICIAL-DOC)
  config.txt (/boot/firmware/config.txt):          # OFFICIAL-DOC
    - camera_auto_detect=0
    - dtoverlay=imx296          # CAM1 (default)
    - dtoverlay=imx296,cam0     # if plugged into CAM0 on Pi 5
  pi5_compatible: yes (doc support table: Raspberry Pi 5 / Bookworm ✓)
  jetson: not supported (OFFICIAL-STAFF t/12618)
```

## Source list
- Arducam IMX296 doc (OFFICIAL-DOC): https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/Global-Shutter/1.58MP-IMX296/
- UC-717 = IMX296, staff confirmation: https://forum.arducam.com/t/10328 and https://forum.arducam.com/t/12618
- B0445 product (Arducam/UCTRONICS store): https://www.uctronics.com/1-58mp-imx296-color-global-shutter-camera-module-with-m12-lens-for-raspberry-pi.html
- B0445 on arducam.com (403 to scripts): https://www.arducam.com/product/1-58mp-imx296-color-global-shutter-camera-module-with-m12-lens-for-raspberry-pi/
- B0445 mechanical drawing request (none published): https://forum.arducam.com/t/mechanical-drawing-for-b0445/10317
- Pi 5 libcamera mode log (imx296 1456x1088 @ 60.38 fps): https://forum.arducam.com/t/9520
- RPi GS camera brief + drawing (reference footprint only): https://datasheets.raspberrypi.com/gs-camera/gs-camera-product-brief.pdf ,
  https://datasheets.raspberrypi.com/gs-camera/gs-camera-mechanical-drawing.pdf
