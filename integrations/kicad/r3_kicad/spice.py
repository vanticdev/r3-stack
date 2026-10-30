"""Averaged converter subcircuits and TVS vendor curves for ngspice."""

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


def power_subcircuit(designs: dict[str, BuckDesign | BuckBoostDesign], name: str = "R3_POWER") -> str:
    """Export explicitly declared converters, with one output port per mapping entry.

    VIN and ground are shared. Input protection, contacts, monitors and hold-up
    components must be declared by the project's electrical model.
    """
    outputs = [f"OUT{index}" for index in range(len(designs))]
    lines = [f"* {name}: averaged converter models", f".subckt {name} VIN 0 {' '.join(outputs)}"]
    for index, (key, design) in enumerate(designs.items()):
        tag, output = f"REG{index}", outputs[index]
        lines.append(f"* {output}: {key}")
        lines += [f"CIN_{tag} VIN 0 {design.c_in.effective:.6g}"]
        if isinstance(design, BuckBoostDesign):
            lines += _buck_boost(tag, design, "VIN", output)
        else:
            lines += _buck(tag, design, "VIN", output, design.ic.uvlo_rising_max,
                           design.ic.uvlo_falling_min, design.values["i_out_limit_min"])
        if isinstance(design, BuckDesign) and design.bulk_out:
            lines.append(f"CHOLD_{tag} {output} 0 {design.bulk_out:.6g}")
    return "\n".join([*lines, f".ends {name}", ""])
