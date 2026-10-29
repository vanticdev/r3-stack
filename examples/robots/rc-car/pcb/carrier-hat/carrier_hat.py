"""Design intent for the R3 RC-car carrier HAT (catalog type ``r3-carrier-hat``).

Everything an engineer decides before driving KiCad: official library parts,
the net on every pin, the schematic arrangement, footprint placement, net
classes and the hand-routed power copper. ``r3_kicad.flow`` drives KiCad
(eeschema, pcbnew over the IPC API, Freerouting, kicad-cli) from this intent.

Board frame: millimetres, origin at the top-left corner, y down, GPIO edge at
the top. Component frame (catalog): origin at the board centre, +x right, +y
toward the GPIO edge.
"""

from __future__ import annotations

from dataclasses import replace

from r3_kicad import ipc2152
from r3_kicad.intent import BoardIntent, NetClass, Part, Placement, PowerTrack, Via, Zone
from r3_kicad.regulators import pi_rail, servo_rail

WIDTH, HEIGHT = 65.0, 70.0  # 13.5 mm south extension; original HAT mounting / GPIO datum retained
HOLES = ((3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5))   # HAT spec, M2.5, 58 x 49 mm pattern
GPIO_PIN1 = (32.5 - 9.5 * 2.54, 3.5 + 1.27)                     # header centred at x 32.5, rows 3.5 -/+ 1.27 mm from the edge

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
R0402 = "Resistor_SMD:R_0402_1005Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"
YAGEO = "Yageo"
MURATA = "Murata"

# Catalog r3-carrier-hat J_GPIO map plus the failsafe heartbeat on pin 13 (GPIO27).
GPIO_NETS = {"1": "3V3", "2": "5V", "3": "I2C_SDA", "4": "5V", "5": "I2C_SCL", "6": "GND", "7": "GPS_TX", "8": "CRSF_TX",
             "9": "GND", "10": "CRSF_RX", "11": "ENC", "12": "PI_ESC_PWM", "13": "HEARTBEAT", "14": "GND", "17": "3V3",
             "20": "GND", "25": "GND", "29": "GPS_RX", "30": "GND", "34": "GND", "35": "PI_SERVO_PWM", "39": "GND"}


P = Placement
# Board placement: footprint origin (board mm, y down), rotation CCW in degrees, side. Two-terminal parts at
# 90 degrees have pad 1 at the bottom, at 270 at the top; at 180 pad 1 is on the right. Floor plan:
#   top-left      5.1 V buck: L1 output column (C8-C11) feeds the Pi 5 V pins around H1, U2 + network at y 8-17
#   left edge     J6 GPS, J7 encoder (side entry, cables leave -x); C12 5 V hold-up beside them
#   bottom-left   6.0 V servo buck-boost: power row Cout | Q5 Q4 | R21 | Q3 Q2 | Cin (y 48) above J3/J4, L2 above
#                 the row, the LM5175 (U3, rotated so its gate drivers face the row) and its network above L2
#   bottom-right  P-FET, Kelvin shunt, TVS, input bulk C3; INA226 above the shunt
#   south extension  five side-entry harness connectors, clear of the Pi USB/Ethernet block
#   top-right     failsafe arbiter U4; former onboard IMU removed for the owned GY-521
#   right-middle  ESC PWM digital isolator U6 (ISO6720F) and its ESC-side LDO U7: the ESC-side ground is not HAT GND
PLACEMENT: dict[str, Placement] = {
    # connectors
    "J2": P(49.0, 57.0, 180), "J5": P(28.0, 66.3, 0), "J6": P(3.3, 22.5, 270), "J7": P(10.0, 30.5, 270),
    "J3": P(10.54, 64.9, 270), "J4": P(20.54, 64.9, 270), "J8": P(36.0, 25.0, 90),
    # 5.1 V buck (U2 at 90 deg: SW pins 19/20 face L1, VIN pins 2-5 face down, FB/EN/SS/MODE face up)
    "U2": P(24.0, 13.0, 90), "L1": P(15.5, 13.0, 180), "C13": P(21.3, 16.2, 180), "C14": P(20.9, 11.2, 90),
    "R7": P(20.7, 7.9, 270), "R8": P(22.3, 7.9, 90), "R5": P(23.9, 7.9, 270), "C15": P(25.5, 7.9, 270),
    "R6": P(27.1, 7.9, 90), "C16": P(27.7, 10.9, 0), "R9": P(30.0, 11.2, 90), "D3": P(32.0, 12.0, 270),
    "C7": P(26.6, 16.4, 0),
    "C4": P(21.6, 20.1, 270), "C5": P(24.2, 20.1, 270), "C6": P(26.8, 20.1, 270),
    "C8": P(7.5, 9.0, 180), "C9": P(7.5, 11.4, 180), "C10": P(7.5, 13.8, 180), "C11": P(7.5, 16.2, 180),
    "C12": P(14.5, 23.0, 0), "R20": P(14.5, 31.0, 90),
    # 6.0 V servo buck-boost (LM5175). Power row at y 48, from the input on the right to the output on the left:
    # Cin C17/C18 | Q2 high-side buck | Q3 low-side buck | R21 sense | Q4 low-side boost | Q5 high-side boost | Cout.
    # L2 spans SW1 (pad 2, right) and SW2 (pad 1, left) above the row. U3 at 270 deg: gate drivers face down.
    "C17": P(31.6, 48.0, 90), "C18": P(34.0, 48.2, 90),
    "Q2": P(27.0, 48.0, 0), "Q3": P(22.6, 48.0, 0), "R21": P(18.5, 48.0, 90), "Q4": P(14.4, 48.0, 180),
    "Q5": P(10.1, 48.0, 180),
    "C20": P(5.5, 39.6, 0), "C21": P(5.5, 42.2, 0), "C22": P(5.5, 44.8, 0), "C35": P(5.5, 47.4, 0),
    "L2": P(18.5, 40.4, 0), "U3": P(21.0, 32.4, 270),
    # LM5175 (HTSSOP-28 at 270 deg): EN/VIN/analog pins on the top edge, gate drivers on the bottom edge. FB divider
    # in the strip between C12 and U3, the rest of the network in rows above, bootstrap/VCC parts beside L2.
    "R10": P(17.05, 28.1, 0), "R11": P(19.05, 28.1, 0),
    "C31": P(20.75, 26.95, 90), "R22": P(21.95, 26.95, 90), "R25": P(23.15, 26.95, 90), "C19": P(24.35, 26.95, 90),
    "R24": P(25.55, 26.95, 90), "R12": P(26.75, 26.95, 270),
    "R23": P(20.35, 24.8, 90), "C32": P(21.45, 24.8, 90), "C34": P(22.65, 24.8, 270), "C33": P(23.85, 24.8, 270),
    "C30": P(26.35, 37.9, 90), "D5": P(23.8, 38.6, 270), "C23": P(24.7, 40.9, 0),
    "D6": P(14.4, 34.4, 90), "C24": P(13.3, 38.5, 90),
    # battery input, protection, shunt, monitor
    "Q1": P(45.0, 44.5, 0), "R2": P(37.5, 43.6, 180), "D2": P(43.0, 49.5, 0), "D1": P(54.0, 12.0, 0),
    "R1": P(54.0, 16.0, 0), "C3": P(35.0, 36.5, 180),
    "U1": P(45.5, 36.5, 180), "C2": P(45.5, 33.2, 0), "R3": P(43.6, 40.3, 90), "R4": P(45.2, 40.3, 90), "C1": P(46.8, 40.3, 90),
    # failsafe arbiter and its LED
    "U4": P(38.5, 13.0, 0), "C25": P(34.0, 12.3, 90), "R13": P(38.5, 17.8, 0),
    "R15": P(44.0, 13.0, 90), "R16": P(35.0, 17.8, 0), "D4": P(35.0, 19.8, 0),
    # ESC PWM isolation (U6 side 1 on the left = HAT, side 2 on the right = ESC) and the ESC-side LDO
    "U6": P(47.5, 26.0, 0), "C36": P(45.2, 22.2, 0), "C37": P(49.8, 22.2, 0), "U7": P(54.0, 24.5, 0),
    "C38": P(54.5, 28.0, 0), "C39": P(54.5, 20.9, 0), "R14": P(50.0, 30.3, 0), "R26": P(53.2, 30.3, 0),
    # Owned GY-521 edge header; ESD clamps at the harness entry. Pull-ups already on Pi / GY-521.
    "J9": P(39.0, 66.3, 0), "D7": P(38.0, 61.0, 90), "D8": P(40.0, 61.0, 90),
    "R17": P(58.6, 9.6, 90),

}


def gpio_pad(pin: int) -> tuple[float, float]:
    """Board position of header pin ``pin`` (odd row inboard, even row at the edge)."""
    column = (pin - 1) // 2
    return (round(GPIO_PIN1[0] + 2.54 * column, 3), round(GPIO_PIN1[1] - (2.54 if pin % 2 == 0 else 0.0), 3))


def r(ref: str, value: str, a: str, b: str, sch: tuple[float, float, int], pcb: Placement | None, mpn: str,
      description: str, footprint: str = R0603) -> Part:
    return Part(ref, "Device:R", value, footprint, {"1": a, "2": b}, sch=sch, pcb=pcb, mpn=mpn, manufacturer=YAGEO,
                description=description)


def c(ref: str, value: str, a: str, b: str, sch: tuple[float, float, int], pcb: Placement | None, mpn: str,
      description: str, footprint: str = C0603, manufacturer: str = MURATA) -> Part:
    return Part(ref, "Device:C", value, footprint, {"1": a, "2": b}, sch=sch, pcb=pcb, mpn=mpn,
                manufacturer=manufacturer, description=description)


def build() -> BoardIntent:
    pi = pi_rail()
    servo = servo_rail()
    parts: list[Part] = []
    add = parts.append
    P = Placement

    # ------------------------------------------------------------------ connectors (sheet column x 30-60)
    gpio = {str(i): GPIO_NETS[str(i)] for i in range(1, 41) if str(i) in GPIO_NETS}
    add(Part("J1", "Connector_Generic:Conn_02x20_Odd_Even", "RPi_GPIO", "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical",
             gpio, no_connect=tuple(str(i) for i in range(1, 41) if str(i) not in gpio), sch=(45.72, 101.6, 0),
             pcb=P(*GPIO_PIN1, 0, "B"), mpn="PPPC202LFBN-RC", manufacturer="Sullins",
             description="2x20 2.54 mm female socket on the bottom side, mates Raspberry Pi J8", connector="J_GPIO"))
    add(Part("J2", "Connector_Generic:Conn_01x02", "XT30PW-M", "Connector_AMASS:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal",
             {"1": "VBAT", "2": "GND"}, sch=(25.4, 190.5, 180), pcb=P(60.0, 46.0, 90), mpn="XT30PW-M", manufacturer="AMASS",
             description="XT30 right-angle male PCB connector, battery input", connector="J_BATT"))
    add(Part("J3", "Connector_Generic:Conn_01x03", "ESC", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Horizontal",
             {"1": "ESC_PWM", "2": "ESC_BEC", "3": "ESC_GND"}, sch=(386.08, 165.1, 0), pcb=P(9.0, 51.5, 90),
             mpn="PH1-03-RA", manufacturer="Adam Tech",
             description="Side-entry ESC signal header, isolated: ESC_PWM and ESC_GND are the ESC's side of U6; pin 2 (ESC BEC) "
                         "powers only U6's ESC side through U7", connector="J_ESC"))
    add(Part("J4", "Connector_Generic:Conn_01x03", "SERVO", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Horizontal",
             {"1": "SERVO_PWM", "2": "SERVO_6V", "3": "GND"}, sch=(386.08, 190.5, 0), pcb=P(17.0, 51.5, 90),
             mpn="PH1-03-RA", manufacturer="Adam Tech", description="Side-entry steering servo header, 6.0 V from U3", connector="J_SERVO"))
    add(Part("J5", "Connector_Generic_MountingPin:Conn_01x04_MountingPin", "SM04B-GHS-TB",
             "Connector_JST:JST_GH_SM04B-GHS-TB_1x04-1MP_P1.25mm_Horizontal",
             {"1": "5V", "2": "CRSF_TX", "3": "CRSF_RX", "4": "GND", "MP": "GND"}, sch=(386.08, 76.2, 0), pcb=P(61.0, 18.25, 270),
             mpn="SM04B-GHS-TB(LF)(SN)", manufacturer="JST", description="JST GH 4-pin side entry, ELRS receiver", connector="J_RX"))
    add(Part("J6", "Connector_Generic_MountingPin:Conn_01x04_MountingPin", "SM04B-GHS-TB",
             "Connector_JST:JST_GH_SM04B-GHS-TB_1x04-1MP_P1.25mm_Horizontal",
             {"1": "5V", "2": "GPS_TX", "3": "GPS_RX", "4": "GND", "MP": "GND"}, sch=(386.08, 106.68, 0), pcb=P(4.0, 23.25, 90),
             mpn="SM04B-GHS-TB(LF)(SN)", manufacturer="JST", description="JST GH 4-pin side entry, GNSS module", connector="J_GPS"))
    add(Part("J7", "Connector_Generic:Conn_01x03", "S3B-XH-A", "Connector_JST:JST_XH_S3B-XH-A_1x03_P2.50mm_Horizontal",
             {"1": "3V3", "2": "ENC", "3": "GND"}, sch=(386.08, 137.16, 0), pcb=P(3.0, 36.25, 90),
             mpn="S3B-XH-A(LF)(SN)", manufacturer="JST", description="JST XH 3-pin side entry, wheel encoder", connector="J_ENC"))
    add(Part("J8", "Connector_Generic:Conn_01x03", "UPDI", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
             {"1": "3V3", "2": "UPDI", "3": "GND"}, sch=(330.2, 241.3, 0), pcb=P(34.0, 52.0, 90),
             mpn="PH1-03-UA", manufacturer="Adam Tech", description="UPDI programming header for U4"))

    # ------------------------------------------------------------------ battery input, reverse protection, monitor
    add(Part("Q1", "Transistor_FET:DMP3013SFV", "DMP3013SFV", "Package_SON:Diodes_PowerDI3333-8",
             {"1": "VBAT_PROT", "2": "VBAT_PROT", "3": "VBAT_PROT", "4": "Q1_GATE", "5": "VBAT"}, sch=(50.8, 190.5, 90), pcb=P(52.5, 46.0, 0), mpn="DMP3013SFV-7", manufacturer="Diodes Inc.",
             description="P-MOSFET reverse-polarity protection, -30 V, 9.5 mOhm @ -10 V"))
    add(Part("D1", "Diode:BZT52Bxx", "BZT52B12", "Diode_SMD:D_SOD-123F", {"1": "VBAT_PROT", "2": "Q1_GATE"},
             sch=(66.04, 205.74, 270), pcb=P(52.5, 41.5, 180), mpn="BZT52B12-7-F", manufacturer="Diodes Inc.",
             description="12 V zener, Q1 gate-source clamp"))
    add(r("R1", "100k", "Q1_GATE", "GND", (50.8, 218.44, 0), P(48.5, 41.5, 90), "RC0603FR-07100KL", "Q1 gate pull-down"))
    # TVS: SMAJ10A. A 2S pack is 8.4 V full (below VRWM 10 V); the clamp, 17.0 V at I_PP 23.5 A (16.3 V at the
    # 20 A expected surge, 2 x the HAT's 10 A fuse), stays below the TPS56528's 20 V VIN absolute maximum.
    # The SMAJ16A it replaces clamped at 26 V. 2S only: a 3S pack (12.6 V) exceeds the stand-off.
    add(Part("D2", "Diode:SMAJ10A", "SMAJ10A", "Diode_SMD:D_SMA", {"1": "VBAT_PROT", "2": "GND"}, sch=(78.74, 203.2, 90),
             pcb=P(45.0, 51.0, 0), mpn="SMAJ10A", manufacturer="Littelfuse",
             description="400 W TVS, VRWM 10 V, VBR 11.1 V min, VC 17.0 V at 23.5 A"))
    add(Part("R2", "Device:R_Shunt", "2m", "Resistor_SMD:R_Shunt_Vishay_WSK2512_6332Metric_T1.19mm",
             {"1": "VBAT_PROT", "2": "SENSE_P", "3": "SENSE_N", "4": "VIN_HAT"}, sch=(99.06, 190.5, 90), pcb=P(44.5, 45.5, 0),
             mpn="WSK25122L000FEA", manufacturer="Vishay Dale", description="2 mOhm 1 % 1 W four-terminal current shunt"))
    add(r("R3", "10", "SENSE_P", "INA_INP", (106.68, 172.72, 90), P(43.0, 41.0, 90), "RC0603FR-0710RL", "INA226 input filter"))
    add(r("R4", "10", "SENSE_N", "INA_INN", (106.68, 180.34, 90), P(41.5, 41.0, 90), "RC0603FR-0710RL", "INA226 input filter"))
    add(c("C1", "100n", "INA_INP", "INA_INN", (119.38, 167.64, 0), P(42.25, 38.5, 0), "GRM188R71H104KA93D",
          "INA226 differential input filter (SBOS547 sec. 8.4.1)"))
    add(Part("U1", "Sensor_Energy:INA226", "INA226", "Package_SO:TSSOP-10_3x3mm_P0.5mm",
             {"1": "GND", "2": "GND", "4": "I2C_SDA", "5": "I2C_SCL", "6": "3V3", "7": "GND", "8": "VIN_HAT", "9": "INA_INN",
              "10": "INA_INP"}, no_connect=("3",), sch=(149.86, 177.8, 0), pcb=P(42.0, 34.5, 0),
             mpn="INA226AIDGSR", manufacturer="Texas Instruments", description="HAT input current/voltage monitor, I2C 0x40"))
    add(c("C2", "100n", "3V3", "GND", (172.72, 160.02, 0), P(45.5, 34.5, 90), "GRM188R71H104KA93D", "INA226 VS decoupling"))
    add(Part("C3", "Device:C_Polarized", "220u 25V", "Capacitor_SMD:CP_Elec_8x10", {"1": "VIN_HAT", "2": "GND"},
             sch=(129.54, 205.74, 0), pcb=P(32.0, 47.5, 0), mpn="EEH-ZA1E221P", manufacturer="Panasonic",
             description="Input bulk / hold-up, hybrid polymer 220 uF 25 V, damps the harness LC"))

    # ------------------------------------------------------------------ U2: 5.1 V / 5 A for the Pi (TPS568230)
    add(Part("U2", "Regulator_Switching:TPS568230", "TPS568230",
             "Package_DFN_QFN:Texas_RJE0020A_VQFN-20-1EP_3x3mm_P0.45mm_EP0.675x0.76mm",
             {"1": "BST_5V", "2": "VIN_HAT", "3": "VIN_HAT", "4": "VIN_HAT", "5": "VIN_HAT", "6": "SW_5V", "19": "SW_5V",
              "20": "SW_5V", "7": "GND", "8": "GND", "18": "GND", "21": "GND", "11": "SS_5V", "12": "EN_5V", "13": "GND",
              "14": "FB_5V", "15": "MODE_5V", "17": "VCC_5V"}, no_connect=("9", "10", "16"),
             sch=(203.2, 63.5, 0), pcb=P(22.0, 16.0, 0), mpn=pi.ic.part, manufacturer="Texas Instruments",
             description="4.5-18 V in, 8 A sync buck; 5.1 V / 5 A Pi rail, UVLO 4.4 V max"))
    add(Part("L1", "Device:L", "2.2u", pi.inductor.footprint, {"1": "SW_5V", "2": "5V"}, sch=(241.3, 50.8, 90),
             pcb=P(14.0, 18.0, 0), mpn=pi.inductor.part, manufacturer="Coilcraft",
             description="2.2 uH, 13.7 mOhm, Isat 18 A molded inductor"))
    for i, x in enumerate((26.0, 28.5, 31.0)):
        add(c(f"C{4 + i}", "22u 25V", "VIN_HAT", "GND", (175.26 + 10.16 * i, 101.6, 0), P(x, 17.5, 90), pi.c_in.part,
              "U2 input capacitor 22 uF 25 V X5R", C1206))
    add(c("C7", "100n", "VIN_HAT", "GND", (205.74, 101.6, 0), P(24.5, 18.0, 90), "GRM188R71H104KA93D", "U2 VIN HF decoupling"))
    for i, x in enumerate((7.0, 9.5, 12.0, 14.5)):
        add(c(f"C{8 + i}", "22u 16V", "5V", "GND", (264.16 + 10.16 * i, 63.5, 0), P(x, 10.5, 90), pi.c_out.part,
              "U2 output capacitor 22 uF 16 V X5R", C1206))
    add(Part("C12", "Device:C_Polarized", "220u 25V", "Capacitor_SMD:CP_Elec_8x10", {"1": "5V", "2": "GND"},
             sch=(304.8, 63.5, 0), pcb=P(9.0, 27.0, 0), mpn="EEH-ZA1E221P", manufacturer="Panasonic",
             description="5 V hold-up, hybrid polymer 220 uF (TI Cout max 500 uF)"))
    add(c("C13", "100n", "BST_5V", "SW_5V", (226.06, 40.64, 90), P(19.0, 13.0, 0), "GRM188R71H104KA93D", "U2 bootstrap"))
    add(c("C14", "1u", "VCC_5V", "GND", (175.26, 30.48, 0), P(25.0, 13.0, 0), "GRM188R61E105KA12D", "U2 VCC LDO bypass"))
    add(r("R5", "226k", "5V", "FB_5V", (241.3, 76.2, 0), P(19.0, 21.5, 90), "RC0603FR-07226KL", "U2 feedback upper"))
    add(c("C15", "100p", "5V", "FB_5V", (251.46, 76.2, 0), P(17.5, 21.5, 90), "GRM1885C1H101JA01D",
          "U2 feed-forward (Table 3: 47-330 pF)"))
    add(r("R6", "30.1k", "FB_5V", "GND", (241.3, 93.98, 0), P(20.5, 21.5, 90), "RC0603FR-0730K1L", "U2 feedback lower"))
    add(r("R7", "110k", "VCC_5V", "MODE_5V", (165.1, 45.72, 0), P(26.5, 13.0, 90), "RC0603FR-07110KL", "U2 MODE upper: FCCM 600 kHz"))
    add(r("R8", "51k", "MODE_5V", "GND", (165.1, 63.5, 0), P(28.0, 13.0, 90), "RC0603FR-0751KL", "U2 MODE lower: FCCM 600 kHz"))
    add(r("R9", "100k", "VIN_HAT", "EN_5V", (154.94, 76.2, 0), P(29.5, 20.5, 90), "RC0603FR-07100KL", "U2 EN pull-up"))
    add(Part("D3", "Diode:BZT52Bxx", "BZT52B3V3", "Diode_SMD:D_SOD-123F", {"1": "EN_5V", "2": "GND"}, sch=(165.1, 86.36, 90),
             pcb=P(27.0, 21.5, 90), mpn="BZT52B3V3-7-F", manufacturer="Diodes Inc.",
             description="Clamps EN to 3.3 V (EN abs. max 4 V); the 4.4 V VIN UVLO controls start-up"))
    add(c("C16", "22n", "SS_5V", "GND", (185.42, 86.36, 0), P(23.0, 21.5, 90), "GRM188R71H223KA01D",
          "U2 soft start: Tss = Css * 0.6 V / 5 uA = 2.6 ms"))

    # ------------------------------------------------------------------ U3: 6.0 V servo rail (LM5175 4-switch buck-boost)
    # VBAT 4.2-17 V -> 6.0 V / 3 A continuous, 4 A peak (r3_kicad.regulators.servo_rail; LM5175 SNVSA37A sec. 9.2.2).
    # No dropout: the servo keeps 6.0 V from a full pack down to the 4.18 V UVLO (the TPS56528 buck it replaces was
    # in dropout below ~7.8 V input). Values from the design procedure, equation numbers in the comments.
    lm = servo
    fet = "Transistor_FET:CSD17577Q3A"
    add(Part("U3", "Regulator_Switching:LM5175PWP", "LM5175", "Package_SO:HTSSOP-28-1EP_4.4x9.7mm_P0.65mm_EP3.4x9.5mm",
             {"1": "EN_6V", "2": "VIN_HAT", "3": "VIN_HAT", "4": "MODE_6V", "5": "GND", "6": "RT_6V", "7": "SLOPE_6V",
              "8": "SS_6V", "9": "COMP_6V", "10": "GND", "11": "FB_6V", "12": "SERVO_6V", "13": "SERVO_6V",
              "14": "SERVO_6V", "15": "GND", "16": "CS_6V", "18": "SW2_6V", "19": "HD2_6V", "20": "BT2_6V",
              "21": "LD2_6V", "22": "GND", "23": "VCC_6V", "24": "GND", "25": "LD1_6V", "26": "BT1_6V", "27": "HD1_6V",
              "28": "SW1_6V", "29": "GND"}, no_connect=("17",), sch=(203.2, 165.1, 0), pcb=P(21.0, 32.4, 270),
             mpn="LM5175PWPR", manufacturer="Texas Instruments",
             description="3.5-42 V synchronous 4-switch buck-boost controller; 6.0 V servo rail, no dropout"))
    for ref, d, sn, g, text in (("Q2", "VIN_HAT", "SW1_6V", "HD1_6V", "buck high side"),
                                ("Q3", "SW1_6V", "CS_6V", "LD1_6V", "buck low side"),
                                ("Q4", "SW2_6V", "CS_6V", "LD2_6V", "boost low side"),
                                ("Q5", "SERVO_6V", "SW2_6V", "HD2_6V", "boost high side")):
        add(Part(ref, fet, "CSD17577Q3A", lm.fet.footprint, {"1": sn, "2": sn, "3": sn, "4": g, "5": d},
                 sch=(241.3, 140.0, 0), pcb=None, mpn=lm.fet.part, manufacturer=lm.fet.manufacturer,
                 description=f"30 V 4 mOhm NexFET, {text} of the servo buck-boost"))
    add(Part("L2", "Device:L", "3.3u", lm.inductor.footprint, {"1": "SW2_6V", "2": "SW1_6V"}, sch=(241.3, 162.56, 90),
             pcb=None, mpn=lm.inductor.part, manufacturer="Coilcraft",
             description="3.3 uH, 19.5 mOhm, Isat 12.3 A molded inductor (Eq. 13/14: 3.0 / 1.0 uH targets)"))
    add(Part("R21", "Device:R", "12m", "Resistor_SMD:R_2010_5025Metric", {"1": "GND", "2": "CS_6V"}, sch=(241.3, 190.0, 0),
             pcb=None, mpn="WSL2010R0120FEA", manufacturer="Vishay Dale",
             description="Current sense 12 mOhm 1 % 0.5 W: 4.4-8.2 A buck valley / 9.9-18 A boost peak limit (Eq. 22-24)"))
    add(Part("D5", "Diode:BAT54J", "BAT54J", "Diode_SMD:D_SOD-323F", {"1": "BT1_6V", "2": "VCC_6V"}, sch=(226.0, 130.0, 0),
             pcb=None, mpn="BAT54J,115", manufacturer="Nexperia", description="BOOT1 bootstrap Schottky from VCC (sec. 8.3.13)"))
    add(Part("D6", "Diode:BAT54J", "BAT54J", "Diode_SMD:D_SOD-323F", {"1": "BT2_6V", "2": "VCC_6V"}, sch=(226.0, 140.0, 0),
             pcb=None, mpn="BAT54J,115", manufacturer="Nexperia", description="BOOT2 bootstrap Schottky from VCC (sec. 8.3.13)"))
    add(c("C23", "100n", "BT1_6V", "SW1_6V", (226.06, 142.24, 90), None, "GRM155R71C104KA88D", "U3 BOOT1 capacitor", C0402))
    add(c("C24", "100n", "BT2_6V", "SW2_6V", (226.06, 150.0, 90), None, "GRM155R71C104KA88D", "U3 BOOT2 capacitor", C0402))
    add(c("C30", "4.7u 16V", "VCC_6V", "GND", (175.26, 147.32, 0), None, "GRM21BR61C475KA88L",
          "U3 VCC (7.35 V gate-drive regulator) bypass", "Capacitor_SMD:C_0805_2012Metric"))
    for i, (ref, x) in enumerate((("C17", 28.5), ("C18", 31.0))):
        add(c(ref, "22u 25V", "GND", "VIN_HAT", (175.26 + 10.16 * i, 200.66, 0), None, lm.c_in.part,
              "U3 power-stage input capacitor 22 uF 25 V X5R", C1206))
    add(c("C19", "100n", "VIN_HAT", "GND", (195.58, 200.66, 0), None, "GRM155R71H104KE14D", "U3 VIN/VISNS decoupling",
          C0402))
    for i, ref in enumerate(("C20", "C21", "C22", "C35")):
        add(c(ref, "22u 16V", "GND", "SERVO_6V", (264.16 + 10.16 * i, 175.26, 0), None, lm.c_out.part,
              "Servo rail output capacitor 22 uF 16 V X5R", C1206))
    add(r("R10", "130k", "SERVO_6V", "FB_6V", (241.3, 185.42, 0), None, "RC0402FR-07130KL", "U3 feedback upper (Eq. 12)", R0402))
    add(r("R11", "20k", "FB_6V", "GND", (241.3, 203.2, 0), None, "RC0402FR-0720KL", "U3 feedback lower (Eq. 11)", R0402))
    add(r("R12", "100k", "VIN_HAT", "EN_6V", (165.1, 185.42, 0), None, "RC0402FR-07100KL",
          "U3 EN/UVLO upper: 0.35 V hysteresis (Eq. 2)", R0402))
    add(r("R24", "35.7k", "EN_6V", "GND", (165.1, 195.0, 0), None, "RC0402FR-0735K7L",
          f"U3 EN/UVLO lower: on {lm.values['uvlo_rising']:.2f} V, off {lm.values['uvlo_falling']:.2f} V (Eq. 1)", R0402))
    add(r("R22", "61.9k", "RT_6V", "GND", (165.1, 205.0, 0), None, "RC0402FR-0761K9L", "U3 RT: 400 kHz (Eq. 5)", R0402))
    add(r("R25", "93.1k", "MODE_6V", "GND", (165.1, 215.0, 0), None, "RC0402FR-0793K1L", "U3 MODE: CCM, hiccup current limit", R0402))
    add(c("C31", "68n", "SS_6V", "GND", (185.42, 186.0, 0), None, "GRM155R71C683KA88D", "U3 soft start 10.9 ms (Eq. 3)", C0402))
    add(c("C32", "47p", "SLOPE_6V", "GND", (185.42, 196.0, 0), None, "GRM1555C1H470JA01D",
          "U3 slope compensation (dead-beat 110 pF, Eq. 25)", C0402))
    add(r("R23", "953", "COMP_6V", "COMP_R_6V", (195.58, 186.0, 0), None, "RC0402FR-07953RL", "U3 compensation Rc1 (Eq. 43)", R0402))
    add(c("C33", "82n", "COMP_R_6V", "GND", (195.58, 196.0, 0), None, "GRM155R71C823KA88D", "U3 compensation Cc1 (Eq. 44)", C0402))
    add(c("C34", "820p", "COMP_6V", "GND", (195.58, 206.0, 0), None, "GRM1555C1H821JA01D", "U3 compensation Cc2 (HF pole)", C0402))

    # ------------------------------------------------------------------ U4: failsafe arbiter (ATtiny412)
    add(Part("U4", "MCU_Microchip_ATtiny:ATtiny412-SS", "ATtiny412", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
             {"1": "3V3", "2": "PI_ESC_PWM", "3": "HEARTBEAT", "4": "FS_LED", "6": "UPDI", "7": "ESC_OUT", "8": "GND"},
             no_connect=("5",), sch=(304.8, 215.9, 0), pcb=P(37.0, 25.0, 0), mpn="ATTINY412-SSN", manufacturer="Microchip",
             description="Failsafe arbiter: passes ESC PWM only while GPIO27 heartbeat toggles, else active brake then "
                         "1500 us neutral (r3_kicad.arbiter)"))
    add(c("C25", "100n", "3V3", "GND", (281.94, 203.2, 0), P(33.5, 25.0, 90), "GRM188R71H104KA93D", "U4 decoupling"))
    add(r("R13", "100k", "PI_ESC_PWM", "GND", (281.94, 228.6, 0), P(37.0, 20.5, 90), "RC0603FR-07100KL",
          "Holds the Pi PWM input low while the Pi boots"))
    add(r("R15", "100k", "ESC_OUT", "GND", (358.14, 228.6, 0), P(43.0, 28.0, 90), "RC0603FR-07100KL",
          "Holds the isolator input low while U4 is unpowered or in reset"))
    add(r("R16", "1k", "FS_LED", "LED_A", (330.2, 190.5, 90), P(40.5, 22.5, 0), "RC0603FR-071KL", "Failsafe LED resistor"))
    add(Part("D4", "Device:LED", "red", "LED_SMD:LED_0603_1608Metric", {"1": "GND", "2": "LED_A"}, sch=(345.44, 190.5, 180),
             pcb=P(43.5, 22.5, 0), mpn="150060RS75000", manufacturer="Wurth Elektronik", description="Failsafe active indicator"))

    # ------------------------------------------------------------------ U6: ESC PWM digital isolator (ground-loop break)
    # The ESC's signal ground is tied to the motor return inside the ESC; on the HAT it would parallel the power
    # harness ground (3.4 A of motor current in the 22 AWG signal lead at stall). U6 re-drives the arbiter output on
    # the ESC's side, referenced to the ESC's own ground (ESC_GND, not HAT GND) and powered from the ESC's BEC
    # through U7. Fail-safe low (F): with the Pi (3V3) unpowered the ESC sees no pulses.
    add(Part("U6", "Isolator:ISO6720FBD", "ISO6720F", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
             {"1": "3V3", "2": "ESC_OUT", "3": "GND", "4": "GND", "5": "ESC_GND", "7": "ESC_ISO", "8": "ESC_3V3"},
             no_connect=("6",), sch=(330.2, 241.3, 0), pcb=None, mpn="ISO6720FBDR", manufacturer="Texas Instruments",
             description="Dual digital isolator, default-low outputs, 1.71-5.5 V per side; ESC PWM channel A"))
    add(Part("U7", "Regulator_Linear:AP7381-33SA-7", "AP7381-33", "Package_TO_SOT_SMD:SOT-23",
             {"1": "ESC_BEC", "2": "ESC_3V3", "3": "ESC_GND"}, sch=(360.0, 241.3, 0), pcb=None, mpn="AP7381-33SA-7",
             manufacturer="Diodes Inc.", description="3.3 V LDO, 40 V input, from the ESC BEC to U6's ESC side"))
    add(c("C36", "100n", "3V3", "GND", (320.0, 255.0, 0), None, "GRM188R71H104KA93D", "U6 VCC1 decoupling"))
    add(c("C37", "100n", "ESC_3V3", "ESC_GND", (340.0, 255.0, 0), None, "GRM188R71H104KA93D", "U6 VCC2 decoupling"))
    add(c("C38", "1u 25V", "ESC_BEC", "ESC_GND", (350.0, 255.0, 0), None, "GRM188R61E105KA12D", "U7 input capacitor"))
    add(c("C39", "1u", "ESC_3V3", "ESC_GND", (360.0, 255.0, 0), None, "GRM188R61E105KA12D", "U7 output capacitor"))
    add(r("R14", "220", "ESC_ISO", "ESC_PWM", (345.44, 215.9, 90), None, "RC0603FR-07220RL", "ESC PWM series resistor (ESC side)"))
    add(r("R26", "100k", "ESC_PWM", "ESC_GND", (370.0, 255.0, 0), None, "RC0603FR-07100KL",
          "Holds ESC_PWM low while U6's ESC side is unpowered"))

    # ------------------------------------------------------------------ servo PWM, IMU, I2C, encoder
    add(r("R17", "220", "PI_SERVO_PWM", "SERVO_PWM", (345.44, 165.1, 90), P(51.5, 9.0, 90), "RC0603FR-07220RL",
          "Servo PWM series resistor"))
    add(Part("J9", "Connector_Generic_MountingPin:Conn_01x04_MountingPin", "SM04B-GHS-TB",
             "Connector_JST:JST_GH_SM04B-GHS-TB_1x04-1MP_P1.25mm_Horizontal",
             {"1": "3V3", "2": "GND", "3": "I2C_SDA", "4": "I2C_SCL", "MP": "GND"},
             mpn="SM04B-GHS-TB(LF)(SN)", manufacturer="JST", connector="J_IMU",
             description="Keyed 3.3 V / GND / SDA / SCL header for the owned GY-521 (AD0 low, 0x68)"))
    for ref, net in (("D7", "I2C_SDA"), ("D8", "I2C_SCL")):
        add(Part(ref, "Diode:PESD3V3U1UL", "PESD3V3U1UL", "Diode_SMD:D_SOD-882",
                 {"1": net, "2": "GND"}, mpn="PESD3V3U1UL", manufacturer="Nexperia",
                 description="2.6 pF unidirectional ESD clamp: pin 1 cathode to I2C, pin 2 anode to ground"))
    # No HAT pull-ups: Pi 1.8k in parallel with typical GY-521 4.7k gives 1.30k, ~2.23 mA at VOL=0.4 V.
    add(r("R20", "10k", "3V3", "ENC", (355.6, 129.54, 0), P(8.5, 33.0, 90), "RC0603FR-0710KL", "Encoder open-drain pull-up"))

    # ------------------------------------------------------------------ mechanical + ERC power flags
    for i, (x, y) in enumerate(HOLES):
        add(Part(f"H{i + 1}", "Mechanical:MountingHole", "M2.5", "MountingHole:MountingHole_2.7mm_M2.5",
                 sch=(20.32 + 12.7 * i, 20.32, 0), pcb=P(x, y), in_bom=False, description="HAT M2.5 mounting hole"))
    for i, net in enumerate(("GND", "VIN_HAT", "3V3", "VBAT", "VCC_6V", "ESC_GND", "ESC_BEC")):
        add(Part(f"#FLG0{i + 1}", "power:PWR_FLAG", "PWR_FLAG", pins={"1": net}, sch=(20.32 + 15.24 * i, 271.78, 0),
                 in_bom=False, description="ERC: net is driven by an off-sheet source"))

    # ------------------------------------------------------------------ board rules and power copper
    dt_power = 20.0   # C rise allowed on power copper (IPC-2152, GND pour on the far side lowers it further)
    w5 = round(ipc2152.required_width(5.0, 1.0, dt_power) + 0.05, 2)
    w_in = round(ipc2152.required_width(5.0, 1.0, dt_power) + 0.05, 2)
    w_servo = round(ipc2152.required_width(3.0, 1.0, dt_power) + 0.05, 2)
    # Net-class widths are the defaults Freerouting uses for the remaining branch connections (pin
    # currents <= 0.3 A); the full rail currents flow in the hand-laid pours/tracks sized by IPC-2152,
    # whose widths are also the board's predefined track sizes.
    # Net-class widths are what Freerouting uses for the remaining low-current branch connections (they must fit
    # 0.45-0.5 mm pitch pins); the rail currents flow in the hand-routed copper sized by IPC-2152 (``copper``).
    classes = [
        NetClass("Default", 0.2, 0.2, 0.6, 0.3, ()),
        NetClass("Power5V", 0.3, 0.2, 0.8, 0.4, ("5V",), 5.0),
        # Rail currents flow in the hand-routed copper and pours; the autorouted branches of VIN_HAT and SERVO_6V reach
        # the LM5175's 0.5 mm-pitch QFN sense/supply pins (mA), so they use 0.2 mm at the 0.15 mm fab clearance.
        NetClass("PowerIn", 0.3, 0.2, 0.8, 0.4, ("VBAT", "VBAT_PROT"), 5.0),
        NetClass("PowerInBranch", 0.2, 0.15, 0.6, 0.3, ("VIN_HAT",), 5.0),
        NetClass("Servo", 0.2, 0.15, 0.6, 0.3, ("SERVO_6V",), 3.0),
        NetClass("SwitchNode", 0.3, 0.2, 0.8, 0.4, ("SW_5V",), 5.0),
        # The NexFETs' gate pad sits 0.15 mm from their source pads (VSON-8, 0.65 mm pitch): the servo power-stage nets
        # use the fab minimum clearance; their pours keep their own 0.2 mm zone clearance.
        NetClass("ServoPower", 0.2, 0.15, 0.6, 0.3, ("SW1_6V", "SW2_6V", "CS_6V"), 5.0),
        # LM5175 gate drive (2.2 A peak for ~10 ns, 0.25 mm fits the 0.5 mm QFN pitch) and bootstrap
        NetClass("GateDrive", 0.2, 0.15, 0.6, 0.3, ("HD1_6V", "LD1_6V", "HD2_6V", "LD2_6V", "BT1_6V", "BT2_6V", "VCC_6V"), 0.5),
        NetClass("Ground", 0.3, 0.15, 0.6, 0.3, ("GND",), 5.0),
        NetClass("Analog", 0.2, 0.2, 0.6, 0.3, ("SENSE_P", "SENSE_N", "INA_INP", "INA_INN", "FB_5V"), 0.0),
        NetClass("AnalogFine", 0.15, 0.15, 0.6, 0.3, ("FB_6V", "COMP_6V", "COMP_R_6V", "SLOPE_6V", "RT_6V", "SS_6V", "MODE_6V",
                                                       "EN_6V"), 0.0),
    ]
    # Minimum clearance 0.13 mm (JLCPCB/PCBWay 2-layer standard: 5 mil) only so the custom rule below can admit TI's
    # own CSD17577Q3A land pattern (0.14 mm between its gate and source pads); every net class keeps >= 0.15 mm.
    nexfet = [(f"{q} land pattern (TI VSON-8 gate-source pad gap 0.14 mm)",
               f"A.memberOfFootprint('{q}') && B.memberOfFootprint('{q}')", "(constraint clearance (min 0.13mm))")
              for q in ("Q2", "Q3", "Q4", "Q5")]
    rules = {"min_clearance": 0.13, "custom_rules": nexfet, "min_track_width": 0.15, "min_via_diameter": 0.5, "min_via_drill": 0.3,
             "min_annular_ring": 0.1, "copper_edge_clearance": 0.3, "hole_clearance": 0.25, "hole_to_hole": 0.5,
             "delta_t_power": dt_power, "track_widths": [0.2, 0.25, 0.3, 0.4, 0.8, 1.0, w_servo, w5]}

    # J1 sits where the HAT specification puts the 40-pin header: pin 1 inboard, pin 2 at the edge.
    j1 = Placement(*gpio_pad(1), 90.0, "B", anchors=(("1", gpio_pad(1)), ("2", gpio_pad(2)), ("3", gpio_pad(3))))
    placement = {**PLACEMENT, "J1": j1}
    parts[:] = [replace(part, pcb=placement.get(part.ref, part.pcb)) for part in parts]
    zones, tracks, vias = copper(w5, w_servo, pi.values["i_in_vin_min"], servo.values["i_in_vin_min"],
                                 round(servo.values["i_l_rms_cont"], 2))
    intent = BoardIntent(name="carrier-hat", title="R3 RC carrier HAT", component="hat", catalog_type="r3-carrier-hat",
                         width=WIDTH, height=HEIGHT, corner_radius=3.0, thickness=1.6, copper_oz=1.0, holes=HOLES,
                         parts=parts, net_classes=classes, rules=rules, zones=zones, power_tracks=tracks, vias=vias,
                         net_currents={"VBAT": 5.0, "VBAT_PROT": 5.0, "VIN_HAT": 5.0, "5V": 5.0, "SW_5V": 5.0,
                                       "SERVO_6V": 3.0, "SW1_6V": round(servo.values["i_l_rms_cont"], 2),
                                       "SW2_6V": round(servo.values["i_l_rms_cont"], 2),
                                       "CS_6V": round(servo.values["i_l_rms_cont"], 2), "GND": 5.0},
                         frame_origin=(32.5, 28.25), origin=(100.0, 44.0), sheet="A2")
    intent.notes += [
        "65 x 70 mm custom PCB: 13.5 mm south extension, original GPIO datum and all four HAT mounting holes unchanged.",
        "Owned GY-521 at J_IMU (J9): 3.3 V, GND, SDA, SCL; AD0 low, address 0x68. No HAT pull-ups: Pi 1.8k || "
        "typical GY-521 4.7k = 1.30k, sink current 2.23 mA at VOL=0.4 V, below the 3 mA I2C limit. "
        "Verify the owned board pull-ups before assembly. PESD3V3U1UL clamps on both external I2C signals.",
        "Camera connects directly to Pi CAM0 by 22-pin FPC, never through this HAT. CAM top z=90.8 mm; HAT "
        "underside z=100.6 mm: 9.8 mm free height, or 6.8 mm allowing 3 mm trimmed THT tails. Route the 0.3 mm "
        "FPC up 1 mm, bend toward -y at radius 3 mm, then under the extension at z=94.8 mm and beyond y=-43 mm. "
        "Cable top z=94.95 leaves 2.65 mm below the lowest allowed solder tail. Plug and latch FPC before fitting HAT.",
        f"IPC-2152 external fit, 1 oz, {dt_power:g} C rise: 5 A -> {w5} mm, 3 A -> {w_servo} mm; signals 0.2 mm.",
        "INA226 measures only the HAT branch (Pi + servo + HAT logic). The ESC/motor branch splits off in the XT60 "
        "Y-harness before the HAT, so motor current is not visible to the Pi.",
        "J_ESC is galvanically isolated (U6 ISO6720F): pin 3 is the ESC's own ground, not HAT GND, so no motor return "
        "current can flow in the ESC signal lead; pin 2 (the ESC's 6 V BEC) powers only U6's ESC side through U7. The "
        "servo is powered by U3, never by the ESC BEC.",
        "Custom DRC rules (carrier-hat.kicad_dru): only pads inside each CSD17577Q3A (Q2-Q5, TI's VSON-8 land pattern, "
        "0.14 mm gate-source gap) may sit 0.13 mm apart; every net class keeps >= 0.15 mm.",
        "Autorouted branches of VIN_HAT, SERVO_6V and the servo switch/sense nets are 0.2 mm: they reach the LM5175's "
        "0.5 mm-pitch QFN pins and carry mA; the rail currents flow in the hand-routed copper and pours sized by IPC-2152.",
    ]
    return intent


def rect(x0: float, y0: float, x1: float, y1: float) -> tuple[tuple[float, float], ...]:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def copper(w5: float, w_servo: float, i_in_5v: float, i_in_servo: float,
           i_l_servo: float = 4.55) -> tuple[list[Zone], list[PowerTrack], list[Via]]:
    """Power copper routed by hand before the autorouter, the way a person routes power first: wide locked
    tracks along each rail's current path (necked down only at fine-pitch pins), the 5 V path to the Pi pins
    doubled on the bottom layer, regulator grounds tied into their exposed pads. GND pours on both layers
    are added after routing. Freerouting connects everything else."""
    # Segment currents: the full HAT input on the spine; each regulator's input current at the 6.0 V
    # minimum battery (regulators.py: Pout / (eta Vin)) on its own branch.
    T, V = PowerTrack, Via
    tracks = [
        # battery -> P-FET drain -> source -> shunt -> TVS (FET drain -> shunt: the VBAT_PROT pour below)
        T("VBAT", w5, ("J2.1", (49.0, 47.0), (46.2, 44.5)), current=5.0),
        T("VBAT_PROT", 1.2, ((40.6, 44.6), (41.0, 48.9)), current=1.0),
        # VIN_HAT spine: shunt -> bulk C3 -> north to the 5 V buck input bus, west to the servo buck input bar
        T("VIN_HAT", w5, ((35.0, 42.4), (38.3, 38.6), (38.3, 31.0), (29.6, 31.0), (29.6, 18.6)), current=5.0),
        T("VIN_HAT", 2.2, ((20.7, 18.6), (29.6, 18.6)), current=round(i_in_5v, 2)),
        # servo buck-boost input: from the shunt end of the spine down to its input capacitors and Q2's drain
        T("VIN_HAT", 2.2, ((35.0, 42.4), (30.2, 42.4), (30.2, 45.9)), current=round(i_in_servo, 2)),
        # 5.1 V buck: switch node L1 -> U2 pins 19/20 (necked at the pins), pin 6 over the back, bootstrap stub
        T("SW_5V", 2.6, ((18.0, 13.9), (19.9, 13.9)), current=5.0),
        T("SW_5V", 0.6, ("C13.2", (20.5, 14.0)), current=0.5),
        T("SW_5V", 0.25, ("U2.6", (26.0, 13.9), (26.0, 14.6))),
        T("SW_5V", 0.5, ((26.0, 14.6), (20.0, 14.6)), layer="B.Cu", current=1.5),
        # U2 small-signal loops kept tight by hand: soft start, enable divider/clamp, feedback, mode, VCC, bootstrap
        T("SS_5V", 0.25, ("U2.11", (25.5, 10.9), "C16.1")),
        T("EN_5V", 0.25, ("U2.12", (24.45, 9.75), (29.6, 9.75), "R9.2", (32.0, 10.425), "D3.1")),
        T("FB_5V", 0.25, ("U2.14", (23.55, 10.0), (23.9, 9.4), "R5.2")),
        T("FB_5V", 0.25, ("R5.2", "C15.2", "R6.1")),
        T("MODE_5V", 0.25, ("U2.15", (23.1, 10.0), (22.3, 9.4), "R8.1")),
        T("MODE_5V", 0.25, ("R8.1", "R7.2")),
        T("VCC_5V", 0.25, ("U2.17", (21.6, 12.55), "C14.1")),
        T("BST_5V", 0.25, ("C13.1", (22.6, 15.4), "U2.1")),
        T("VIN_HAT", 0.3, ("R9.1", (30.0, 17.5))),
        T("VIN_HAT", 0.6, ((24.2, 16.4), "C7.1")),
        T("VIN_HAT", 0.3, ((39.5, 36.5), "U1.8")),
        T("5V", 0.5, ("C11.1", (7.0, 18.5), "J6.1")),
        # CRSF UART GPIO escapes to the receiver (J5, south edge): the Pi's TXD/RXD sit on the header's edge row, so the
        # pair is routed by hand along the two corridors that row leaves: TX on top between the board edge
        # and the row, RX on the bottom between the two rows; both drop past H2's keep-out before autorouting to J5.
        T("CRSF_TX", 0.2, ("J1.8", (15.99, 0.85), (55.36, 0.85), (55.36, 6.3), (57.1, 6.3), (57.1, 25.625))),
        T("CRSF_RX", 0.2, ("J1.10", (18.53, 3.5), (57.9, 3.5), (57.9, 24.375)), layer="B.Cu"),
        # U2 grounds into the exposed pad, then out to a stitching via
        T("GND", 0.2, ("U2.18", "U2.21")), T("GND", 0.2, ("U2.13", "U2.21")), T("GND", 0.2, ("U2.8", "U2.21")),
        T("GND", 0.2, ("U2.7", "U2.8")), T("GND", 0.3, ("U2.8", (26.6, 13.1))),
        # 5 V: L1 -> output capacitor column -> around H1 -> Pi pins 2 and 4, on both layers; hold-up C12
        T("5V", 2.6, ((10.3, 13.0), (13.1, 13.0)), current=5.0),
        T("5V", 2.6, ((10.3, 17.2), (10.3, 7.8)), current=5.0),
        # Between H1's screw-head/standoff contact circles (2.25 mm top, 2.88 mm bottom) and J1 pin 1 the 5 V
        # path is a parallel pair: 1.4 mm on top (the smaller top circle leaves room) and 0.8 mm on the bottom,
        # joined by the via at its foot. Equal lengths, so the current splits by width: 5 A x 1.4 / 2.2 =
        # 3.18 A on top, 1.82 A below. The 2.6 mm feed from the column stays clear of J1 pin 1. The bottom
        # layer stays open below the header so pins 1-10 can escape.
        T("5V", 2.6, ((10.3, 7.15), (7.9, 7.15)), current=5.0),
        T("5V", 1.4, ((7.9, 7.15), (6.55, 7.15), (6.55, 2.23), (7.6, 2.23)), current=3.18),
        T("5V", 1.6, ((7.6, 2.23), "J1.2", "J1.4"), current=3.18),
        T("5V", 0.8, ((6.86, 7.3), (6.86, 2.23), (7.6, 2.23)), layer="B.Cu", current=1.82),
        T("5V", 1.6, ((7.6, 2.23), "J1.2", "J1.4"), layer="B.Cu", current=1.82),
        T("5V", 1.2, ((10.3, 17.2), (10.9, 21.8)), current=1.0),
        # 6.0 V servo buck-boost (LM5175 HTSSOP-28): ground pins into the exposed pad, VOSNS/ISNS(-)/ISNS(+) on the
        # output (average-current loop disabled), VIN/VISNS tied, and the FB divider at the FB pin (Kelvin to AGND).
        T("GND", 0.25, ("U3.10", (19.375, 31.0))), T("GND", 0.25, ("U3.5", (22.625, 31.0))),
        T("GND", 0.25, ("U3.15", (16.775, 33.8))), T("GND", 0.25, ("U3.22", (21.325, 33.8))),
        T("GND", 0.25, ("U3.24", (22.625, 33.8))),
        T("SERVO_6V", 0.25, ("U3.12", "U3.13", "U3.14")), T("VIN_HAT", 0.25, ("U3.2", "U3.3")),
        T("SERVO_6V", 0.2, ("R10.1", "U3.14")),
        # VOSNS/ISNS/FB-divider top sense from the output pour (round the encoder connector, clear of the SW nodes)
        T("SERVO_6V", 0.3, ("R10.1", (11.5, 28.1), (11.5, 38.6), (10.2, 39.4)), current=0.01),
        T("FB_6V", 0.2, ("R10.2", "R11.1")), T("FB_6V", 0.2, ("R11.1", "U3.11")),
        T("GND", 0.2, ("R11.2", "U3.10")),
        # LM5175 network straight off its pins: SS, RT, MODE up into their parts; COMP and SLOPE up between the row's
        # pads to their parts in the second row; EN to the divider beside it.
        T("SS_6V", 0.15, ("U3.8", "C31.1")), T("RT_6V", 0.15, ("U3.6", "R22.1")), T("MODE_6V", 0.15, ("U3.4", "R25.1")),
        T("COMP_6V", 0.15, ("U3.9", (20.025, 28.75), (20.21, 28.55), (20.21, 25.6), "R23.1")),
        T("SLOPE_6V", 0.15, ("U3.7", (21.325, 28.75), (21.37, 28.65), (21.37, 25.6), "C32.1")),
        T("EN_6V", 0.15, ("U3.1", (25.225, 28.3), "R24.1")), T("EN_6V", 0.15, ("R24.1", "R12.2")),
        T("VIN_HAT", 0.25, ("R12.1", (28.6, 26.44))),
        # VIN/VISNS and C19 to the input spine through a short bottom-layer link (the EN pin sits between them)
        T("VIN_HAT", 0.25, ("U3.3", (24.25, 28.45))), T("VIN_HAT", 0.25, ("C19.1", (24.25, 28.45))),
        T("VIN_HAT", 0.4, ((24.25, 28.45), (24.25, 28.9), (28.9, 28.9)), layer="B.Cu", current=0.1),
        # CS to the sense pour under the inductor on the bottom layer (away from the SW2 pad), SW1 straight down
        T("CS_6V", 0.25, ("U3.16", (17.425, 36.1), (17.1, 36.45))),
        T("CS_6V", 0.3, ((17.1, 36.45), (18.6, 37.95), (18.6, 44.6)), layer="B.Cu"), T("CS_6V", 0.3, ((18.6, 44.6), "R21.2")),
        T("SW1_6V", 0.3, ("U3.28", (25.225, 41.4))),
        # Core tracks inside the pours: Specctra export does not hand zones to Freerouting, so without them it routes
        # other nets straight through the pours and splits them. The pour around each core carries the current (its
        # narrowest filled cross-section is the IPC-2152 check), so the cores declare none.
        T("SW1_6V", 2.0, ((20.86, 42.2), (23.4, 42.2), (23.4, 45.6))),
        T("SW1_6V", 1.0, ((25.3, 41.6), (25.3, 46.3))),
        T("SW2_6V", 2.7, ((16.14, 42.2), (13.55, 42.2), (13.55, 45.25))),
        # Protect the pour necks from signal routing: tie Q5 sources into Q4 drain and the sense stub to C20.
        T("SW2_6V", 1.0, ((11.54, 48.0), (13.55, 48.0))),
        T("SERVO_6V", 1.0, ((7.3, 39.4), (10.2, 39.4))),
        T("VBAT_PROT", 1.0, ("R2.1", "Q1.2")),
        T("CS_6V", 1.0, ((16.6, 48.0), (20.3, 48.0))),
        # Boost low-side gate: under L2 between its pads, below the SW2 pour, into Q4's gate pocket
        T("LD2_6V", 0.25, ("U3.21", (20.675, 36.5))),
        T("LD2_6V", 0.3, ((20.675, 36.5), (19.6, 37.6), (19.6, 45.2), (15.35, 45.4)), layer="B.Cu"),
        T("LD2_6V", 0.25, ((15.35, 45.4), (15.35, 46.7), "Q4.4")),
        T("VIN_HAT", 1.2, ((24.22, 15.1), (24.22, 17.8))),   # core of U2's input pour (the pour carries the current)
        # Pi GPIO18 (ESC PWM) to the arbiter: out between header columns 12/14, then the lane under the header
        T("PI_ESC_PWM", 0.15, ("J1.12", (22.34, 3.5), (22.34, 6.05), (32.6, 6.05), (33.0, 6.45), (33.0, 12.3), "U4.2")),
        # R13 (GPIO18 pull-down below U4) through a bottom-layer jumper from the GPIO18 line
        T("PI_ESC_PWM", 0.2, ((33.0, 12.3), (37.67, 16.7)), layer="B.Cu"),
        T("PI_ESC_PWM", 0.2, ((37.67, 16.7), "R13.1")),
        # Cc2 off the COMP line through a bottom-layer jumper (the SLOPE line runs between them on top)
        T("COMP_6V", 0.15, ("R23.1", (19.55, 25.3))), T("COMP_6V", 0.2, ((19.55, 25.3), (19.55, 23.4), (22.65, 23.4)), layer="B.Cu"),
        T("COMP_6V", 0.15, ((22.65, 23.4), "C34.1")),
        T("COMP_R_6V", 0.15, ("R23.2", (20.35, 22.6), (23.85, 22.6), "C33.1")),
        # Buck-side gate drives on the bottom layer, entering Q2/Q3's gates from below so no trace splits the SW1 pour
        T("HD1_6V", 0.25, ("U3.27", (24.575, 36.45))),
        T("HD1_6V", 0.3, ((24.575, 36.45), (24.9, 36.8), (24.9, 50.5)), layer="B.Cu"),
        T("HD1_6V", 0.25, ((24.9, 50.5), (25.56, 49.6), "Q2.4")),
        # BOOT1: U3 -> D5 cathode -> C23, whose SW1 pad sits on the SW1 line (high-side gate loop kept local)
        T("BT1_6V", 0.25, ("U3.26", (23.925, 37.0), "D5.1")), T("BT1_6V", 0.25, ("D5.1", (24.5, 37.9), (24.5, 40.6), "C23.1")),
        T("LD1_6V", 0.25, ("U3.25", (23.275, 36.45))),
        T("LD1_6V", 0.3, ((23.275, 36.45), (22.4, 37.3), (22.4, 50.0), (21.6, 50.5)), layer="B.Cu"),
        T("LD1_6V", 0.25, ((21.6, 50.5), (21.16, 49.6), "Q3.4")),
        # Output to J4 pin 2: the output pour at the left end of the power row, then along the bottom edge under J3,
        # 1.0 mm on both layers in parallel (2 x 2.9 A IPC-2152 rating, 1.5 A each at the 3 A design current).
        T("SERVO_6V", 1.0, ((7.3, 48.8), (7.3, 60.0), (18.0, 60.0), "J4.2"), current=1.5),
        T("SERVO_6V", 1.0, ((7.3, 50.3), (7.3, 60.0), (18.0, 60.0), "J4.2"), layer="B.Cu", current=1.5),
    ]
    vias = [V("CRSF_RX", 57.9, 24.375), V("SERVO_6V", 7.3, 50.3, 0.8, 0.4), V("SERVO_6V", 7.3, 51.3, 0.8, 0.4),
            V("GND", 17.5, 32.4), V("GND", 21.0, 32.4), V("GND", 24.5, 32.4),  # U3 exposed pad to the bottom plane
            V("VIN_HAT", 24.25, 28.45), V("VIN_HAT", 28.9, 28.9), V("CS_6V", 17.1, 36.45), V("CS_6V", 18.6, 44.6),
            V("LD2_6V", 20.675, 36.5), V("LD2_6V", 15.35, 45.4),
            V("COMP_6V", 19.55, 25.3), V("COMP_6V", 22.65, 23.4), V("HD1_6V", 24.575, 36.45), V("HD1_6V", 24.9, 50.5),
            V("LD1_6V", 23.275, 36.45), V("LD1_6V", 21.6, 50.5),
            V("PI_ESC_PWM", 33.0, 12.3), V("PI_ESC_PWM", 37.67, 16.7),
            V("GND", 20.75, 26.44),  # via-in-pad: C31's ground pad sits between the COMP and SLOPE lines
            V("5V", 6.86, 7.3, 0.8, 0.4), V("SW_5V", 26.0, 14.6), V("SW_5V", 20.0, 14.6), V("GND", 26.6, 13.1)]
    board = rect(0.3, 0.3, WIDTH - 0.3, HEIGHT - 0.3)
    # Bottom ground plane first (the autorouter drops GND pads to it with vias and still routes across it),
    # top ground pour after routing. Solid pad connections: reflow assembly, no starved thermal spokes.
    # Power pours where the current enters fine-pitch pins or crosses between large pads: the copper fills
    # right up to the pads at the net-class clearance instead of a fixed-width neck. The stage measures the
    # narrowest filled cross-section between each pour's terminals (r3_kicad.pours) against IPC-2152.
    def pour(net: str, polygon, current: float, terminals) -> Zone:
        return Zone(net, "F.Cu", polygon, priority=1, name=f"{net} pour", clearance=0.2, solid=True, before_routing=True,
                    current=current, terminals=terminals)

    pours = [
        pour("SW_5V", rect(17.0, 12.85, 22.35, 15.6), 5.0, (("L1.1",), ("U2.19", "U2.20"))),
        pour("VIN_HAT", rect(22.95, 14.3, 26.7, 18.7), round(i_in_5v, 2),
             (((22.5, 17.6, 26.5, 18.7),), ("U2.2", "U2.3", "U2.4", "U2.5"))),
        # Servo buck-boost power row (outlines >= 0.4 mm apart between nets; each keeps the FET gate pads free so
        # their traces can escape). Currents: inductor RMS at 3 A out and the minimum input (Eq. 15).
        pour("VIN_HAT", rect(26.3, 45.75, 35.3, 49.5), round(i_in_servo, 2), (("C17.2", "C18.2"), ("Q2.5",))),
        pour("SW1_6V", ((19.9, 41.0), (25.9, 41.0), (25.9, 48.62), (21.95, 48.62), (21.95, 44.4), (19.9, 44.4)), i_l_servo,
             (("L2.2",), ("Q3.5",), ("Q2.1", "Q2.2", "Q2.3"))),
        pour("CS_6V", ((15.75, 44.85), (21.55, 44.85), (21.55, 48.62), (20.0, 48.62), (20.0, 49.35), (15.3, 49.35),
                       (15.3, 47.45), (15.75, 47.45)), i_l_servo, (("R21.2",), ("Q3.1", "Q3.2", "Q3.3"), ("Q4.1", "Q4.2", "Q4.3"))),
        pour("SW2_6V", ((12.2, 41.0), (17.1, 41.0), (17.1, 44.4), (14.9, 44.4), (14.9, 49.4), (11.0, 49.4), (11.0, 47.45),
                        (12.2, 47.45)), i_l_servo, (("L2.1",), ("Q4.5",), ("Q5.1", "Q5.2", "Q5.3"))),
        pour("SERVO_6V", rect(6.3, 38.4, 10.6, 49.4), 3.0, (("C20.2", "C21.2", "C22.2", "C35.2"), ("Q5.5",))),
        # FET drain -> shunt: between the INA226 sense pad above and Q1's gate pin below; the pour widens into
        # the free area under the shunt so the 1.5 mm gap is crossed by more than the shunt pad's 2 mm height.
        pour("VBAT_PROT", ((39.9, 42.2), (43.95, 42.2), (43.95, 45.2), (42.9, 45.2), (42.9, 46.6), (39.9, 46.6)), 5.0,
             (("R2.1",), ("Q1.1", "Q1.2", "Q1.3"))),
    ]
    zones = [Zone("GND", "B.Cu", board, priority=0, name="GND back", clearance=0.25, solid=True, before_routing=True),
             Zone("GND", "F.Cu", board, priority=0, name="GND front", clearance=0.25, solid=True), *pours]
    return zones, tracks, vias


def schematic_groups() -> list:
    """Functional blocks on the A3 sheet (x0, y0, x1, y1 in mm; title block at bottom right)."""
    from r3_kicad.schematic_layout import Group

    return [
        Group("Battery input: XT30, reverse polarity, TVS, hold-up", (15, 15, 135, 85),
              ("J2", "Q1", "D1", "R1", "D2", "C3", "#FLG04")),
        Group("HAT current monitor (INA226, 0x40)", (15, 88, 135, 160), ("R2", "R3", "R4", "C1", "U1", "C2", "#FLG02")),
        Group("5.1 V / 5 A Pi rail (TPS568230)", (140, 15, 405, 100),
              ("U2", "L1", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11", "C12", "C13", "C14", "R5", "C15", "R6",
               "R7", "R8", "R9", "D3", "C16")),
        Group("6.0 V servo rail (LM5175 4-switch buck-boost, 3 A, no dropout)", (140, 105, 580, 202),
              ("U3", "Q2", "Q3", "Q4", "Q5", "L2", "R21", "D5", "D6", "C17", "C18", "C19", "C20", "C21", "C22", "C35",
               "C23", "C24", "C30", "R10", "R11", "#FLG05")),
        Group("6.0 V servo rail: LM5175 timing, UVLO, soft start, slope and compensation", (15, 272, 300, 380),
              ("R12", "R24", "R22", "R25", "C31", "C32", "R23", "C33", "C34")),
        Group("Raspberry Pi 40-pin header (onboard I2C pull-ups)", (15, 163, 135, 268), ("J1", "#FLG03", "#FLG01")),
        Group("Failsafe arbiter (ATtiny412): PWM passes only while GPIO27 toggles; ESC PWM isolator", (140, 205, 400, 290),
              ("U4", "C25", "R13", "R15", "R16", "D4", "U6", "C36", "U7", "C37", "C38", "C39", "R14", "R26",
               "#FLG06", "#FLG07")),
        Group("Owned GY-521 I2C harness (0x68), ESD; no additional pull-ups", (405, 205, 580, 290), ("J9", "D7", "D8")),
        Group("Vehicle connectors", (410, 15, 580, 100), ("J3", "J4", "J5", "J6", "J7", "R17", "R20", "J8")),
        Group("Mounting", (305, 300, 400, 330), ("H1", "H2", "H3", "H4")),
    ]
