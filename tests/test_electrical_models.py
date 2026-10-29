"""Unit tests of electrical behavior models (ngspice subcircuits) in minimal circuits."""

import math

import numpy as np
import pytest
from r3_catalog import default_catalog

from r3_sim.electrical.bench import Bench
from r3_sim.electrical.models.base import BuildContext
from r3_sim.electrical.models.battery import BatteryLipo
from r3_sim.electrical.models.fuse import FuseBlade
from r3_sim.electrical.models.motor import DcBrushedMotor
from r3_sim.electrical.ngspice import find_library

pytestmark = pytest.mark.skipif(find_library() is None, reason="libngspice not installed")


def _model(cls, type_id: str, cid: str, reference: str):
    catalog = default_catalog()
    ctype = catalog.component(type_id)
    return cls(cid, ctype, dict(ctype.behavior.params), BuildContext(None, 25.0, reference))


@pytest.mark.parametrize("c_rate", [1.0, 2.0])
def test_battery_discharge_delivers_rated_capacity(c_rate):
    battery = _model(BatteryLipo, "lipo-2s-5000-25c", "bat", "C:bat.NEG")
    current = c_rate * 5.0
    # Constant-current load (resistive collapse below 1 V keeps the deck well posed after empty).
    bench = Bench([battery], [f"bload {battery.pos.node} 0 i = (v({battery.pos.node}) >= 1 ? {current} : {current}*v({battery.pos.node}))"],
                  max_step=1.0)
    dt, delivered = 1.0, 0.0
    samples = {}
    try:
        while battery.s_soc > 0.0:
            sol = bench.step(dt)
            delivered += battery.last_current * dt
            samples.setdefault(round(battery.s_soc, 2), (sol.v(battery.pos.node), battery.s_soc, battery.r0, battery.r1))
    finally:
        bench.close()
    capacity_ah = delivered / 3600.0
    v_mid, soc_mid, r0, r1 = samples[0.5]
    print(f"{c_rate:.0f}C: delivered {capacity_ah:.4f} Ah, V(SOC 0.5) = {v_mid:.4f} V, T = {battery.s_temperature:.1f} C")
    assert capacity_ah == pytest.approx(5.0, rel=0.002)
    # Mid-curve terminal voltage = 2 OCV(SOC) - I (R0 + R1) once the RC pair has settled (tau = 5 s);
    # R0, R1 at the pack's self-heated temperature (Arrhenius), nominal 2 x 4 mohm + 2 x 2.5 mohm at 25 C.
    expected = 2 * float(np.interp(soc_mid, [0.5, 0.6], [3.82, 3.87])) - current * (r0 + r1)
    assert v_mid == pytest.approx(expected, abs=0.002)
    assert r0 + r1 == pytest.approx(0.013, rel=0.15)
    assert battery.s_temperature > 25.0  # I^2 R heating


def test_motor_stall_current_is_v_over_r():
    motor = _model(DcBrushedMotor, "motor-540-brushed", "motor", "C:motor.M_NEG")
    bench = Bench([motor], [f"vsrc {motor.pos.node} 0 dc 7.4"])
    try:
        sol = bench.operating_point({"motor": {"omega": 0.0}})
    finally:
        bench.close()
    out = motor.outputs(sol)
    expected = (7.4 - 0.2) / 0.11  # (V - V_brush) / R
    print(f"stall current {out['current']:.2f} A (V/R = {7.4 / 0.11:.2f} A, (V-Vb)/R = {expected:.2f} A), torque {out['torque']:.4f} N m")
    assert out["current"] == pytest.approx(expected, rel=1e-3)
    assert out["current"] == pytest.approx(7.4 / 0.11, rel=0.04)
    assert out["torque"] == pytest.approx(60 / (2 * math.pi * 2100) * out["current"], rel=1e-9)


def test_motor_inductive_rise_matches_rl_exponential():
    motor = _model(DcBrushedMotor, "motor-540-brushed", "motor", "C:motor.M_NEG")
    bench = Bench([motor], [f"vsrc {motor.pos.node} 0 pwl(0 0 1n 7.4)"], max_step=1e-4)
    currents = []
    try:
        for _ in range(10):
            bench.step(1e-4, {"motor": {"omega": 0.0}})
            currents.append(motor.s_current)
    finally:
        bench.close()
    steady = (7.4 - 0.2) / 0.11
    tau = 60e-6 / 0.11
    # L di/dt = V - V_brush - R i: i(t) = (V - V_b) / R (1 - exp(-t / tau)) once the brush drop has saturated (> 0.3 A).
    for k, current in enumerate(currents, start=1):
        analytic = steady * (1 - math.exp(-k * 1e-4 / tau))
        assert current == pytest.approx(analytic, rel=0.01), (k, current, analytic)
    assert all(np.diff(currents) > 0)


def test_fuse_blows_on_overcurrent_not_on_rated_load():
    fuse = _model(FuseBlade, "fuse-atc-10a", "fuse", "C:fuse.B")

    class _Branch:
        node_a, node_b = "C:fuse.A", "C:fuse.B"

    fuse.attach(_Branch)
    for amps, expect_blow in ((10.0, False), (20.0, True), (100.0, True)):
        fuse.reset()
        bench = Bench([fuse], [f"vsrc {fuse.pins['A'].node} 0 dc {amps * 0.007}"])
        t, dt = 0.0, 1e-3
        try:
            while t < 5.0 and not fuse.s_blown:
                bench.step(dt)
                t += dt
        finally:
            bench.close()
        analytic = fuse.time_to_blow(amps)
        print(f"{amps:5.1f} A: blown={fuse.s_blown} after {t:.3f} s (analytic {analytic:.3f} s)")
        assert fuse.s_blown is expect_blow
        if expect_blow:
            assert t == pytest.approx(analytic, abs=2e-3)
    assert fuse.time_to_blow(100.0) == pytest.approx(120.0 / 100.0 ** 2, rel=0.02)  # adiabatic I2t limit
