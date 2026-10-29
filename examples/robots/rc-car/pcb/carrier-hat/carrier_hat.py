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

WIDTH, HEIGHT = 65.0, 56.5
HOLES = ((3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5))   # HAT spec, M2.5, 58 x 49 mm pattern
GPIO_PIN1 = (32.5 - 9.5 * 2.54, 3.5 + 1.27)                     # header centred at x 32.5, rows 3.5 -/+ 1.27 mm from the edge

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
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
#   bottom-left   6.0 V servo buck U3/L2 above J3 (ESC) and J4 (servo)
#   bottom-right  XT30 battery input, P-FET, Kelvin shunt, TVS, input bulk C3; INA226 above the shunt
#   top-right     failsafe arbiter U4, IMU U5 + I2C pull-ups; J5 receiver on the right edge
PLACEMENT: dict[str, Placement] = {
    # connectors
    "J2": P(51.5, 44.5, 270), "J5": P(61.7, 25.0, 90), "J6": P(3.3, 22.5, 270), "J7": P(10.0, 30.5, 270),
    "J3": P(13.0, 53.5, 90), "J4": P(25.0, 53.5, 90), "J8": P(36.0, 25.0, 90),
    # 5.1 V buck (U2 at 90 deg: SW pins 19/20 face L1, VIN pins 2-5 face down, FB/EN/SS/MODE face up)
    "U2": P(24.0, 13.0, 90), "L1": P(15.5, 13.0, 180), "C13": P(21.3, 16.2, 180), "C14": P(20.9, 11.2, 90),
    "R7": P(20.7, 7.9, 270), "R8": P(22.3, 7.9, 90), "R5": P(23.9, 7.9, 270), "C15": P(25.5, 7.9, 270),
    "R6": P(27.1, 7.9, 90), "C16": P(27.7, 10.9, 0), "R9": P(30.0, 11.2, 90), "D3": P(32.0, 12.0, 270),
    "C7": P(26.6, 16.4, 0),
    "C4": P(21.6, 20.1, 270), "C5": P(24.2, 20.1, 270), "C6": P(26.8, 20.1, 270),
    "C8": P(7.5, 9.0, 180), "C9": P(7.5, 11.4, 180), "C10": P(7.5, 13.8, 180), "C11": P(7.5, 16.2, 180),
    "C12": P(14.5, 23.0, 0), "R20": P(14.5, 31.0, 90),
    # 6.0 V servo buck (U3 at 0 deg: VIN/BST/SW/GND on the right toward L2, EN/FB/VREG5 on the left)
    "U3": P(14.0, 45.0, 0), "L2": P(25.2, 45.0, 0), "C23": P(19.3, 44.0, 0),
    "C17": P(15.8, 40.6, 180), "C18": P(15.8, 38.0, 180), "C19": P(18.9, 40.3, 90),
    "C20": P(31.9, 47.0, 0), "C21": P(31.9, 49.5, 0), "C22": P(31.0, 43.4, 270),
    "R10": P(8.0, 42.4, 0), "R11": P(8.0, 44.0, 180), "C24": P(8.0, 45.6, 0), "R12": P(8.0, 47.2, 0),
    # battery input, protection, shunt, monitor
    "Q1": P(45.0, 44.5, 0), "R2": P(37.5, 43.6, 180), "D2": P(43.0, 49.5, 0), "D1": P(45.8, 52.8, 0),
    "R1": P(49.6, 52.8, 0), "C3": P(35.0, 36.5, 180),
    "U1": P(45.5, 36.5, 180), "C2": P(45.5, 33.2, 0), "R3": P(43.6, 40.3, 90), "R4": P(45.2, 40.3, 90), "C1": P(46.8, 40.3, 90),
    # failsafe arbiter and its LED
    "U4": P(38.5, 13.0, 0), "C25": P(34.0, 12.3, 90), "R13": P(38.5, 17.8, 0), "R14": P(42.0, 17.8, 0),
    "R15": P(44.0, 13.0, 90), "R16": P(35.0, 17.8, 0), "D4": P(35.0, 19.8, 0),
    # IMU, I2C pull-ups, servo PWM resistor
    "U5": P(52.0, 13.0, 0), "C26": P(56.0, 12.4, 90), "C27": P(50.2, 17.9, 270), "C28": P(52.25, 18.4, 270),
    "C29": P(53.0, 8.4, 90), "R18": P(46.6, 9.6, 90), "R19": P(48.2, 9.6, 90), "R17": P(58.6, 9.6, 90),
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
    add(Part("J3", "Connector_Generic:Conn_01x03", "ESC", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
             {"1": "ESC_PWM", "3": "GND"}, no_connect=("2",), sch=(386.08, 165.1, 0), pcb=P(9.0, 51.5, 90),
             mpn="PH1-03-UA", manufacturer="Adam Tech",
             description="ESC signal header; pin 2 (ESC BEC) is not connected on the HAT", connector="J_ESC"))
    add(Part("J4", "Connector_Generic:Conn_01x03", "SERVO", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
             {"1": "SERVO_PWM", "2": "SERVO_6V", "3": "GND"}, sch=(386.08, 190.5, 0), pcb=P(17.0, 51.5, 90),
             mpn="PH1-03-UA", manufacturer="Adam Tech", description="Steering servo header, 6.0 V from U3", connector="J_SERVO"))
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
    add(Part("D2", "Diode:SMAJ16A", "SMAJ16A", "Diode_SMD:D_SMA", {"1": "VBAT_PROT", "2": "GND"}, sch=(78.74, 203.2, 90),
             pcb=P(45.0, 51.0, 0), mpn="SMAJ16A", manufacturer="Littelfuse",
             description="400 W TVS, VRWM 16 V, VBR 17.8 V min, VC 26 V"))
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

    # ------------------------------------------------------------------ U3: 6.0 V servo rail (TPS56528)
    add(Part("U3", "Regulator_Switching:TPS56528DDA", "TPS56528", "Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias",
             {"1": "EN_6V", "2": "FB_6V", "3": "VREG5_6V", "5": "GND", "6": "SW_6V", "7": "BST_6V", "8": "VIN_HAT", "9": "GND"},
             no_connect=("4",), sch=(203.2, 165.1, 0), pcb=P(24.0, 40.0, 0), mpn=servo.ic.part,
             manufacturer="Texas Instruments", description="4.5-18 V in, 5 A sync buck; 6.0 V servo rail, 5.5-7.8 A current limit"))
    add(Part("L2", "Device:L", "3.3u", servo.inductor.footprint, {"1": "SW_6V", "2": "SERVO_6V"}, sch=(241.3, 162.56, 90),
             pcb=P(15.0, 43.0, 0), mpn=servo.inductor.part, manufacturer="Coilcraft",
             description="3.3 uH, 19.5 mOhm, Isat 12.3 A molded inductor"))
    for i, x in enumerate((28.5, 31.0)):
        add(c(f"C{17 + i}", "22u 25V", "VIN_HAT", "GND", (175.26 + 10.16 * i, 200.66, 0), P(x, 40.0, 90), servo.c_in.part,
              "U3 input capacitor 22 uF 25 V X5R", C1206))
    add(c("C19", "100n", "VIN_HAT", "GND", (195.58, 200.66, 0), P(27.0, 36.0, 90), "GRM188R71H104KA93D", "U3 VIN HF decoupling"))
    for i, x in enumerate((9.0, 11.5, 14.0)):
        add(c(f"C{20 + i}", "22u 16V", "SERVO_6V", "GND", (264.16 + 10.16 * i, 175.26, 0), P(x, 36.5, 90), servo.c_out.part,
              "U3 output capacitor 22 uF 16 V X5R", C1206))
    add(c("C23", "100n", "BST_6V", "SW_6V", (226.06, 142.24, 90), P(20.0, 37.0, 0), "GRM188R71H104KA93D", "U3 bootstrap"))
    add(c("C24", "470n", "VREG5_6V", "GND", (175.26, 147.32, 0), P(20.0, 44.0, 0), "GRM188R71C474KA88D",
          "U3 VREG5 bypass (datasheet 0.47 uF)"))
    add(r("R10", "453k", "SERVO_6V", "FB_6V", (241.3, 185.42, 0), P(21.5, 46.5, 90), "RC0603FR-07453KL", "U3 feedback upper"))
    add(r("R11", "49.9k", "FB_6V", "GND", (241.3, 203.2, 0), P(23.0, 46.5, 90), "RC0603FR-0749K9L", "U3 feedback lower"))
    add(r("R12", "100k", "VIN_HAT", "EN_6V", (165.1, 185.42, 0), P(28.0, 44.0, 90), "RC0603FR-07100KL",
          "U3 EN pull-up (internal 180-700 k to GND keeps EN >= 2.9 V at 4.5 V)"))

    # ------------------------------------------------------------------ U4: failsafe arbiter (ATtiny412)
    add(Part("U4", "MCU_Microchip_ATtiny:ATtiny412-SS", "ATtiny412", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
             {"1": "3V3", "2": "PI_ESC_PWM", "3": "HEARTBEAT", "4": "FS_LED", "6": "UPDI", "7": "ESC_OUT", "8": "GND"},
             no_connect=("5",), sch=(304.8, 215.9, 0), pcb=P(37.0, 25.0, 0), mpn="ATTINY412-SSN", manufacturer="Microchip",
             description="Failsafe arbiter: passes ESC PWM only while GPIO27 heartbeat toggles, else 1500 us neutral"))
    add(c("C25", "100n", "3V3", "GND", (281.94, 203.2, 0), P(33.5, 25.0, 90), "GRM188R71H104KA93D", "U4 decoupling"))
    add(r("R13", "100k", "PI_ESC_PWM", "GND", (281.94, 228.6, 0), P(37.0, 20.5, 90), "RC0603FR-07100KL",
          "Holds the Pi PWM input low while the Pi boots"))
    add(r("R14", "220", "ESC_OUT", "ESC_PWM", (345.44, 215.9, 90), P(40.5, 27.5, 0), "RC0603FR-07220RL", "ESC PWM series resistor"))
    add(r("R15", "100k", "ESC_PWM", "GND", (358.14, 228.6, 0), P(43.0, 28.0, 90), "RC0603FR-07100KL",
          "Holds ESC_PWM low while U4 is unpowered or in reset"))
    add(r("R16", "1k", "FS_LED", "LED_A", (330.2, 190.5, 90), P(40.5, 22.5, 0), "RC0603FR-071KL", "Failsafe LED resistor"))
    add(Part("D4", "Device:LED", "red", "LED_SMD:LED_0603_1608Metric", {"1": "GND", "2": "LED_A"}, sch=(345.44, 190.5, 180),
             pcb=P(43.5, 22.5, 0), mpn="150060RS75000", manufacturer="Wurth Elektronik", description="Failsafe active indicator"))

    # ------------------------------------------------------------------ servo PWM, IMU, I2C, encoder
    add(r("R17", "220", "PI_SERVO_PWM", "SERVO_PWM", (345.44, 165.1, 90), P(51.5, 9.0, 90), "RC0603FR-07220RL",
          "Servo PWM series resistor"))
    add(Part("U5", "Sensor_Motion:MPU-6050", "MPU-6050", "Sensor_Motion:InvenSense_QFN-24_4x4mm_P0.5mm",
             {"1": "GND", "8": "3V3", "9": "GND", "10": "REGOUT", "11": "GND", "13": "3V3", "18": "GND", "20": "CPOUT",
              "23": "I2C_SCL", "24": "I2C_SDA"}, no_connect=("6", "7", "12"), sch=(116.84, 63.5, 0), pcb=P(48.0, 20.0, 0),
             mpn="MPU-6050", manufacturer="TDK InvenSense", description="6-axis IMU, I2C 0x68 (AD0 = GND)"))
    add(c("C26", "100n", "3V3", "GND", (91.44, 27.94, 0), P(52.0, 20.0, 90), "GRM188R71H104KA93D", "U5 VDD decoupling"))
    add(c("C27", "10n", "3V3", "GND", (101.6, 27.94, 0), P(48.0, 16.0, 0), "GRM188R71H103KA01D", "U5 VLOGIC decoupling"))
    add(c("C28", "100n", "REGOUT", "GND", (142.24, 93.98, 0), P(48.0, 24.0, 0), "GRM188R71H104KA93D", "U5 REGOUT filter"))
    add(c("C29", "2.2n", "CPOUT", "GND", (154.94, 93.98, 0), P(51.5, 23.5, 0), "GRM188R71H222KA01D",
          "U5 charge pump (50 V rated)"))
    add(r("R18", "4.7k", "3V3", "I2C_SDA", (76.2, 27.94, 0), P(12.0, 7.5, 90), "RC0603FR-074K7L", "I2C SDA pull-up"))
    add(r("R19", "4.7k", "3V3", "I2C_SCL", (66.04, 27.94, 0), P(13.5, 7.5, 90), "RC0603FR-074K7L", "I2C SCL pull-up"))
    add(r("R20", "10k", "3V3", "ENC", (355.6, 129.54, 0), P(8.5, 33.0, 90), "RC0603FR-0710KL", "Encoder open-drain pull-up"))

    # ------------------------------------------------------------------ mechanical + ERC power flags
    for i, (x, y) in enumerate(HOLES):
        add(Part(f"H{i + 1}", "Mechanical:MountingHole", "M2.5", "MountingHole:MountingHole_2.7mm_M2.5",
                 sch=(20.32 + 12.7 * i, 20.32, 0), pcb=P(x, y), in_bom=False, description="HAT M2.5 mounting hole"))
    for i, net in enumerate(("GND", "VIN_HAT", "3V3", "VBAT")):
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
        NetClass("PowerIn", 0.3, 0.2, 0.8, 0.4, ("VBAT", "VBAT_PROT", "VIN_HAT"), 5.0),
        NetClass("Servo", 0.3, 0.2, 0.8, 0.4, ("SERVO_6V",), 3.0),
        NetClass("SwitchNode", 0.3, 0.2, 0.8, 0.4, ("SW_5V", "SW_6V"), 5.0),
        NetClass("Ground", 0.3, 0.2, 0.6, 0.3, ("GND",), 5.0),
        NetClass("Analog", 0.2, 0.2, 0.6, 0.3, ("SENSE_P", "SENSE_N", "INA_INP", "INA_INN", "FB_5V", "FB_6V"), 0.0),
    ]
    rules = {"min_clearance": 0.15, "min_track_width": 0.15, "min_via_diameter": 0.5, "min_via_drill": 0.3,
             "min_annular_ring": 0.1, "copper_edge_clearance": 0.3, "hole_clearance": 0.25, "hole_to_hole": 0.5,
             "delta_t_power": dt_power, "track_widths": [0.2, 0.25, 0.3, 0.4, 0.8, 1.0, w_servo, w5]}

    # J1 sits where the HAT specification puts the 40-pin header: pin 1 inboard, pin 2 at the edge.
    j1 = Placement(*gpio_pad(1), 90.0, "B", anchors=(("1", gpio_pad(1)), ("2", gpio_pad(2)), ("3", gpio_pad(3))))
    placement = {**PLACEMENT, "J1": j1}
    parts[:] = [replace(part, pcb=placement.get(part.ref, part.pcb)) for part in parts]
    zones, tracks, vias = copper(w5, w_servo, pi.values["i_in_vin_min"], servo.values["i_in_vin_min"])
    intent = BoardIntent(name="carrier-hat", title="R3 RC carrier HAT", component="hat", catalog_type="r3-carrier-hat",
                         width=WIDTH, height=HEIGHT, corner_radius=3.0, thickness=1.6, copper_oz=1.0, holes=HOLES,
                         parts=parts, net_classes=classes, rules=rules, zones=zones, power_tracks=tracks, vias=vias,
                         net_currents={"VBAT": 5.0, "VBAT_PROT": 5.0, "VIN_HAT": 5.0, "5V": 5.0, "SW_5V": 5.0,
                                       "SERVO_6V": 3.0, "SW_6V": 3.0, "GND": 5.0},
                         frame_origin=(WIDTH / 2, HEIGHT / 2), origin=(100.0, 44.0))
    intent.notes += [
        f"IPC-2152 external fit, 1 oz, {dt_power:g} C rise: 5 A -> {w5} mm, 3 A -> {w_servo} mm; signals 0.2 mm.",
        "INA226 measures only the HAT branch (Pi + servo + HAT logic). The ESC/motor branch splits off in the XT60 "
        "Y-harness before the HAT, so motor current is not visible to the Pi.",
        "J_ESC pin 2 (ESC BEC) is not connected: the servo is powered by U3, never by the ESC BEC.",
    ]
    return intent


def rect(x0: float, y0: float, x1: float, y1: float) -> tuple[tuple[float, float], ...]:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def copper(w5: float, w_servo: float, i_in_5v: float, i_in_servo: float) -> tuple[list[Zone], list[PowerTrack], list[Via]]:
    """Power copper routed by hand before the autorouter, the way a person routes power first: wide locked
    tracks along each rail's current path (necked down only at fine-pitch pins), the 5 V path to the Pi pins
    doubled on the bottom layer, regulator grounds tied into their exposed pads. GND pours on both layers
    are added after routing. Freerouting connects everything else."""
    # Segment currents: the full HAT input on the spine; each regulator's input current at the 6.0 V
    # minimum battery (regulators.py: Pout / (eta Vin)) on its own branch.
    T, V = PowerTrack, Via
    tracks = [
        # battery -> P-FET drain -> source -> shunt -> TVS
        T("VBAT", w5, ("J2.1", (46.2, 44.5)), current=5.0),
        T("VBAT_PROT", 1.6, ((43.5, 44.1), (40.6, 44.1)), current=5.0),
        T("VBAT_PROT", 1.2, ((40.6, 44.6), (41.0, 48.9)), current=1.0),
        # VIN_HAT spine: shunt -> bulk C3 -> north to the 5 V buck input bus, west to the servo buck input bar
        T("VIN_HAT", w5, ((35.0, 42.4), (38.3, 38.6), (38.3, 31.0), (29.6, 31.0), (29.6, 18.6)), current=5.0),
        T("VIN_HAT", 2.2, ((20.7, 18.6), (29.6, 18.6)), current=round(i_in_5v, 2)),
        T("VIN_HAT", 1.4, ((24.2, 18.6), (24.2, 14.4)), current=round(i_in_5v, 2)),
        T("VIN_HAT", 2.0, ((29.6, 31.0), (23.6, 36.6), (16.6, 36.6)), current=round(i_in_servo, 2)),
        T("VIN_HAT", 1.4, ((16.6, 36.6), (16.6, 42.9)), current=round(i_in_servo, 2)),
        T("VIN_HAT", 0.6, ((16.6, 41.075), "C19.1"), current=0.5),
        # 5.1 V buck: switch node L1 -> U2 pins 19/20 (necked at the pins), pin 6 over the back, bootstrap stub
        T("SW_5V", 2.6, ((18.0, 13.9), (19.9, 13.9)), current=5.0),
        T("SW_5V", 1.6, ((19.9, 13.68), (21.2, 13.68)), current=5.0),
        T("SW_5V", 0.65, ((21.2, 13.68), (22.5, 13.68)), current=5.0),
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
        T("GND", 0.25, ("U5.11", (52.75, 15.6), (53.3, 16.1))),
        T("GND", 0.25, ("U5.9", (51.75, 15.5), (51.45, 16.6))),
        T("3V3", 0.25, ("U5.8", (51.25, 15.5), (50.2, 16.4), "C27.1")),
        T("3V3", 0.25, ("C27.1", (49.6, 16.4), (48.2, 15.0), "R19.1")),
        T("REGOUT", 0.25, ("U5.10", "C28.1")),
        T("FB_6V", 0.25, ("R10.2", "R11.1")),
        T("5V", 0.5, ("C11.1", (7.0, 18.5), "J6.1")),
        # CRSF UART to the receiver (J5, right edge): the Pi's TXD/RXD sit on the header's edge row, so the
        # pair is routed by hand along the two corridors that row leaves: TX on top between the board edge
        # and the row, RX on the bottom between the two rows; both drop past H2's keep-out to J5.
        T("CRSF_TX", 0.2, ("J1.8", (15.99, 0.85), (55.36, 0.85), (55.36, 6.3), (57.1, 6.3), (57.1, 25.625), "J5.2")),
        T("CRSF_RX", 0.2, ("J1.10", (18.53, 3.5), (57.9, 3.5), (57.9, 24.375)), layer="B.Cu"),
        T("CRSF_RX", 0.2, ((57.9, 24.375), "J5.3")),
        # U2 grounds into the exposed pad, then out to a stitching via
        T("GND", 0.2, ("U2.18", "U2.21")), T("GND", 0.2, ("U2.13", "U2.21")), T("GND", 0.2, ("U2.8", "U2.21")),
        T("GND", 0.2, ("U2.7", "U2.8")), T("GND", 0.3, ("U2.8", (26.6, 13.1))),
        # 5 V: L1 -> output capacitor column -> around H1 -> Pi pins 2 and 4, on both layers; hold-up C12
        T("5V", 2.5, ((10.3, 13.0), (13.1, 13.0)), current=5.0),
        T("5V", 2.6, ((10.3, 17.2), (10.3, 7.8)), current=5.0),
        # Between H1's standoff/screw-head contact circle and J1 pins 1/2 only a 0.7 mm neck fits; it is
        # doubled on the bottom layer (fed by one via) and 3.5 mm long. The bottom layer stays open below
        # the header so pins 1-10 can escape.
        T("5V", 2.0, ((10.0, 6.86), (6.85, 6.86)), current=5.0),
        T("5V", 0.7, ((6.85, 6.86), (6.85, 2.23), (7.6, 2.23)), current=2.5),
        T("5V", 1.6, ((7.6, 2.23), "J1.2", "J1.4"), current=2.5),
        T("5V", 0.7, ((6.85, 7.3), (6.85, 2.23), (7.6, 2.23)), layer="B.Cu", current=2.5),
        T("5V", 1.6, ((7.6, 2.23), "J1.2", "J1.4"), layer="B.Cu", current=2.5),
        T("5V", 1.2, ((10.3, 17.2), (10.9, 21.8)), current=1.0),
        # 6.0 V servo buck: U3 SW -> L2 (necked at the pin), bootstrap, ground into the pad, output to J4.2
        T("SW_6V", 0.6, ("U3.6", (18.0, 45.635)), current=3.0),
        T("SW_6V", 1.6, ((18.0, 45.635), (22.0, 45.635)), current=3.0),
        T("SW_6V", 0.6, ("C23.2", (20.1, 45.2))),
        T("BST_6V", 0.3, ("U3.7", "C23.1")),
        T("GND", 0.6, ("U3.5", (14.8, 46.3))),
        T("SERVO_6V", w_servo, ((27.56, 47.5), "J4.2"), current=3.0),
        T("SERVO_6V", w_servo, ((27.56, 47.0), (29.9, 47.0), (29.9, 49.5), "C21.1"), current=1.0),
        T("SERVO_6V", w_servo, ((27.56, 42.0), "C22.1"), current=1.0),
    ]
    vias = [V("5V", 6.85, 7.3, 0.8, 0.4), V("SW_5V", 26.0, 14.6), V("SW_5V", 20.0, 14.6), V("GND", 26.6, 13.1), V("GND", 53.3, 16.1), V("GND", 51.45, 16.6), V("CRSF_RX", 57.9, 24.375)]
    board = rect(0.3, 0.3, WIDTH - 0.3, HEIGHT - 0.3)
    # Bottom ground plane first (the autorouter drops GND pads to it with vias and still routes across it),
    # top ground pour after routing. Solid pad connections: reflow assembly, no starved thermal spokes.
    zones = [Zone("GND", "B.Cu", board, priority=0, name="GND back", clearance=0.25, solid=True, before_routing=True),
             Zone("GND", "F.Cu", board, priority=0, name="GND front", clearance=0.25, solid=True)]
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
        Group("6.0 V servo rail (TPS56528, 5.5-7.8 A limit)", (140, 105, 405, 170),
              ("U3", "L2", "C17", "C18", "C19", "C20", "C21", "C22", "C23", "C24", "R10", "R11", "R12")),
        Group("Raspberry Pi 40-pin header, I2C pull-ups", (15, 163, 135, 268), ("J1", "R18", "R19", "#FLG03", "#FLG01")),
        Group("Failsafe arbiter (ATtiny412): PWM passes only while GPIO27 toggles", (140, 175, 290, 228),
              ("U4", "C25", "R13", "R14", "R15", "R16", "D4")),
        Group("IMU (MPU-6050, 0x68)", (140, 230, 290, 288), ("U5", "C26", "C27", "C28", "C29")),
        Group("Vehicle connectors", (295, 175, 405, 250), ("J3", "J4", "J5", "J6", "J7", "R17", "R20", "J8")),
        Group("Mounting", (40, 270, 135, 290), ("H1", "H2", "H3", "H4")),
    ]
