# Lane D research: bought drivetrain and off-the-shelf electronics

Researched 2026-10-29. Every value has a source URL and a status:

- `datasheet`: manufacturer manual, datasheet, drawing or official product page.
- `vendor`: retailer or third-party listing (not the manufacturer).
- `estimated`: inferred from a related official source. The reasoning is given each time.
- `unknown`: not published anywhere I could find. No number is invented.

## Chosen coherent kit (summary)

The kit is a 3D-printed frame around the **Traxxas Slash 2WD (1/10 short-course) rear drivetrain**. Every
mechanical part is a Traxxas spare that fits the same 2WD rear driveline. The front is non-driven: Traxxas
2WD front wheels on axles with bearings.

| # | Role | Product | Part no. |
|---|------|---------|----------|
| 1 | Gearbox + diff | Traxxas Pro-Built Magnum 272 transmission (2WD, planetary diff) | 3695 |
| 2 | Motor | Traxxas Titan 12T 550 brushed | 3785 |
| 2b | Pinion / spur | Traxxas 48P 23T pinion (3 mm bore) / 86T spur | 2423 / 4686 |
| 3 | Driveline | Rear driveshaft assemblies, stub axles, stub axle carriers, output yokes | 6852X, 2753X + 2754, 3652/3752, 4628R |
| 4 | Wheels | Traxxas SCT pre-glued tire and wheel, rear (4WD f/r, 2WD rear) + 2WD-front set | 5878-BLK (rear), 5879-BLK (front) |
| 5 | Steering servo | Savox SC-0251MG (alt: Traxxas 2075, the Slash stock servo) | SC-0251MG |
| 6 | ESC | Hobbywing QuicRun WP 1060 brushed (6 V / 3 A BEC) | 30120200 |
| 7 | 5 V Pi supply | Pololu D36V50F5 (5 V, 5.5 A buck) | 4091 |
| 8 | GNSS | SparkFun MAX-M10S breakout; RTK option SparkFun GPS-RTK2 ZED-F9P | GPS-18037 / GPS-15136 |
| 9 | Battery | Gens Ace 5000 mAh 2S 60C, PC hard case, XT60 | GEA50002S60X6 |
| — | IMU (user-owned) | GY-521 MPU-6050 breakout | n/a (generic) |

Important compatibility notes (all sourced below):

- The Titan 12T has a **3 mm shaft, not 3.175 mm**. The Traxxas 2423 pinion "fits 3mm shaft". A 3.175 mm
  540 (e.g. Mabuchi RS-540) would need a 1/8" bore pinion.
- The QuicRun 1060 ships with a Tamiya connector, so it must be re-terminated to XT60 to match the Gens Ace
  pack. This is a connector swap only, not a custom PCB.
- The Hobbywing 1060 motor limit on 2S is ≥12T (540/550), so the Titan 12T is exactly at the limit. Its BEC
  (6 V / 3 A) matches the SC-0251MG's 6 V rating.
- The Traxxas XL-5 (the ESC sold with the Slash) has only a 1 A BEC and accepts 2S LiPo only, so the 1060
  was chosen instead.
- The Slash 2WD battery compartment is 165 × 50 × 23 mm. The Gens Ace pack (133 × 42 × 20 mm) fits the stock
  tray envelope. That envelope is useful as a design target for the printed tray.

---

## 1. Gearbox / transmission: Traxxas Pro-Built Magnum 272 (3695)

| Field | Value | Status | Source |
|---|---|---|---|
| Manufacturer / part | Traxxas 3695 "Pro-Built Magnum 272 Transmission", assembled | datasheet | https://traxxas.com/products/parts/3695 |
| Alternative | Traxxas 9495 Pro Series Magnum 272R transmission, complete (assembled) | vendor | https://www.bigboyswithcooltoys.ca/products/tra9495-traxxas-transmission-complete-assembled |
| Fits | Slash 2WD (58214-4, 58234-8), Bandit, Rustler, Stampede 2WD, BIGFOOT No. 1 | datasheet | https://traxxas.com/products/parts/3695 |
| Differential type | Planetary steel gear | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery |
| Differential spare | 2380 "Differential, complete (fits 1/10-scale 2WD Rustler, Bandit, Stampede, Slash)" | datasheet | https://traxxas.com/media/productattach/C-58234-8/3/58234-8_parts.pdf |
| Internal ratio | 2.72:1 | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery (also the manual formula "spur/pinion × 2.72 = final ratio") |
| Final drive (as listed) | 9.05 | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery |
| Gear pitch | 48 pitch (diametral). 48P ≈ module 0.529 (25.4/48, computed) | datasheet (pitch); computed (module) | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery |
| Spur options | 76, 83, 86, 90 T (48P); parts 4683 (83T), 4686 (86T), 4690 (90T) | datasheet | manual gear chart https://traxxas.com/media/productattach/C-58234-8/2/58234-8-om-en-r00.pdf ; parts list |
| Manual gear combos | 14T/90T (optional), 16T/90T, 23T/86T "high top speed" (included high-speed gearing) | datasheet | owner's manual, above |
| Final ratio examples (chart) | 23/86 = 10.17; 16/86 = 14.62; 16/90 = 15.30; 12/90 = 20.40 | datasheet | owner's manual gear chart |
| Slipper clutch | Yes (Torque-Control slipper, on the spur) | datasheet | owner's manual and parts list |
| Output type | Differential output yokes (U-joint) take the rear driveshafts: 4628R "Differential output yokes, black (2)"; CV option 9057X "Differential output shafts, steel-splined constant-velocity (2)" | datasheet | parts list PDF |
| Motor mount pattern | 2 × M3 on 25.0 mm centers (540/550 standard). Traxxas does not publish the plate drawing, but the manual says the gear chart includes "items … [that] fit 540 motor only", so the mount takes standard 540 cans | estimated (25 mm comes from the Mabuchi RS-540 drawing in §2) | https://static.chipdip.ru/lib/278/DOC006278739.pdf |
| Overall envelope (L × W × H) | unknown | unknown | not published by Traxxas |
| Chassis mounting-hole pattern | unknown (Traxxas publishes no drawing; it must be measured from the part) | unknown | — |
| Mass | unknown | unknown | — |
| STEP / CAD | none official (Traxxas publishes no CAD) | unknown | — |

Note: Traxxas lists 3695 as "out of stock" on its page (2026-10-29). 9495 (Magnum 272R) is the in-production
equivalent named by resellers.

## 2. Motor: Traxxas Titan 12T 550 (3785), plus pinion

| Field | Value | Status | Source |
|---|---|---|---|
| Part | Traxxas 3785 "Motor, Titan 12T (12-Turn, 550 size)" | datasheet | parts list PDF |
| Type | High-speed brushed, integrated cooling fan | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery ; owner's manual |
| Mass | 240 g (8.47 oz) | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery |
| Can diameter | 36 mm (1.41 in) | datasheet | same |
| Can length | 57 mm (2.25 in) | datasheet | same |
| Overall length (incl. shaft) | 78 mm (3.07 in) | datasheet | same |
| Shaft diameter | 3 mm (not 3.175 mm) | datasheet | same; pinion 2423 "fits 3mm shaft" in the parts list |
| Connector | Bullet | datasheet | same |
| Max voltage | up to 8.4 V (7-cell NiMH / 2S LiPo) | vendor | https://www.bigboyswithcooltoys.ca/collections/all/products/tra3785-motor-titan-12t-12-turn-550-size |
| Kv / no-load rpm | unknown (Traxxas does not publish it) | unknown | — |
| No-load current | unknown | unknown | — |
| Stall current | unknown | unknown | — |
| Winding resistance | unknown | unknown | — |
| Mount holes | 2 × M3, 25 mm centers | estimated (550 can mounts in the same plate as 540; see the RS-540 drawing below) | — |
| STEP | none official | unknown | — |

### Pinion

| Field | Value | Status | Source |
|---|---|---|---|
| Part | Traxxas 2423 "Gear, 23-T pinion (48-pitch) (fits 3mm shaft)/ set screw" | datasheet | parts list PDF |
| Alternatives | 2416 (16T), 1918 (18T), 2419 (19T), 4725 (25T), 2426 (26T), 4728 (28T), 2431 (31T), all 48P | datasheet | parts list PDF |
| Bore | 3 mm | datasheet | parts list PDF |
| Set screw size | unknown (included, size not stated) | unknown | — |
| Matching spur | 4686 "Spur gear, 86-tooth (48-pitch)" | datasheet | parts list PDF |

### Reference 540 can (mount pattern and electrical data, datasheet-grade): Mabuchi RS-540SH

Mabuchi publishes a full datasheet for its 540 can (the Tamiya 540 is a Mabuchi RS-540 type). The table
covers industrial windings, not RC 7.2 V windings. Use it for the mount pattern and can geometry, not to
model the Titan.

| Field | Value | Status | Source |
|---|---|---|---|
| Mount | ISO M3.0 × 0.5 tapped, 2 places, 25.0 mm centers; usable screw length 5.0 mm max from mounting face | datasheet | https://static.chipdip.ru/lib/278/DOC006278739.pdf |
| Shaft | Ø3.17 mm | datasheet | same |
| Can | Ø35.8 mm, body length 50.0 mm, 67.0 mm overall; front boss Ø13.0 mm | datasheet | same |
| Mass | 160 g (approx.) | datasheet | same |
| RS-540SH-5045 (12 V nominal) | no-load 17,500 rpm @ 0.95 A; max efficiency 15,080 rpm, 5.93 A; stall 230 mN·m @ 37.0 A | datasheet | same |

## 3. Driveline: driveshafts, axles, carriers, hubs (Traxxas Slash 2WD rear)

| Field | Value | Status | Source |
|---|---|---|---|
| Rear driveshafts | 6852X "Driveshaft assembly, rear, heavy duty (1) (left or right)"; 6852R "Driveshafts, rear, steel-spline constant-velocity (complete)" | datasheet | parts list PDF https://traxxas.com/media/productattach/C-58234-8/3/58234-8_parts.pdf |
| Half shafts | 6750 external-splined / 6751 internal-splined (steel-spline CV) | datasheet | parts list PDF |
| Diff output yokes | 4628R (black, with 3 × 5 mm countersunk screws); 4628X hardened steel with U-joints | datasheet | parts list PDF |
| Rear stub axles | 2753X "Stub axles, rear (2)" + 2754 "Stub axle pins (4)" | datasheet | parts list PDF |
| Stub axle length / dia | 42 mm long; 13 mm max diameter | vendor | https://ca.wrekd.com/products/traxxas-2753x-rear-stub-axles-2-bandit-stampede-slash |
| Stub axle carriers | 3752 (requires 5 × 11 × 4 mm bearings); 3652-series aluminium (with 5 × 11 mm bearings) | datasheet | parts list PDF |
| Carrier bearings | 5 × 11 × 4 mm (Traxxas 5116) | datasheet | parts list PDF |
| Front (non-driven) axles | 3637 "Axles (front) (2)" with 5 × 11 × 4 mm bearings | datasheet | parts list PDF |
| Wheel drive interface (rear) | 12 mm hex | vendor (Traxxas does not state the hex size in the parts list) | https://www.bigboyswithcooltoys.ca/products/tra6875-wheels-sct-chrome-green-beadlock-style-dual-profile-2-2-outer-3-0-inner-2-4wd-front-rear-2wd-rear-only |
| Separate 12 mm hex hub part no. | unknown (no "hex hub" part is listed for Slash 2WD in the parts list; how the hex is formed, as a hub or on the stub axle, is not documented) | unknown | — |
| Driveshaft lengths | unknown | unknown | — |
| Wheel nuts | 1747 "Nuts, 4mm nylon locking (10)"; 1747A/G aluminium flanged serrated 4 mm | datasheet | parts list PDF |
| Nut thread pitch | unknown for 1747 (Traxxas lists M4 × 0.7 only for the 5854 17 mm-hub retainer) | unknown | parts list PDF |
| STEP | none official | unknown | — |

## 4. Wheels / tires (pre-mounted)

| Field | Value | Status | Source |
|---|---|---|---|
| Rear (driven) | Traxxas 5878-BLK tire and wheel assy, glued (SCT black beadlock wheels, BFGoodrich Mud-Terrain T/A KM2, foam inserts) (2) "(4WD front/rear, 2WD rear only)" | datasheet | parts list PDF |
| Front (2WD, bearings) | Traxxas 5879-BLK, same style "(2WD front)" | datasheet | parts list PDF |
| Wheel size | 2.2 in outer / 3.0 in inner bead, dual profile SCT | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery (written there as 2"/3"); Traxxas part names "2.2" outer 3.0" inner" via https://www.bigboyswithcooltoys.ca/products/tra5866-wheels-sct-chrome-green-beadlock-style-dual-profile-2-2-outer-3-0-inner-2-2wd-front-only |
| Tire outer diameter | 110 mm (4.3 in) | vendor | https://www.bigboyswithcooltoys.ca/products/tra5878-tires-wheels-assembled-glued-sct-satin-chrome-black-beadlock-style-wheels-bfgoodrich-mud-terraintm-km2-tires-foam-inserts-24wd-front-rear-2wd-rear-only |
| Tire width | 44 mm (1.73 in) | vendor | same |
| Hex | 12 mm (one vendor listing for 5878 says 14 mm, which contradicts its own fitment; the 12 mm value comes from the sister SKU 6876 and from Pro-Line's "Traxxas wheels feature 12mm hexes") | vendor | https://www.bigboyswithcooltoys.ca/products/tra6876-tires-wheels-assembled-glued-sct-chrome-green-beadlock-wheel-bfgoodrich-mud-terraintm-t-a-km2-tire-foam-inserts-2-4wd-front-rear-2wd-rear-only |
| Hex depth | unknown | unknown | — |
| Mass | unknown | unknown | — |
| Nut | 4 mm nyloc (1747) | datasheet | parts list PDF |
| Vehicle track | 296 mm front and rear (Slash 2WD) | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery |
| Wheelbase | 335 mm | datasheet | same |

## 5. Steering servo: Savox SC-0251MG (alt. Traxxas 2075)

Savox's official sites (savoxusa.com, savox-servo.com) could not be reached (TLS failure / no spec page), so
the values below are secondary.

| Field | Value | Status | Source |
|---|---|---|---|
| Part | Savox SC-0251MG, standard-size digital, metal gear, brushed 3-pole motor | vendor | https://redlineperformance.co.nz/product/savox-sc-0251mg-metal-gear-std-size-digital-servo-savox-size-16kgcm-018-sec |
| Torque 4.8 V / 6.0 V | 13.0 kg·cm (180.5 oz-in) / 16.0 kg·cm (222.2 oz-in) | vendor | https://servodatabase.com/servo/savox/sc0251mg |
| Speed 4.8 V / 6.0 V | 0.20 s/60° / 0.18 s/60° | vendor | same |
| 7.4 V rating | not rated (6.0 V max model; use the HV SV-series for 7.4 V) | vendor | same |
| Dimensions | 40.7 × 20 × 42.4 mm (note: taller than a standard ~38 mm servo) | vendor | https://store.hobbyetc.com/parts/view/168168 |
| Mass | 61 g | vendor | same |
| Spline | 25T | vendor | https://servodatabase.com/servo/savox/sc0251mg |
| Ear-hole pattern | unknown | unknown | — |
| Stall / idle current | unknown (not published for SC-0251MG; the sister SC-0252MG is listed at 1.7 A stall @ 6 V, 6 mA idle, which does not apply to this model) | unknown | https://www.servocity.com/sc0252mg-standard-digital-servo |
| STEP | unknown | unknown | — |

Alternative, Traxxas 2075 (the Slash's stock "digital high-torque waterproof" servo, fits the Slash servo
saver 3744X/6845X): 125 oz-in @ 6.0 V, 0.17 s/60° @ 6.0 V, 55.1 × 20.1 × 38.1 mm, 45 g, plastic gears
(status: vendor, https://servodatabase.com/servo/traxxas/2075). Spline, ear pattern and stall current: unknown.

## 6. ESC: Hobbywing QuicRun WP 1060 Brushed (30120200)

| Field | Value | Status | Source |
|---|---|---|---|
| Part | Hobbywing QuicRun WP 1060 Brushed, product number 30120200 | datasheet | https://www.hobbywing.com/en/products/quicrun-wp-1060-brushed55 |
| Fwd cont. / peak | 60 A / 360 A | datasheet | same; manual https://www.hobbywing.com/en/uploads/file/20240315/4806bbdbab8eb9fea0bb26da87d81b00.pdf |
| Rev cont. / peak | 30 A / 180 A | datasheet | manual |
| Input | 2–3S LiPo / 6–9 cell NiMH (manual table: 5–9 NiMH) | datasheet | product page; manual |
| Motor limit | 2S LiPo: 540/550 ≥12T or <30,000 rpm @ 7.2 V; 3S: ≥18T or <20,000 rpm | datasheet | manual |
| BEC | 6 V / 3 A switch mode | datasheet | product page; manual |
| Dimensions | 36.5 × 32.0 × 18.0 mm | datasheet | product page |
| Mass | 39 g | datasheet | product page |
| Input connector | Tamiya (large). **Re-terminate to XT60** for this kit | datasheet | product page |
| Motor connectors | 4.0 mm bullet (female) | datasheet | product page |
| Wires | 16 AWG, 100 mm (input and output) | datasheet | product page |
| LVC | LiPo mode: power reduced below 3.25 V/cell for 2 s, cut-off below 3.0 V/cell | datasheet | manual |
| Thermal protection | Power reduced above 105 °C, restored below 65 °C | datasheet | manual |
| Programming | Jumper caps (running mode F/B/R or F/R, battery type) | datasheet | manual |
| Mounting holes | none (double-sided tape mount; no holes listed) | datasheet (none listed) | product page |
| STEP | none | unknown | — |

For reference, the stock Traxxas XL-5 (from the official owner's manual): input 4–7 NiMH or 2S LiPo; case
1.23 × 2.18 × 0.75 in; 57 g; motor limit 15T (540) / 12T (550); on-resistance 0.005 Ω forward / 0.014 Ω
reverse; peak 100 A forward / 60 A reverse; brake 60 A; continuous 15 A; BEC 6.0 V / 1 A; Traxxas
High-Current connector (https://traxxas.com/media/productattach/C-58234-8/2/58234-8-om-en-r00.pdf). The kit
product page instead lists 31 × 55 × 26 mm and 69 g, which disagrees with the manual
(https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery).

## 7. 5 V supply for the Raspberry Pi 5: Pololu D36V50F5 (#4091), verified

| Field | Value | Status | Source |
|---|---|---|---|
| Part | Pololu 5V, 5.5A Step-Down Voltage Regulator D36V50F5, item #4091 | datasheet | https://www.pololu.com/product/4091 |
| Input range | 5.5 V – 50 V (minimum subject to dropout) | datasheet | https://www.pololu.com/product/4091/specs |
| Output | fixed 5 V, ±4 % accuracy | datasheet | product page / specs |
| Continuous current | 5.5 A typical max, at 36 V in. Thermally limited; range 3.5–8 A depending on input voltage | datasheet | specs; product page |
| Efficiency | 80 – 95 % typical (depends on Vin, Vout, load; curves on the product page) | datasheet | product page |
| Quiescent current | 4 mA max (enabled, no load); sleep ~10–20 µA per volt of VIN via EN | datasheet | specs; product page |
| Dropout | linear in load; values only in a graph | datasheet (graph only) | product page |
| Switching frequency | ~500 kHz at heavy load | datasheet | product page |
| Protections | reverse voltage to −40 V, over-current, short-circuit, over-temperature | datasheet | specs; product page |
| Extra pins | EN (sleep <1.2 V, wake >1.35 V), PG open-drain (needs pull-up) | datasheet | product page |
| Dimensions | 25.4 × 25.4 mm board (1 × 1 in); 9.5 mm tall (0.375 in) without headers; PCB 1.57 mm | datasheet | specs; drawing https://www.pololu.com/file/0J1732/d36v50fx-step-down-voltage-regulator-dimensions.pdf |
| Mass | 7.0 g (without headers) | datasheet | specs |
| Mounting holes | 3 × Ø2.18 mm (0.086 in), for #2 or M2 screws; centers 2.2 mm from the board edges, 21.1 mm apart in X and Y (three corners) | datasheet | dimension drawing |
| Connector pitch | 2.54 mm pin header (12 × through-holes on 1.27 mm offset rows per drawing) | datasheet | dimension drawing |
| STEP | https://www.pololu.com/file/0J1733/d36v50fx-step-down-voltage-regulator.step | datasheet | https://www.pololu.com/product/4091/resources |
| Drill guide DXF | https://www.pololu.com/file/0J1734/reg24d-drill.dxf | datasheet | same |

Verdict: correct choice. Two cautions. First, the Raspberry Pi 5 asks for 5.1 V / 5 A, and this board's
5 V ±4 % is at the low edge; Pi 5 will cap USB current unless it detects a 5 A PD supply (Pi config, not
hardware). Second, from a 2S pack (≈6.0–8.4 V) the headroom above 5 V is small at high load, so check the
dropout graph on the product page.

## 8. GNSS

### 8a. SparkFun GNSS Receiver Breakout MAX-M10S (Qwiic), GPS-18037

| Field | Value | Status | Source |
|---|---|---|---|
| Part | SparkFun GPS-18037, u-blox MAX-M10S | datasheet | https://www.sparkfun.com/sparkfun-gnss-receiver-breakout-max-m10s-qwiic.html |
| Board | 38.1 × 30.5 mm (1.50 × 1.20 in); 44.2 × 30.5 mm (1.74 in) with the SMA connector (+0.24 in) | datasheet | drawing https://cdn.sparkfun.com/assets/e/8/8/9/8/SparkFun_u-blox_MAX-M10S_Board_Dimensions.png |
| Mounting holes | 4 holes, centers 0.10 in (2.54 mm) from each edge → 33.0 × 25.4 mm (1.30 × 1.00 in) rectangle | datasheet | same drawing |
| Hole diameter | unknown (not labeled on the drawing) | unknown | — |
| Supply | 3.3 V VCC and I/O (module VCC 1.76–3.6 V abs. operating) | datasheet | product page; https://content.u-blox.com/sites/default/files/MAX-M10S_DataSheet_UBX-20035208.pdf |
| Current | ~6–25 mA; 25 mW continuous tracking | vendor (SparkFun) / datasheet (25 mW) | product page; u-blox datasheet |
| Update rate | default config (GPS+GAL+BDS B1I): 3 Hz default, 12 Hz high-performance mode. GPS+GAL: 10/20 Hz. Single GNSS: 18/25 Hz | datasheet | u-blox datasheet R08 Table 1/3 |
| CEP | 1.5 m (CEP 50 %, 24 h static, −130 dBm) | datasheet | u-blox datasheet |
| Default UART baud | 38400 | datasheet (SparkFun hookup guide) | https://learn.sparkfun.com/tutorials/gnss-receiver-breakout---max-m10s-qwiic-hookup-guide/all |
| Interfaces | 2 × Qwiic (I²C), UART on 0.1 in header, PPS, EINT, RST | datasheet | product page / drawing |
| Antenna | SMA (external antenna required) | datasheet | product page |
| CAD | Eagle: https://cdn.sparkfun.com/assets/c/5/9/6/8/SparkFun_u-blox_GNSS_MAX-M10S_Board_Files_v10.zip ; repo https://github.com/sparkfun/SparkFun_u-blox_MAX-M10S | datasheet | product page |
| STEP | not listed | unknown | — |

### 8b. RTK: SparkFun GPS-RTK2 Board ZED-F9P (Qwiic), GPS-15136

| Field | Value | Status | Source |
|---|---|---|---|
| Part | SparkFun GPS-15136, u-blox ZED-F9P | datasheet | https://www.sparkfun.com/sparkfun-gps-rtk2-board-zed-f9p-qwiic-gps-15136.html |
| Board | 43.5 × 43.2 mm (drawing: 1.70 × 1.70 in) | datasheet | product page; drawing https://cdn.sparkfun.com/assets/7/d/f/a/c/SparkFun_RTK2_ZED-F9P_Dimensions.jpg |
| Mounting holes | 4 holes on a 38.1 × 38.1 mm (1.50 × 1.50 in) square | datasheet | drawing |
| Hole diameter | unknown (not labeled) | unknown | — |
| Mass | 6.8 g | datasheet | product page |
| Supply | 5 V or 3.3 V; logic 3.3 V | datasheet | product page |
| Current | 68–130 mA | datasheet | product page |
| Update rate | PVT 25 Hz, RTK 20 Hz, raw 25 Hz | datasheet | product page |
| Accuracy | 2.5 m without RTK; 0.010 m with RTK | datasheet | product page |
| Default UART baud | 38400 | vendor | https://learn.sparkfun.com/tutorials/whats-the-difference-between-the-zed-f9p-and-the-zed-x20p/hardware-changes |
| Connectors | 2 × Qwiic, USB-C (COM port), U.FL antenna (active L1/L2 antenna required), 0.1 in headers (UART1, UART2/RTCM, SPI) | datasheet | product page / drawing |
| CAD | Eagle: https://cdn.sparkfun.com/assets/0/3/c/a/4/Qwiic_GPS-RTK2_-_ublox_ZED-F9P.zip ; schematic https://cdn.sparkfun.com/assets/9/a/0/0/a/Qwiic_GPS-RTK2_-_ublox_ZED-F9P.pdf | datasheet | product page |
| STEP | not listed | unknown | — |

Not researched in depth: Holybro M10, Matek M10Q-5883, Beitian BN-220/880 and ArduSimple simpleRTK2B
(JST-GH options with integrated patch antennas). SparkFun was chosen because it publishes drawings and
Eagle files.

## 9. 2S LiPo: Gens Ace 5000 mAh 7.4 V 60C 2S1P, PC case, XT60 (GEA50002S60X6)

| Field | Value | Status | Source |
|---|---|---|---|
| Part | Gens Ace 5000mAh 7.4V 60C 2S1P LiPo, PC material case, XT60 plug | datasheet | https://www.gensace.de/gens-ace-5000mah-7-4v-60c-2s1p-lipo-pc-material-case-with-xt60-plug.html (official Gens Ace EU store; returned 404 on direct fetch, figures from search snapshot) |
| Capacity / voltage | 5000 mAh / 7.4 V (2S1P) | datasheet | same |
| C rating | 60C (the page also says "50C" once; conflict). Burst 100C per a reseller | datasheet / vendor (burst) | same; https://readymaderc.com/products/details/gensace-5000mah-7-4v-60c-2s1p-short-lipo-battery-pack-xt60 |
| Dimensions | 133 × 42 × 20 mm (reseller: 135 × 42.7 × 22.2 mm) | datasheet / vendor | same two URLs |
| Mass | 247 g ±10 g (reseller: 275 g ±20 g) | datasheet / vendor | same |
| Connector | XT60; balance JST-XH; 12 AWG | datasheet | gensace.de |
| Fit | Slash battery compartment is 165 × 50 × 23 mm | datasheet | https://traxxas.com/58234-8-slash-2wd-xl-5-wbattery |

Alternative: Traxxas 2843X 5800 mAh 2S 25C, Traxxas iD connector (not XT60), 12 AWG; 135–137 × 43–45 ×
24–25 mm, 290–308 g (vendor: https://ozrc.com.au/products/traxxas-2843x-5800mah-7-4v-2-cell-25c-lipo-battery).

## 10. IMU: GY-521 MPU-6050 breakout (user-owned)

There is no single manufacturer (generic board), so every board value is `vendor`.

| Field | Value | Status | Source |
|---|---|---|---|
| Board size | ≈20.5 × 15.5 mm (other listings 21 × 16 mm, 21 × 15 mm) | vendor | https://openelab.io/products/wheeltec-gy-521-mpu-6050 |
| Mounting holes | 2 holes, ≈Ø3.2 mm | vendor | same |
| Hole spacing | unknown (measure the owned board) | unknown | — |
| Supply (board) | 3.3–5 V DC (onboard regulator); I/O not 5 V-tolerant per one listing | vendor | same |
| I²C address | 0x68 (AD0 low) / 0x69 (AD0 high) | vendor | same |
| Mass | ≈1.8 g | vendor | same |
| Chip supply | MPU-6050 VDD 2.375–3.46 V (the board regulator provides this) | datasheet (InvenSense PS-MPU-6000A-00; not re-fetched this session) | https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet1.pdf |

## Open items (must be measured, not published)

- Magnum 272 envelope, chassis bolt pattern and mass. Measure the purchased 3695/9495 or use a community scan
  (no official CAD exists).
- Driveshaft lengths (6852X), hex-hub geometry and hex depth, wheel/tire mass.
- Titan 12T Kv, no-load current, stall current and resistance. Measure on the bench (no published values).
- SC-0251MG ear-hole pattern and stall current; GY-521 hole spacing; SparkFun hole diameters (read them from
  the Eagle files).
