"""SPICE (ngspice) subcircuit of the carrier HAT power path for the electrical twin.

XT30 contact -> P-FET channel (DMP3013SFV) -> 2 mOhm shunt -> VIN_HAT (ceramics +
220 uF polymer) -> an *averaged* CCM buck, TPS568230 (5.1 V, Pi), and an averaged
current-mode 4-switch buck-boost, LM5175 (6.0 V, servo). The buck's switch node is
the controlled source D * Vin with an integrating error amplifier on the real
feedback divider; D is clamped to the part's maximum duty and the input draws
D * I_L. The buck-boost has no dropout: a PI error amplifier on its divider
commands the output current (current-mode control, inner loop assumed ideal),
bounded by the minimum current limit (buck valley, boost peak scaled by
Vin eta / Vout), and the input draws P_out / (eta V_in). Each converter is
enabled only above its VIN UVLO (with hysteresis). Switching ripple is not modelled.
"""

from __future__ import annotations

from .regulators import BuckBoostDesign, BuckDesign


def _buck(tag: str, design: BuckDesign, vin: str, vout: str, uvlo_on: float, uvlo_off: float,
          current_limit: float | None) -> list[str]:
    ic, ind = design.ic, design.inductor
    c_out = design.values["c_out_effective"]
    lines = [
        f"* --- {tag}: {ic.part}, {design.values['v_out_nominal']:.3f} V, L {ind.inductance * 1e6:.1f} uH, Cout {c_out * 1e6:.0f} uF eff.",
        f"RFB1_{tag} {vout} FB_{tag} {design.r_fb_upper:.6g}",
        f"RFB2_{tag} FB_{tag} 0 {design.r_fb_lower:.6g}",
        f"* UVLO comparator with hysteresis: EN_{tag} = 1 above {uvlo_on} V, 0 below {uvlo_off} V (datasheet)",
        f"BEN_{tag} EN_{tag} 0 V = V({vin}) > {uvlo_on} ? 1 : (V({vin}) < {uvlo_off} ? 0 : V(ENM_{tag}))",
        f"RENM_{tag} EN_{tag} ENM_{tag} 1k",
        f"CENM_{tag} ENM_{tag} 0 1n",
        f"* Error integrator (crossover ~{ic.f_sw / 30e3:.0f} kHz, estimated)",
        f"BERR_{tag} 0 NC_{tag} I = {6.2832 * ic.f_sw / 30:.6g}*({ic.v_ref} - V(FB_{tag}))",
        f"CC_{tag} NC_{tag} 0 1",
        f"RCL_{tag} NC_{tag} 0 1e9",
        f"BD_{tag} ND_{tag} 0 V = V(EN_{tag}) * min(max(V(NC_{tag}), 0), {ic.max_duty:.4f})",
        f"BSW_{tag} NSW_{tag} 0 V = V(ND_{tag}) * V({vin})",
        f"BIN_{tag} {vin} 0 I = V(ND_{tag}) * I(VL_{tag})",
        f"RST_{tag} NSW_{tag} NL_{tag} {(0.7 * ic.rds_on_high + 0.3 * ic.rds_on_low):.4g}",
        f"L_{tag} NL_{tag} NL2_{tag} {ind.inductance:.4g}",
        f"VL_{tag} NL2_{tag} ND2_{tag} 0",
        f"RDCR_{tag} ND2_{tag} {vout} {ind.dcr_typ:.4g}",
        f"COUT_{tag} {vout} NCE_{tag} {c_out:.4g}",
        f"RCE_{tag} NCE_{tag} 0 {0.003 / max(design.c_out.count, 1):.4g}",
    ]
    if current_limit is not None:
        lines += [f"* Valley current limit {current_limit:g} A (datasheet minimum): pull duty down above the limit",
                  f"BOCL_{tag} NC_{tag} 0 I = I(VL_{tag}) > {current_limit} ? 1e3*(I(VL_{tag}) - {current_limit}) : 0"]
    return lines


def _buck_boost(tag: str, design: BuckBoostDesign, vin: str, vout: str) -> list[str]:
    v, ic = design.values, design.ic
    c_out = v["c_out_effective"]
    eta = v["efficiency_min"]
    ratio = design.r_fb_lower / (design.r_fb_lower + design.r_fb_upper)
    i_ctl = 2.0 * design.req.i_peak                  # control range of the current command
    kp = 2 * 3.14159 * v["f_bw"] * c_out / (i_ctl * ratio)
    ki = kp * 2 * 3.14159 * v["f_bw"] / 5.0
    i_buck = v["i_out_limit_buck_min"]
    i_l_pk = v["i_l_peak_limit_min"] - v["ripple_boost_vin_min"] / 2.0
    return [
        f"* --- {tag}: {ic.part} 4-switch buck-boost, {v['v_out_nominal']:.3f} V, Cout {c_out * 1e6:.0f} uF eff., "
        f"crossover {v['f_bw'] / 1e3:.0f} kHz",
        f"RFB1_{tag} {vout} FB_{tag} {design.r_fb_upper:.6g}",
        f"RFB2_{tag} FB_{tag} 0 {design.r_fb_lower:.6g}",
        f"* EN/UVLO divider thresholds: on {v['uvlo_rising']:.3f} V, off {v['uvlo_falling']:.3f} V (Eq. 1/2)",
        f"BEN_{tag} EN_{tag} 0 V = V({vin}) > {v['uvlo_rising']:.4g} ? 1 : (V({vin}) < {v['uvlo_falling']:.4g} ? 0 : V(ENM_{tag}))",
        f"RENM_{tag} EN_{tag} ENM_{tag} 1k",
        f"CENM_{tag} ENM_{tag} 0 1n",
        f"* PI error amplifier -> output-current command (0..{i_ctl:g} A)",
        f"BERR_{tag} 0 NC_{tag} I = {ki:.6g}*({ic.v_ref} - V(FB_{tag}))",
        f"CC_{tag} NC_{tag} 0 1",
        f"RCL_{tag} NC_{tag} 0 1e9",
        f"BCMD_{tag} ND_{tag} 0 V = V(EN_{tag}) * min(max(V(NC_{tag}) + {kp:.6g}*({ic.v_ref} - V(FB_{tag})), 0), 1)",
        f"* current limit: buck valley {i_buck:.2f} A; boost peak {i_l_pk:.2f} A x Vin x {eta:.3f} / Vout (datasheet minimum)",
        f"BOUT_{tag} 0 {vout} I = min(V(ND_{tag})*{i_ctl:g}, min({i_buck:.4g}, {i_l_pk:.4g}*max(V({vin}),0.5)*{eta:.4g}/"
        f"{v['v_out_nominal']:.4g}))",
        f"BIN_{tag} {vin} 0 I = V({vout})*I(VOUT_{tag})/({eta:.4g}*max(V({vin}),0.5))",
        f"VOUT_{tag} NSO_{tag} 0 0",
        f"BSO_{tag} NSO_{tag} 0 I = min(V(ND_{tag})*{i_ctl:g}, min({i_buck:.4g}, {i_l_pk:.4g}*max(V({vin}),0.5)*{eta:.4g}/"
        f"{v['v_out_nominal']:.4g}))",
        f"BQ_{tag} {vin} 0 I = V(EN_{tag}) * {ic.i_q:.4g}",
        f"COUT_{tag} {vout} NCE_{tag} {c_out:.4g}",
        f"RCE_{tag} NCE_{tag} 0 {0.003 / max(design.c_out.count, 1):.4g}",
    ]


# Littelfuse SMAJ series (400 W, unidirectional), datasheet: stand-off V_RWM, breakdown V_BR min/max at
# I_T = 1 mA, clamp V_C at the rated 10/1000 us peak pulse current I_PP.
TVS_PARTS: dict[str, dict[str, float]] = {
    "SMAJ10A": {"v_rwm": 10.0, "v_br_min": 11.1, "v_br_max": 12.3, "v_c": 17.0, "i_pp": 23.5},
    "SMAJ16A": {"v_rwm": 16.0, "v_br_min": 17.8, "v_br_max": 19.7, "v_c": 26.0, "i_pp": 15.4},
}


def tvs_clamp(part: str, current: float) -> float:
    """Clamp voltage at ``current`` (A): V_BR,max + (V_C - V_BR,max) I / I_PP (linear dynamic resistance)."""
    d = TVS_PARTS[part]
    return d["v_br_max"] + (d["v_c"] - d["v_br_max"]) * current / d["i_pp"]


def power_subcircuit(pi: BuckDesign, servo: BuckBoostDesign, name: str = "R3_CARRIER_HAT_POWER", tvs: str = "SMAJ10A") -> str:
    d = TVS_PARTS[tvs]
    # SPICE diode: breakdown at the middle of the V_BR window, series resistance from the clamp line.
    bv, rs = 0.5 * (d["v_br_min"] + d["v_br_max"]), (d["v_c"] - d["v_br_max"]) / d["i_pp"]
    lines = [
        f"* {name}: R3 carrier HAT input path, 5.1 V Pi rail and 6.0 V servo rail (averaged models)",
        "* Generated by r3_kicad.spice from the board design; datasheet values unless marked (est).",
        "* Pins: VBAT GND V5 V6 (V5 = Pi 5 V at the GPIO header, V6 = servo header pin 2)",
        f".subckt {name} VBAT 0 V5 V6",
        "* XT30 contact pair (2 x 1.5 mOhm, est), DMP3013SFV channel 12 mOhm at VGS = -7.4 V (est. from 9.5/13 mOhm curve)",
        "RCONN VBAT NC1 3m",
        "RFET NC1 VBATP 12m",
        "DBODY NC1 VBATP DFET",
        ".model DFET D(IS=1e-9 N=1.5 RS=10m)",
        f"* {tvs} TVS: VRWM {d['v_rwm']:g} V, VBR {d['v_br_min']:g}-{d['v_br_max']:g} V, VC {d['v_c']:g} V at {d['i_pp']:g} A",
        "DTVS 0 VBATP DTVS",
        f".model DTVS D(IS=1e-12 BV={bv:.4g} IBV=1m RS={rs:.3g})",
        "* 2 mOhm Kelvin shunt (INA226 senses across it)",
        "RSH VBATP VIN 2m",
        "* VIN_HAT capacitance: 5 x 22 uF 25 V X5R (65 % at 7.4 V, est) + 220 uF hybrid polymer (27 mOhm ESR)",
        f"CIN VIN NCI {pi.c_in.effective + servo.c_in.effective:.4g}",
        "RCI NCI 0 1m",
        "CBULK VIN NCB 220u",
        "RCB NCB 0 27m",
        "IQ VIN 0 3m",
    ]
    lines += _buck("PI", pi, "VIN", "V5", uvlo_on=pi.ic.uvlo_rising_max, uvlo_off=pi.ic.uvlo_falling_min, current_limit=None)
    lines += _buck_boost("SERVO", servo, "VIN", "V6")
    lines += [
        "* 5 V hold-up: 220 uF hybrid polymer on the Pi rail",
        "CHOLD V5 NCH 220u",
        "RCH NCH 0 27m",
        f".ends {name}",
        "",
        "* Example testbench (ngspice): Pi at 6.5 W, servo stall at 2.5 A, 2S pack sagging to 6 V (the servo stays at 6.0 V)",
        "* VB VBAT 0 PWL(0 7.4 5m 7.4 6m 6.0 20m 6.0 21m 7.4)",
        f"* XHAT VBAT 0 V5 V6 {name}",
        "* RPI V5 0 4.0",
        "* RSERVO V6 0 2.4",
        "* .tran 1u 30m",
        "* .end",
    ]
    return "\n".join(lines) + "\n"
