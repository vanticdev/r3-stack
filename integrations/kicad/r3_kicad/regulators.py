"""Converter sizing and verification from explicit requirements and vendor data."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# E96 series (1 %) for divider resistors.
E96 = [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30, 1.33, 1.37, 1.40, 1.43, 1.47, 1.50,
       1.54, 1.58, 1.62, 1.65, 1.69, 1.74, 1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
       2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09, 3.16, 3.24, 3.32, 3.40, 3.48, 3.57,
       3.65, 3.74, 3.83, 3.92, 4.02, 4.12, 4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
       5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32, 7.50, 7.68, 7.87, 8.06, 8.25, 8.45,
       8.66, 8.87, 9.09, 9.31, 9.53, 9.76]


E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]


def nearest_e12(value: float) -> float:
    """Nearest E12 value (capacitors)."""
    decade = 10 ** math.floor(math.log10(value))
    return min((m * decade for m in E12 + [10.0]), key=lambda candidate: abs(math.log(candidate / value)))


def nearest_e96(value: float) -> float:
    decade = 10 ** math.floor(math.log10(value))
    return min((m * decade for m in E96 + [10.0]), key=lambda candidate: abs(candidate - value))


@dataclass(frozen=True)
class BuckIC:
    """Regulator electrical data (datasheet, 25 C unless noted)."""

    part: str
    datasheet: str
    v_in_min: float
    v_in_max: float
    v_in_abs_max: float
    i_out_max: float
    v_ref: float
    v_ref_min: float
    v_ref_max: float
    f_sw: float
    t_off_min: float
    max_duty: float
    rds_on_high: float
    rds_on_low: float
    i_ocl_min: float              # valley current limit
    i_ocl_typ: float
    i_ocl_max: float
    uvlo_rising_max: float
    uvlo_falling_min: float
    t_ss: float
    theta_ja_jedec: float


TPS568230 = BuckIC(
    part="TPS568230RJER", datasheet="https://www.ti.com/lit/ds/symlink/tps568230.pdf",
    v_in_min=4.5, v_in_max=18.0, v_in_abs_max=22.0, i_out_max=8.0,
    v_ref=0.600, v_ref_min=0.592, v_ref_max=0.611, f_sw=600e3, t_off_min=190e-9, max_duty=0.90,
    rds_on_high=19.5e-3, rds_on_low=9.5e-3, i_ocl_min=8.1, i_ocl_typ=9.8, i_ocl_max=12.0,
    uvlo_rising_max=4.4, uvlo_falling_min=3.6, t_ss=1.3e-3, theta_ja_jedec=44.9,
)

TPS56528 = BuckIC(
    part="TPS56528DDAR", datasheet="https://www.ti.com/lit/ds/symlink/tps56528.pdf",
    v_in_min=4.5, v_in_max=18.0, v_in_abs_max=20.0, i_out_max=5.0,
    v_ref=0.600, v_ref_min=0.588, v_ref_max=0.612, f_sw=650e3, t_off_min=310e-9, max_duty=1 - 310e-9 * 650e3,
    rds_on_high=68e-3, rds_on_low=37e-3, i_ocl_min=5.5, i_ocl_typ=6.2, i_ocl_max=7.8,
    # VREG5 UVLO wake-up 3.45-4.05 V; VREG5 follows VIN minus ~0.3 V below 6 V (estimated).
    uvlo_rising_max=4.05 + 0.3, uvlo_falling_min=3.45 - 0.48, t_ss=1.0e-3, theta_ja_jedec=44.4,
)


@dataclass(frozen=True)
class Inductor:
    part: str
    inductance: float
    dcr_typ: float
    dcr_max: float
    i_sat: float                  # 30 % inductance drop (Coilcraft definition)
    i_rms: float                  # 20 C rise
    footprint: str
    manufacturer: str = "Coilcraft"


XAL7030_222 = Inductor("XAL7030-222MEB", 2.2e-6, 13.7e-3, 15.07e-3, 18.0, 10.0, "Inductor_SMD:L_Coilcraft_XAL7030-222")
XAL7030_332 = Inductor("XAL7030-332MEB", 3.3e-6, 19.5e-3, 21.45e-3, 12.3, 8.0, "Inductor_SMD:L_Coilcraft_XAL7030-332")


@dataclass(frozen=True)
class CapacitorBank:
    part: str
    capacitance: float
    voltage_rating: float
    dc_bias_retention: float      # fraction of nominal at the operating bias (estimated, Murata curves)
    count: int
    footprint: str
    esr: float = 3e-3             # per capacitor (estimated for X5R 1206 at f_sw)

    @property
    def effective(self) -> float:
        return self.capacitance * self.dc_bias_retention * self.count


@dataclass
class BuckRequirements:
    name: str
    v_out: float
    i_out: float                  # continuous design current
    v_in_min: float
    v_in_nom: float
    v_in_max: float
    v_in_ripple: float = 0.2      # V p-p at nominal input
    i_peak: float = 0.0           # required peak output; 0 = same as i_out


@dataclass
class BuckDesign:
    ic: BuckIC
    inductor: Inductor
    c_out: CapacitorBank
    c_in: CapacitorBank
    req: BuckRequirements
    r_fb_upper: float
    r_fb_lower: float
    c_ff: float
    bulk_out: float = 0.0         # extra hold-up capacitance on the output (effective, F)
    values: dict[str, float] = field(default_factory=dict)
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[tuple[str, bool, str]]:
        return [check for check in self.checks if not check[1]]


def ripple_current(v_in: float, v_out: float, inductance: float, f_sw: float) -> float:
    """Inductor ripple dI(p-p) = Vout (Vin - Vout) / (Vin L fsw)."""
    return max(v_out * (v_in - v_out) / (v_in * inductance * f_sw), 0.0)


def regulated_output(design: BuckDesign, v_in: float, i_out: float) -> float:
    """Output voltage including dropout.

    In regulation Vout = Vset. At the maximum duty the averaged switch node is
    Vsw = Dmax Vin - I (Dmax Rhs + (1 - Dmax) Rls), and Vout = Vsw - I DCR."""
    ic = design.ic
    v_set = design.values["v_out_nominal"]
    d = ic.max_duty
    v_drop = d * v_in - i_out * (d * ic.rds_on_high + (1 - d) * ic.rds_on_low + design.inductor.dcr_max)
    return min(v_set, v_drop)


def design_buck(ic: BuckIC, inductor: Inductor, c_out: CapacitorBank, c_in: CapacitorBank, req: BuckRequirements,
                r_fb_lower: float, c_ff: float, bulk_out: float = 0.0, t_transition: float = 10e-9,
                theta_ja_board: float = 55.0) -> BuckDesign:
    v: dict[str, float] = {}
    checks: list[tuple[str, bool, str]] = []

    # Feedback divider: Vout = Vref (1 + Rupper / Rlower)  (TPS568230 Eq. 4, TPS56528 Eq. 2).
    r_fb_upper = nearest_e96(r_fb_lower * (req.v_out / ic.v_ref - 1.0))
    v_out = ic.v_ref * (1 + r_fb_upper / r_fb_lower)
    v["v_out_nominal"] = v_out
    v["v_out_min"] = ic.v_ref_min * (1 + r_fb_upper * 0.99 / (r_fb_lower * 1.01))
    v["v_out_max"] = ic.v_ref_max * (1 + r_fb_upper * 1.01 / (r_fb_lower * 0.99))

    for label, v_in in (("min", req.v_in_min), ("nom", req.v_in_nom), ("max", req.v_in_max)):
        v[f"duty_{label}"] = min(v_out / v_in, 1.0)
        v[f"ripple_{label}"] = ripple_current(v_in, v_out, inductor.inductance, ic.f_sw)
    # Dropout: lowest input that still regulates at the design current with Dmax.
    i = req.i_out
    v["v_in_dropout"] = (v_out + i * (ic.max_duty * ic.rds_on_high + (1 - ic.max_duty) * ic.rds_on_low + inductor.dcr_max)) / ic.max_duty

    i_peak_out = req.i_peak or req.i_out
    v["i_l_rms"] = math.sqrt(req.i_out ** 2 + v["ripple_max"] ** 2 / 12.0)
    v["i_l_peak"] = i_peak_out + v["ripple_max"] / 2.0
    # Under current limit the valley sits at I_ocl; the peak adds one ripple (worst at Vin max).
    v["i_l_peak_at_ocl"] = ic.i_ocl_max + v["ripple_max"]
    # Output current available at current limit: valley + ripple/2 (minimum limit, minimum ripple).
    v["i_out_limit_min"] = ic.i_ocl_min + v["ripple_min"] / 2.0
    v["i_out_limit_max"] = ic.i_ocl_max + v["ripple_max"] / 2.0
    v["ripple_fraction_nom"] = v["ripple_nom"] / req.i_out
    checks.append(("inductor ripple 15-50 % of Iout at nominal Vin", 0.15 <= v["ripple_fraction_nom"] <= 0.5,
                   f"{100 * v['ripple_fraction_nom']:.0f} %"))
    checks.append(("I_L peak at design current below Isat", v["i_l_peak"] < inductor.i_sat,
                   f"{v['i_l_peak']:.2f} A < {inductor.i_sat} A"))
    checks.append(("I_L peak at maximum current limit below Isat", v["i_l_peak_at_ocl"] < inductor.i_sat,
                   f"{v['i_l_peak_at_ocl']:.2f} A < {inductor.i_sat} A"))
    checks.append(("I_L RMS below Irms (20 C rise)", v["i_l_rms"] < inductor.i_rms, f"{v['i_l_rms']:.2f} A < {inductor.i_rms} A"))
    checks.append(("current limit (min) covers the required peak output", v["i_out_limit_min"] >= i_peak_out,
                   f"{v['i_out_limit_min']:.2f} A >= {i_peak_out:g} A"))
    checks.append(("design current within IC rating", req.i_out <= ic.i_out_max, f"{req.i_out:g} A <= {ic.i_out_max:g} A"))

    c_out_eff = c_out.effective + bulk_out
    v["c_out_ceramic_effective"] = c_out.effective
    v["c_out_effective"] = c_out_eff
    # LC double pole fp = 1 / (2 pi sqrt(L C)) (TPS56528 Eq. 3; TPS568230 Eq. 1).
    v["f_lc"] = 1.0 / (2 * math.pi * math.sqrt(inductor.inductance * c_out_eff))
    v["f_lc_ceramic"] = 1.0 / (2 * math.pi * math.sqrt(inductor.inductance * c_out.effective))
    for label in ("nom", "max"):
        # Ceramic bank ripple: dV = dI / (8 fsw C) + dI ESR.
        v[f"v_out_ripple_{label}"] = v[f"ripple_{label}"] / (8 * ic.f_sw * c_out.effective) + v[f"ripple_{label}"] * c_out.esr / c_out.count

    # Input capacitance (TPS568230 Eq. 7): Cin(min) = Iout Vout / (dVin Vin fsw) at nominal input.
    v["c_in_min"] = req.i_out * v_out / (req.v_in_ripple * req.v_in_nom * ic.f_sw)
    v["c_in_effective"] = c_in.effective
    checks.append(("effective Cin above the input-ripple minimum", c_in.effective >= v["c_in_min"],
                   f"{c_in.effective * 1e6:.1f} uF >= {v['c_in_min'] * 1e6:.1f} uF"))
    v["v_in_ripple_nom"] = req.i_out * v_out / (c_in.effective * req.v_in_nom * ic.f_sw)
    # Input RMS ripple current (TPS568230 Eq. 8): I = Iout sqrt(D (1 - D)), worst at D = 0.5 if reachable.
    d_worst = min(max(0.5, v_out / req.v_in_max), v_out / max(req.v_in_min, v_out))
    v["i_cin_rms"] = req.i_out * math.sqrt(d_worst * (1 - d_worst))

    # Losses at nominal input and design current.
    duty = min(v_out / req.v_in_nom, ic.max_duty)
    i_sq = req.i_out ** 2 + v["ripple_nom"] ** 2 / 12.0
    v["p_hs"] = i_sq * duty * ic.rds_on_high
    v["p_ls"] = i_sq * (1 - duty) * ic.rds_on_low
    v["p_sw"] = 0.5 * req.v_in_nom * req.i_out * t_transition * ic.f_sw        # transition time estimated
    v["p_dead"] = 2 * 0.7 * req.i_out * 20e-9 * ic.f_sw                        # body diode, 20 ns dead time (estimated)
    v["p_ic"] = v["p_hs"] + v["p_ls"] + v["p_sw"] + v["p_dead"] + 0.03        # + gate drive (estimated)
    v["p_dcr"] = i_sq * inductor.dcr_typ
    p_out = v_out * req.i_out
    v["efficiency"] = p_out / (p_out + v["p_ic"] + v["p_dcr"])
    v["theta_ja_board"] = theta_ja_board
    v["t_junction_25c"] = 25.0 + v["p_ic"] * theta_ja_board
    checks.append(("junction below 125 C at the design current, 25 C ambient", v["t_junction_25c"] < 125,
                   f"{v['t_junction_25c']:.0f} C"))
    v["i_in_vin_min"] = p_out / (v["efficiency"] * req.v_in_min)
    v["i_in_vin_nom"] = p_out / (v["efficiency"] * req.v_in_nom)
    checks.append(("maximum input below the IC recommended maximum", req.v_in_max <= ic.v_in_max,
                   f"{req.v_in_max:g} V <= {ic.v_in_max:g} V"))

    design = BuckDesign(ic=ic, inductor=inductor, c_out=c_out, c_in=c_in, req=req, r_fb_upper=r_fb_upper,
                        r_fb_lower=r_fb_lower, c_ff=c_ff, bulk_out=bulk_out, values=v, checks=checks)
    return design


# Murata GRM31CR61E226ME15 (22 uF 25 V X5R 1206) keeps ~65 % at 7.4 V; GRM31CR61C226KE15 (22 uF 16 V) ~55 % at 5.1 V
# and ~45 % at 6 V (SimSurfing curves read approximately -> estimated).
C_IN_22U_25V = ("GRM31CR61E226ME15L", 22e-6, 25.0, 0.65, "Capacitor_SMD:C_1206_3216Metric")
C_OUT_22U_16V = ("GRM31CR61C226KE15L", 22e-6, 16.0, 0.55, "Capacitor_SMD:C_1206_3216Metric")
# Panasonic EEH-ZA1E221P hybrid polymer 220 uF 25 V, 8 x 10.2 mm, ESR 27 mOhm (datasheet): hold-up/bulk.
BULK_220U = ("EEH-ZA1E221P", 220e-6, 25.0, 1.0, "Capacitor_SMD:CP_Elec_8x10")






def hold_up_time(c_eff: float, v_start: float, v_end: float, power: float) -> float:
    """Time a capacitor bank alone holds a constant-power load: t = C (V0^2 - V1^2) / (2 P)."""
    return c_eff * (v_start ** 2 - v_end ** 2) / (2 * power)


def _si(value: float, unit: str) -> str:
    for scale, prefix in ((1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p")):
        if abs(value) >= scale * 0.9999:
            return f"{value / scale:.4g} {prefix}{unit}"
    return f"{value:.3g} {unit}"


def design_notes_markdown(design: BuckDesign, title: str, extra_rows: list[tuple[str, str, str]] | None = None) -> str:
    v, ic, ind, req = design.values, design.ic, design.inductor, design.req
    rows = [
        ("Feedback divider", "Vout = Vref (1 + Rupper/Rlower)",
         f"Rupper = {_si(design.r_fb_upper, 'Ohm')}, Rlower = {_si(design.r_fb_lower, 'Ohm')} -> {v['v_out_nominal']:.3f} V"),
        ("Vout tolerance", f"Vref {ic.v_ref_min * 1e3:.0f}-{ic.v_ref_max * 1e3:.0f} mV, 1 % resistors",
         f"{v['v_out_min']:.3f}-{v['v_out_max']:.3f} V"),
        ("Duty", "D = Vout/Vin", f"{v['duty_min']:.3f} @ {req.v_in_min:g} V, {v['duty_nom']:.3f} @ {req.v_in_nom:g} V, "
                                  f"{v['duty_max']:.3f} @ {req.v_in_max:g} V; Dmax {ic.max_duty:.3f}"),
        ("Dropout", "Vin = (Vout + I (Dmax Rhs + (1-Dmax) Rls + DCR)) / Dmax", f"{v['v_in_dropout']:.2f} V at {req.i_out:g} A"),
        ("Inductor", "datasheet table", f"{ind.manufacturer} {ind.part}: {_si(ind.inductance, 'H')}, DCR {ind.dcr_typ * 1e3:.1f}/"
                                         f"{ind.dcr_max * 1e3:.2f} mOhm, Isat {ind.i_sat:g} A, Irms {ind.i_rms:g} A"),
        ("Ripple current", "dI = Vout (Vin - Vout) / (Vin L fsw)",
         f"{v['ripple_min']:.2f} / {v['ripple_nom']:.2f} / {v['ripple_max']:.2f} A at {req.v_in_min:g} / {req.v_in_nom:g} / "
         f"{req.v_in_max:g} V ({100 * v['ripple_fraction_nom']:.0f} % at nominal)"),
        ("Inductor RMS / peak", "I_rms = sqrt(I^2 + dI^2/12); I_pk = I + dI/2", f"{v['i_l_rms']:.2f} A / {v['i_l_peak']:.2f} A"),
        ("Current limit", f"valley I_ocl {ic.i_ocl_min:g}/{ic.i_ocl_typ:g}/{ic.i_ocl_max:g} A",
         f"output at limit {v['i_out_limit_min']:.2f}-{v['i_out_limit_max']:.2f} A; inductor peak at limit {v['i_l_peak_at_ocl']:.2f} A"),
        ("Output capacitance", "ceramic X5R (DC-bias estimated) + bulk",
         f"{design.c_out.count} x {_si(design.c_out.capacitance, 'F')} -> {v['c_out_ceramic_effective'] * 1e6:.1f} uF"
         + (f" + {design.bulk_out * 1e6:.0f} uF polymer" if design.bulk_out else "")),
        ("LC double pole", "fp = 1 / (2 pi sqrt(L C))", f"{v['f_lc'] / 1e3:.1f} kHz (ceramic only {v['f_lc_ceramic'] / 1e3:.1f} kHz)"),
        ("Output ripple", "dV = dI / (8 fsw C) + dI ESR", f"{v['v_out_ripple_nom'] * 1e3:.1f} mV nominal, {v['v_out_ripple_max'] * 1e3:.1f} mV at Vin max"),
        ("Input capacitance", "Cin(min) = I Vout / (dVin Vin fsw)",
         f"min {v['c_in_min'] * 1e6:.1f} uF; fitted {design.c_in.count} x {_si(design.c_in.capacitance, 'F')} "
         f"{design.c_in.voltage_rating:g} V -> {v['c_in_effective'] * 1e6:.1f} uF, ripple {v['v_in_ripple_nom'] * 1e3:.0f} mV"),
        ("Cin RMS current", "I = Iout sqrt(D (1-D))", f"{v['i_cin_rms']:.2f} A"),
        ("Losses at nominal input", "I^2 R + 0.5 Vin I t_tr fsw + dead time + gate drive (estimated)",
         f"HS {v['p_hs'] * 1e3:.0f} mW, LS {v['p_ls'] * 1e3:.0f} mW, sw {v['p_sw'] * 1e3:.0f} mW, DCR {v['p_dcr'] * 1e3:.0f} mW "
         f"-> eta {100 * v['efficiency']:.1f} %"),
        ("Junction temperature", f"Tj = 25 C + P_ic theta (theta {v['theta_ja_board']:.0f} C/W on 2-layer, estimated; "
                                 f"JEDEC {ic.theta_ja_jedec} C/W)", f"{v['t_junction_25c']:.0f} C"),
        ("Input current", "Iin = Pout / (eta Vin)", f"{v['i_in_vin_nom']:.2f} A @ {req.v_in_nom:g} V, {v['i_in_vin_min']:.2f} A @ {req.v_in_min:g} V"),
    ] + (extra_rows or [])
    lines = [f"## {title}", "", f"Part: TI {ic.part} ({ic.datasheet}). Input {ic.v_in_min:g}-{ic.v_in_max:g} V "
             f"(abs. max {ic.v_in_abs_max:g} V), {ic.i_out_max:g} A, fsw {ic.f_sw / 1e3:.0f} kHz, RDS(on) "
             f"{ic.rds_on_high * 1e3:.1f}/{ic.rds_on_low * 1e3:.1f} mOhm.", "",
             "| Quantity | Equation / source | Value |", "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    lines += ["", "Design checks:", ""]
    lines += [f"- [{'x' if ok else ' '}] {name}: {detail}" for name, ok, detail in design.checks]
    return "\n".join(lines) + "\n"


def efficiency_curve(design: BuckDesign, v_in: float, loads: tuple[float, ...] = (0.1, 0.5, 1.0, 2.0, 3.0, 5.0),
                     t_transition: float = 10e-9, gate_and_quiescent: float = 0.03,
                     core_loss: float = 0.08) -> list[tuple[float, float]]:
    """Estimated efficiency vs load at ``v_in`` with the same loss terms as :func:`design_buck`
    (FCCM: the ripple current circulates even at light load) plus an estimated inductor core
    loss (Coilcraft XAL7030 at ~600 kHz and ~1.2 A p-p, read from the core-loss curves)."""
    ic, ind = design.ic, design.inductor
    v_out = design.values["v_out_nominal"]
    duty = min(v_out / v_in, ic.max_duty)
    ripple = ripple_current(v_in, v_out, ind.inductance, ic.f_sw)
    out = []
    for i in loads:
        i_sq = i ** 2 + ripple ** 2 / 12.0
        losses = (i_sq * (duty * ic.rds_on_high + (1 - duty) * ic.rds_on_low + ind.dcr_typ)
                  + 0.5 * v_in * max(i, ripple / 2) * t_transition * ic.f_sw + 2 * 0.7 * i * 20e-9 * ic.f_sw
                  + gate_and_quiescent + core_loss)
        out.append((i, round(v_out * i / (v_out * i + losses), 3)))
    return out


# ------------------------------------------------------------------ 4-switch buck-boost (servo rail)
@dataclass(frozen=True)
class BuckBoostIC:
    """Synchronous 4-switch buck-boost controller (external N-FETs), datasheet electrical data."""

    part: str
    datasheet: str
    v_in_min: float
    v_in_max: float
    v_in_abs_max: float
    v_ref: float
    v_ref_min: float
    v_ref_max: float
    rt_offset: float              # RT = (1/fsw - rt_offset) / rt_capacitance
    rt_capacitance: float
    v_en: float                   # EN/UVLO operating threshold
    i_en_standby: float           # EN/UVLO pull-up below the threshold (standby source current)
    i_en_hysteresis: float        # added hysteresis current above it
    i_ss: float
    v_cs_buck: tuple[float, float, float]    # buck valley current-limit threshold min/typ/max
    v_cs_boost: tuple[float, float, float]   # boost peak current-limit threshold min/typ/max
    a_cs: float                   # current-sense amplifier gain
    gm_slope: float
    gm_ea: float
    i_mode: float
    v_vcc: float
    i_q: float                    # operating supply current
    theta_ja_jedec: float


LM5175 = BuckBoostIC(
    part="LM5175PWPR", datasheet="https://www.ti.com/lit/ds/symlink/lm5175.pdf (SNVSA37A)",
    v_in_min=3.5, v_in_max=42.0, v_in_abs_max=60.0, v_ref=0.800, v_ref_min=0.788, v_ref_max=0.812,
    rt_offset=200e-9, rt_capacitance=37e-12, v_en=1.23, i_en_standby=1.5e-6, i_en_hysteresis=3.5e-6, i_ss=5.0e-6,
    v_cs_buck=(53.2e-3, 76e-3, 98e-3), v_cs_boost=(119e-3, 170e-3, 221e-3), a_cs=5.0, gm_slope=2e-6, gm_ea=1.27e-3,
    i_mode=20e-6, v_vcc=7.35, i_q=1.65e-3, theta_ja_jedec=33.5,
)


@dataclass(frozen=True)
class Mosfet:
    part: str
    v_ds: float
    rds_on: float                 # at the gate drive used here (VCC 7.35 V; at VIN < 7.6 V VCC follows VIN)
    rds_on_4v5: float
    q_g: float                    # total gate charge at ~7 V gate drive
    t_switch: float               # rise + fall time (estimated from Qgd and the driver resistance)
    footprint: str
    manufacturer: str = "Texas Instruments"


# TI CSD17577Q3A NexFET (SLPS433): 30 V, 4.0 mOhm at VGS 10 V, ~5.5 mOhm at 4.5 V, Qg 12 nC (10 V; ~8.5 nC at 7 V, est.).
CSD17577Q3A = Mosfet("CSD17577Q3AT", 30.0, 4.6e-3, 5.5e-3, 8.5e-9, 15e-9, "Package_SON:VSON-8_3.3x3.3mm_P0.65mm_NexFET")


@dataclass
class BuckBoostRequirements:
    name: str
    v_out: float
    i_out: float                  # continuous design current
    i_peak: float                 # short-term peak
    v_in_min: float
    v_in_nom: float
    v_in_max: float
    v_in_transient: float
    f_sw: float = 400e3
    uvlo_on: float = 4.5


@dataclass
class BuckBoostDesign:
    ic: BuckBoostIC
    fet: Mosfet
    inductor: Inductor
    c_out: CapacitorBank
    c_in: CapacitorBank
    req: BuckBoostRequirements
    r_fb_upper: float
    r_fb_lower: float
    r_sense: float
    r_t: float
    c_slope: float
    r_uv_upper: float
    r_uv_lower: float
    c_ss: float
    r_comp: float
    c_comp: float
    c_comp_hf: float
    r_mode: float
    topology: str = "buck_boost"
    values: dict[str, float] = field(default_factory=dict)
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[tuple[str, bool, str]]:
        return [check for check in self.checks if not check[1]]


def buck_boost_losses(d: BuckBoostDesign, v_in: float, i_out: float, core_loss: float = 0.06) -> dict[str, float]:
    """Loss terms (W) of the 4-switch converter at one point: buck mode (QH2 on) above V_out, boost mode (QH1 on)
    below (LM5175 Eq. 29-36); the sense resistor carries the low-side currents; gate drive Qg VCC fsw per switching
    pair; dead time 2 x 0.7 V x I_L x 20 ns x fsw (estimated); estimated core loss."""
    fet, ic, req = d.fet, d.ic, d.req
    v_out = d.values["v_out_nominal"]
    r_on = fet.rds_on if v_in >= 7.6 else fet.rds_on_4v5
    f = req.f_sw
    if v_in >= v_out:  # buck
        duty = v_out / v_in
        i_l = i_out
        ripple = ripple_current(v_in, v_out, d.inductor.inductance, f)
        i_sq = i_l ** 2 + ripple ** 2 / 12.0
        conduction = i_sq * (duty * r_on + (1 - duty) * r_on + r_on) + (1 - duty) * i_sq * d.r_sense
        switching = 0.5 * v_in * i_l * fet.t_switch * f
    else:  # boost
        duty = 1.0 - v_in / v_out
        i_l = i_out * v_out / (v_in * 0.95)
        ripple = v_in * (v_out - v_in) / (v_out * d.inductor.inductance * f)
        i_sq = i_l ** 2 + ripple ** 2 / 12.0
        conduction = i_sq * (r_on + duty * r_on + (1 - duty) * r_on) + duty * i_sq * d.r_sense
        switching = 0.5 * v_out * i_l * fet.t_switch * f
    dcr = i_sq * d.inductor.dcr_typ
    gate = 2 * fet.q_g * min(ic.v_vcc, v_in) * f
    dead = 2 * 0.7 * i_l * 20e-9 * f
    quiescent = ic.i_q * v_in
    total = conduction + switching + dcr + gate + dead + quiescent + core_loss
    return {"conduction": conduction, "switching": switching, "dcr": dcr, "gate": gate, "dead_time": dead,
            "quiescent": quiescent, "core": core_loss, "total": total, "i_l": i_l, "duty": duty}


def buck_boost_efficiency(d: BuckBoostDesign, v_in: float, i_out: float) -> float:
    v_out = d.values["v_out_nominal"]
    p_out = v_out * i_out
    return p_out / (p_out + buck_boost_losses(d, v_in, i_out)["total"])


def design_buck_boost(ic: BuckBoostIC, fet: Mosfet, inductor: Inductor, c_out: CapacitorBank, c_in: CapacitorBank,
                      req: BuckBoostRequirements, r_fb_lower: float, r_sense: float, c_slope: float, r_uv_upper: float,
                      t_ss: float, f_bw: float, theta_ja_board: float = 45.0) -> BuckBoostDesign:
    """LM5175 design procedure (SNVSA37A sec. 9.2.2), equation numbers from the datasheet."""
    v: dict[str, float] = {}
    checks: list[tuple[str, bool, str]] = []
    f = req.f_sw
    # Eq. 12: Rfb2 = (Vout - 0.8) / 0.8 * Rfb1.
    r_fb_upper = nearest_e96(r_fb_lower * (req.v_out / ic.v_ref - 1.0))
    v_out = ic.v_ref * (1 + r_fb_upper / r_fb_lower)
    v["v_out_nominal"] = v_out
    v["v_out_min"] = ic.v_ref_min * (1 + r_fb_upper * 0.99 / (r_fb_lower * 1.01))
    v["v_out_max"] = ic.v_ref_max * (1 + r_fb_upper * 1.01 / (r_fb_lower * 0.99))
    # Eq. 5: RT = (1/fsw - 200 ns) / 37 pF.
    r_t = nearest_e96((1.0 / f - ic.rt_offset) / ic.rt_capacitance)
    v["f_sw"] = 1.0 / (r_t * ic.rt_capacitance + ic.rt_offset)
    # Eq. 13/14: inductance for 40 % ripple at the extremes.
    v["l_buck_target"] = (req.v_in_max - v_out) * v_out / (0.4 * req.i_peak * f * req.v_in_max)
    v["l_boost_target"] = req.v_in_min ** 2 * (v_out - req.v_in_min) / (0.4 * req.i_peak * f * v_out ** 2)
    L = inductor.inductance
    v["ripple_buck_vin_max"] = ripple_current(req.v_in_max, v_out, L, f)
    v["ripple_boost_vin_min"] = req.v_in_min * (v_out - req.v_in_min) / (v_out * L * f)
    v["d_boost_max"] = 1.0 - req.v_in_min / v_out
    # Eq. 15-17: inductor current at the minimum input and peak load (90 % efficiency).
    v["i_l_max"] = v_out * req.i_peak / (0.9 * req.v_in_min)
    v["i_l_peak"] = v["i_l_max"] + v["ripple_boost_vin_min"] / 2.0
    v["i_l_sat_required"] = 1.2 * v["i_l_peak"] / 0.8
    checks.append(("inductor Isat above 1.2 I_L,peak / 0.8 (Eq. 17)", inductor.i_sat >= v["i_l_sat_required"],
                   f"{inductor.i_sat:g} A >= {v['i_l_sat_required']:.2f} A"))
    v["i_l_rms_cont"] = v_out * req.i_out / (0.9 * req.v_in_min)
    checks.append(("inductor Irms above the continuous current at minimum input", inductor.i_rms >= v["i_l_rms_cont"],
                   f"{inductor.i_rms:g} A >= {v['i_l_rms_cont']:.2f} A"))
    # Eq. 22/23: sense resistor, 70 % of the current-limit thresholds at the peak load.
    v["r_sense_buck_max"] = ic.v_cs_buck[1] * 0.7 / req.i_peak
    v["r_sense_boost_max"] = ic.v_cs_boost[1] * 0.7 / v["i_l_peak"]
    checks.append(("sense resistor leaves the peak load below 70 % of both current limits (Eq. 22-23)",
                   r_sense <= min(v["r_sense_buck_max"], v["r_sense_boost_max"]) + 1e-12,
                   f"{r_sense * 1e3:.1f} mOhm <= {min(v['r_sense_buck_max'], v['r_sense_boost_max']) * 1e3:.1f} mOhm"))
    # Output current at the limits: buck valley + ripple/2; boost peak - ripple/2 scaled by Vin eta / Vout.
    v["i_out_limit_buck_min"] = ic.v_cs_buck[0] / r_sense + ripple_current(req.v_in_nom, v_out, L, f) / 2.0
    v["i_out_limit_buck_max"] = ic.v_cs_buck[2] / r_sense + v["ripple_buck_vin_max"] / 2.0
    v["i_l_peak_limit_min"] = ic.v_cs_boost[0] / r_sense
    v["i_l_peak_limit_max"] = ic.v_cs_boost[2] / r_sense
    v["i_out_limit_boost_min"] = (v["i_l_peak_limit_min"] - v["ripple_boost_vin_min"] / 2.0) * req.v_in_min * 0.9 / v_out
    v["i_out_limit_min"] = min(v["i_out_limit_buck_min"], v["i_out_limit_boost_min"])
    v["i_out_limit_max"] = v["i_out_limit_buck_max"]
    checks.append(("minimum current limit covers the peak load in buck and boost mode", v["i_out_limit_min"] >= req.i_peak,
                   f"{v['i_out_limit_min']:.2f} A >= {req.i_peak:g} A"))
    # In a sustained overload the inductor may reach the maximum boost limit (18 A with 12 mOhm) above its 12.3 A
    # (30 % drop) saturation rating; hiccup mode (MODE = 93.1 kOhm) stops switching after 128 limited cycles (320 us).
    v["i_sat_margin_at_max_limit"] = inductor.i_sat / v["i_l_peak_limit_max"]
    # Eq. 24: sense resistor dissipation at the boost current limit, minimum input.
    v["p_r_sense_max"] = (ic.v_cs_boost[1] / r_sense) ** 2 * r_sense * (1 - req.v_in_min / v_out)
    # Eq. 25: dead-beat slope capacitor; a smaller capacitor (more slope) is chosen for the transition region.
    v["c_slope_deadbeat"] = ic.gm_slope * L / (r_sense * ic.a_cs)
    checks.append(("slope capacitor at or below the dead-beat value (Eq. 25)", c_slope <= v["c_slope_deadbeat"] * 1.05,
                   f"{c_slope * 1e12:.0f} pF <= {v['c_slope_deadbeat'] * 1e12:.0f} pF"))
    # Eq. 7/9: COMP range 0.3-3 V at the input extremes (no load buck, full load boost).
    d_buck = v_out / req.v_in_transient
    v["v_comp_buck_vin_transient"] = (1.6 + ic.a_cs * r_sense * v_out / (2 * L * f) * (1 - d_buck)
                                      - (ic.gm_slope * (req.v_in_transient - v_out) + 6e-6) * (1 - d_buck) / (c_slope * f))
    d_boost = v["d_boost_max"]
    v["v_comp_boost_vin_min"] = (1.6 + ic.a_cs * r_sense * (req.i_peak * v_out / req.v_in_min
                                                            - req.v_in_min * d_boost / (2 * L * f))
                                 + (ic.gm_slope * (v_out - req.v_in_min) + 5e-6) * d_boost / (c_slope * f))
    checks.append(("COMP above 0.3 V at no load and the 17 V transient input (Eq. 7)", v["v_comp_buck_vin_transient"] > 0.3,
                   f"{v['v_comp_buck_vin_transient']:.2f} V"))
    checks.append(("COMP below 3 V at peak load and minimum input (Eq. 9)", v["v_comp_boost_vin_min"] < 3.0,
                   f"{v['v_comp_boost_vin_min']:.2f} V"))
    # Eq. 1/2/26: EN/UVLO divider.
    r_uv_lower = nearest_e96(r_uv_upper * ic.v_en / (req.uvlo_on + ic.i_en_standby * r_uv_upper - ic.v_en))
    v["uvlo_rising"] = ic.v_en * (1 + r_uv_upper / r_uv_lower) - r_uv_upper * ic.i_en_standby
    v["uvlo_hysteresis"] = ic.i_en_hysteresis * r_uv_upper
    v["uvlo_falling"] = v["uvlo_rising"] - v["uvlo_hysteresis"]
    checks.append(("UVLO falling threshold above the controller's 3.5 V minimum VIN", v["uvlo_falling"] > ic.v_in_min,
                   f"{v['uvlo_falling']:.2f} V"))
    # Eq. 3: soft start.
    c_ss = nearest_e12(t_ss * ic.i_ss / ic.v_ref)
    v["t_ss"] = c_ss * ic.v_ref / ic.i_ss
    # Eq. 37-44: compensation for the boost mode (RHP zero), zero at 1.5 x the boost output pole.
    c_eff = c_out.effective
    v["c_out_effective"] = c_eff
    r_load = v_out / req.i_peak
    v["f_rhp"] = r_load * (1 - d_boost) ** 2 / (2 * math.pi * L)
    v["f_bw"] = min(f_bw, v["f_rhp"] / 3.0, f / 20.0)
    v["f_p_boost"] = 2.0 / (2 * math.pi * r_load * c_eff)
    r_comp = nearest_e96(2 * math.pi * v["f_bw"] / ic.gm_ea * (r_fb_lower + r_fb_upper) / r_fb_lower
                         * ic.a_cs * r_sense * c_eff / (1 - d_boost))
    v["f_zc"] = 1.5 * v["f_p_boost"] / 4.0  # well below crossover: the output pole moves down at light load
    c_comp = nearest_e12(1.0 / (2 * math.pi * v["f_zc"] * r_comp))
    c_comp_hf = nearest_e12(1.0 / (2 * math.pi * r_comp * f / 2.0))  # HF pole at fsw / 2
    checks.append(("crossover below one third of the RHP zero and fsw/20", v["f_bw"] <= min(v["f_rhp"] / 3, f / 20) + 1e-6,
                   f"{v['f_bw'] / 1e3:.1f} kHz (RHP zero {v['f_rhp'] / 1e3:.1f} kHz)"))
    # Output capacitor ripple in boost (Eq. 18-20) and input RMS in buck (Eq. 21).
    v["i_cout_rms"] = req.i_out * math.sqrt(max(v_out / req.v_in_min - 1, 0.0))
    v["v_ripple_boost"] = req.i_out * (1 - req.v_in_min / v_out) / (c_eff * f) + req.i_out * v_out / req.v_in_min * c_out.esr / c_out.count
    v["i_cin_rms"] = req.i_out * 0.5
    checks.append(("FET VDS rating above the 17 V TVS clamp", fet.v_ds >= req.v_in_transient * 1.25,
                   f"{fet.v_ds:g} V >= {req.v_in_transient * 1.25:.1f} V"))
    design = BuckBoostDesign(ic=ic, fet=fet, inductor=inductor, c_out=c_out, c_in=c_in, req=req, r_fb_upper=r_fb_upper,
                             r_fb_lower=r_fb_lower, r_sense=r_sense, r_t=r_t, c_slope=c_slope, r_uv_upper=r_uv_upper,
                             r_uv_lower=r_uv_lower, c_ss=c_ss, r_comp=r_comp, c_comp=c_comp, c_comp_hf=c_comp_hf,
                             r_mode=93.1e3, values=v, checks=checks)
    # Losses, efficiency, junction temperatures.
    for label, v_in in (("min", req.v_in_min), ("nom", req.v_in_nom), ("max", req.v_in_max)):
        loss = buck_boost_losses(design, v_in, req.i_out)
        v[f"loss_{label}"] = loss["total"]
        v[f"efficiency_{label}"] = v_out * req.i_out / (v_out * req.i_out + loss["total"])
        v[f"i_in_vin_{label}"] = v_out * req.i_out / (v[f"efficiency_{label}"] * v_in)
    v["efficiency"] = v["efficiency_nom"]
    v["theta_ja_board"] = theta_ja_board
    worst = buck_boost_losses(design, req.v_in_min, req.i_out)
    per_fet = (worst["conduction"] - worst["i_l"] ** 2 * design.r_sense * worst["duty"]) / 2.0 + worst["switching"]
    v["t_fet_25c"] = 25.0 + per_fet * 60.0  # VSON 3.3 x 3.3 on 1 oz with a small pour: ~60 C/W (estimated)
    checks.append(("hottest FET below 125 C at the continuous load, minimum input, 25 C", v["t_fet_25c"] < 125,
                   f"{v['t_fet_25c']:.0f} C"))
    v["i_out_max"] = req.i_out
    return design




def buck_boost_efficiency_curve(design: BuckBoostDesign, v_in: float,
                                loads: tuple[float, ...] = (0.1, 0.5, 1.0, 2.0, 3.0, 4.0)) -> list[tuple[float, float]]:
    return [(i, round(buck_boost_efficiency(design, v_in, i), 3)) for i in loads]


def buck_boost_notes_markdown(design: BuckBoostDesign, title: str) -> str:
    v, ic, req, ind, fet = design.values, design.ic, design.req, design.inductor, design.fet
    rows = [
        ("Feedback divider", "Vout = 0.8 V (1 + Rfb2/Rfb1) (Eq. 12)",
         f"Rfb2 = {_si(design.r_fb_upper, 'Ohm')}, Rfb1 = {_si(design.r_fb_lower, 'Ohm')} -> {v['v_out_nominal']:.3f} V "
         f"({v['v_out_min']:.3f}-{v['v_out_max']:.3f} V)"),
        ("Switching frequency", "RT = (1/fsw - 200 ns) / 37 pF (Eq. 5)", f"RT = {_si(design.r_t, 'Ohm')} -> {v['f_sw'] / 1e3:.0f} kHz"),
        ("Inductor", "Eq. 13/14: L for 40 % ripple", f"targets {v['l_buck_target'] * 1e6:.2f} uH (buck) / {v['l_boost_target'] * 1e6:.2f} uH "
         f"(boost); fitted {ind.manufacturer} {ind.part} {_si(ind.inductance, 'H')}, Isat {ind.i_sat:g} A, Irms {ind.i_rms:g} A"),
        ("Ripple", "buck Vout (Vin-Vout)/(Vin L f); boost Vin (Vout-Vin)/(Vout L f)",
         f"{v['ripple_buck_vin_max']:.2f} A at {req.v_in_max:g} V, {v['ripple_boost_vin_min']:.2f} A at {req.v_in_min:g} V"),
        ("Inductor current", "Eq. 15-17", f"I_L,max {v['i_l_max']:.2f} A, peak {v['i_l_peak']:.2f} A at {req.i_peak:g} A out, "
         f"Isat required {v['i_l_sat_required']:.2f} A"),
        ("Sense resistor", "Eq. 22/23: 70 % of 76 mV buck valley / 170 mV boost peak",
         f"{design.r_sense * 1e3:.0f} mOhm (max {v['r_sense_buck_max'] * 1e3:.1f} / {v['r_sense_boost_max'] * 1e3:.1f} mOhm); "
         f"dissipation at the boost limit {v['p_r_sense_max']:.2f} W (Eq. 24)"),
        ("Current limit", "buck valley 53-98 mV, boost peak 119-221 mV",
         f"output {v['i_out_limit_min']:.2f}-{v['i_out_limit_max']:.2f} A; inductor peak limit {v['i_l_peak_limit_min']:.1f}-"
         f"{v['i_l_peak_limit_max']:.1f} A; hiccup after 128 cycles (MODE 93.1 kOhm: CCM, hiccup)"),
        ("Slope", "C_slope = gm_slope L / (Rsense A_cs) (Eq. 25)",
         f"dead-beat {v['c_slope_deadbeat'] * 1e12:.0f} pF; fitted {design.c_slope * 1e12:.0f} pF (more slope for the transition region)"),
        ("COMP range", "Eq. 7 / Eq. 9", f"{v['v_comp_buck_vin_transient']:.2f} V at no load, {req.v_in_transient:g} V in; "
         f"{v['v_comp_boost_vin_min']:.2f} V at {req.i_peak:g} A, {req.v_in_min:g} V in (window 0.3-3 V)"),
        ("UVLO", "Vuv = 1.23 V (1 + Ruv2/Ruv1) - 1.5 uA Ruv2; hysteresis 3.5 uA Ruv2 (Eq. 1/2)",
         f"Ruv2 {_si(design.r_uv_upper, 'Ohm')}, Ruv1 {_si(design.r_uv_lower, 'Ohm')}: on {v['uvlo_rising']:.2f} V, off {v['uvlo_falling']:.2f} V"),
        ("Soft start", "tss = Css 0.8 V / 5 uA (Eq. 3)", f"Css {_si(design.c_ss, 'F')} -> {v['t_ss'] * 1e3:.1f} ms"),
        ("Compensation", "Eq. 39, 43, 44", f"RHP zero {v['f_rhp'] / 1e3:.1f} kHz, crossover {v['f_bw'] / 1e3:.1f} kHz; Rc1 "
         f"{_si(design.r_comp, 'Ohm')}, Cc1 {_si(design.c_comp, 'F')}, Cc2 {_si(design.c_comp_hf, 'F')}"),
        ("Output capacitance", "ceramic X5R (DC bias estimated)", f"{design.c_out.count} x 22 uF -> {v['c_out_effective'] * 1e6:.1f} uF; "
         f"boost ripple {v['v_ripple_boost'] * 1e3:.0f} mV, RMS {v['i_cout_rms']:.2f} A (Eq. 18-20)"),
        ("FETs", "Eq. 29-36 + gate drive + dead time (estimated)", f"4 x {fet.part}, {fet.v_ds:g} V, {fet.rds_on * 1e3:.1f} mOhm; "
         f"hottest {v['t_fet_25c']:.0f} C at {req.i_out:g} A, {req.v_in_min:g} V in"),
        ("Efficiency at 3 A", "losses above (estimated)", f"{100 * v['efficiency_min']:.1f} / {100 * v['efficiency_nom']:.1f} / "
         f"{100 * v['efficiency_max']:.1f} % at {req.v_in_min:g} / {req.v_in_nom:g} / {req.v_in_max:g} V"),
        ("Input current at 3 A", "Iin = Pout / (eta Vin)", f"{v['i_in_vin_min']:.2f} A at {req.v_in_min:g} V, {v['i_in_vin_nom']:.2f} A at "
         f"{req.v_in_nom:g} V"),
    ]
    lines = [f"## {title}", "", f"Part: TI {ic.part} ({ic.datasheet}), 4-switch buck-boost controller, VIN "
             f"{ic.v_in_min:g}-{ic.v_in_max:g} V. No dropout: the output stays at {v['v_out_nominal']:.2f} V from the 17 V "
             f"transient down to the {v['uvlo_falling']:.2f} V UVLO.", "",
             "| Quantity | Equation / source | Value |", "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    lines += ["", "Design checks:", ""]
    lines += [f"- [{'x' if ok else ' '}] {name}: {detail}" for name, ok, detail in design.checks]
    return "\n".join(lines) + "\n"
