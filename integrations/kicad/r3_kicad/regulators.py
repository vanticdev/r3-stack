"""Synchronous buck designs for the carrier HAT regulators.

* ``U2`` TI TPS568230 (SLVSEY5C, D-CAP3, 8 A): VBAT -> 5.1 V / 5 A for the Pi.
* ``U3`` TI TPS56528 (SLVSBV3B, D-CAP2, 5 A): VBAT -> 6.0 V servo rail, the
  cycle-by-cycle valley current limit (5.5-7.8 A) is the servo stall limit.

Equation numbers refer to the respective datasheet. Values tagged ``datasheet``
come from the cited tables; capacitor DC-bias retention, switching transition
time and the 2-layer board thermal resistance are engineering estimates.
"""

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
    v_in_min: float = 6.0
    v_in_nom: float = 7.4
    v_in_max: float = 17.0
    v_in_ripple: float = 0.2      # V p-p at nominal input
    i_peak: float = 0.0           # required peak output (servo stall); 0 = same as i_out


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


def pi_rail() -> BuckDesign:
    """5.1 V / 5 A for the Pi 5. TPS568230 Table 3 (5 V, 600 kHz): Rlower 30 k, L 2.2 uH,
    Cout 66-500 uF, Cff 47-330 pF. VIN UVLO 4.2 V typ / 4.4 V max rising, 3.8 V falling."""
    req = BuckRequirements("5V", v_out=5.1, i_out=5.0)
    c_out = CapacitorBank(*C_OUT_22U_16V[:4], count=4, footprint=C_OUT_22U_16V[4])
    # TI recommends 40 uF of X5R/X7R on VIN (sec. 8.2.2.1.4): 3 x 22 uF 25 V keeps ~43 uF at 7.4 V.
    c_in = CapacitorBank(*C_IN_22U_25V[:4], count=3, footprint=C_IN_22U_25V[4])
    return design_buck(TPS568230, XAL7030_222, c_out, c_in, req, r_fb_lower=30.1e3, c_ff=100e-12, bulk_out=220e-6)


def servo_rail() -> BuckDesign:
    """6.0 V servo rail. TPS56528 Table 2 (6.5 V row): R2 49.9 k, L 2.2-4.7 uH (3.3 typ), Cout 20-68 uF.
    The DS3218 servo stalls at 2.5 A (catalog); the rail must deliver >= 5 A peak and the 5.5 A minimum
    valley limit bounds a shorted or stalled servo."""
    req = BuckRequirements("SERVO_6V", v_out=6.0, i_out=2.5, i_peak=5.0)
    c_out = CapacitorBank("GRM31CR61C226KE15L", 22e-6, 16.0, 0.45, count=3, footprint="Capacitor_SMD:C_1206_3216Metric")
    c_in = CapacitorBank(*C_IN_22U_25V[:4], count=2, footprint=C_IN_22U_25V[4])
    return design_buck(TPS56528, XAL7030_332, c_out, c_in, req, r_fb_lower=49.9e3, c_ff=0.0, theta_ja_board=55.0)


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
