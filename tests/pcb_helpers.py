"""Independent converter-sizing examples for mathematical verification, without a board layout."""
from r3_kicad.regulators import (
    BuckRequirements, BuckBoostRequirements, CapacitorBank, TPS568230, LM5175,
    CSD17577Q3A, XAL7030_222, XAL7030_332, design_buck, design_buck_boost,
)


def buck_fixture():
    req = BuckRequirements("logic", v_out=3.3, i_out=4, v_in_min=8, v_in_nom=12, v_in_max=16)
    output = CapacitorBank("test-output", 100e-6, 25, .8, count=2, footprint="Capacitor_SMD:C_1206_3216Metric")
    supply = CapacitorBank("test-input", 47e-6, 25, .8, count=2, footprint="Capacitor_SMD:C_1206_3216Metric")
    return design_buck(TPS568230, XAL7030_222, output, supply, req, r_fb_lower=30.1e3, c_ff=100e-12)


def boost_fixture():
    req = BuckBoostRequirements("instrument", v_out=9, i_out=1, i_peak=1.5,
        v_in_min=6, v_in_nom=12, v_in_max=16, v_in_transient=17, uvlo_on=5.5)
    output = CapacitorBank("test-output", 100e-6, 25, .8, count=2, footprint="Capacitor_SMD:C_1206_3216Metric")
    supply = CapacitorBank("test-input", 47e-6, 25, .8, count=2, footprint="Capacitor_SMD:C_1206_3216Metric")
    return design_buck_boost(LM5175, CSD17577Q3A, XAL7030_332, output, supply, req,
        r_fb_lower=20e3, r_sense=.02, c_slope=47e-12, r_uv_upper=100e3, t_ss=.01, f_bw=8e3)
