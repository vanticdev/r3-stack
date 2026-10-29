"""Unit tests of electrical behavior models in minimal circuits."""

import math

import numpy as np
import pytest
from r3_catalog import default_catalog

from r3_sim.electrical.mna import Circuit, VoltageSource
from r3_sim.electrical.models.base import BuildContext, SupplyLoad, constant_current
from r3_sim.electrical.models.battery import BatteryLipo
from r3_sim.electrical.models.fuse import FuseBlade
from r3_sim.electrical.models.motor import DcBrushedMotor


def _model(cls, type_id: str, cid: str, reference: str):
    catalog = default_catalog()
    ctype = catalog.component(type_id)
    circuit = Circuit()
    circuit.set_reference(reference)
    model = cls(cid, ctype, dict(ctype.behavior.params), BuildContext(circuit, None, 25.0))
    model.build(circuit)
    return circuit, model


def _step(circuit, models, dt, x):
    for m in models:
        m.begin_step(dt, {}, 0.0)
    x, info = circuit.solve(x)
    assert info.converged
    for m in models:
        m.end_step(dt, x, 0.0)
    return x


@pytest.mark.parametrize("c_rate", [1.0, 2.0])
def test_battery_discharge_delivers_rated_capacity(c_rate):
    circuit, battery = _model(BatteryLipo, "lipo-2s-5000-25c", "bat", "C:bat.NEG")
    current = c_rate * 5.0
    fn, expr = constant_current(current, 1.0)
    circuit.add(SupplyLoad("load", battery.pos.node, -1, fn, expr))
    dt, delivered, x = 1.0, 0.0, None
    samples = {}
    while battery.s_soc > 0.0:
        x = _step(circuit, [battery], dt, x)
        delivered += battery.last_current * dt
        samples.setdefault(round(battery.s_soc, 2), (x[battery.pos.node], battery.s_soc, battery.r0, battery.r1))
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
    circuit, motor = _model(DcBrushedMotor, "motor-540-brushed", "motor", "C:motor.M_NEG")
    supply_branch = circuit.branch("src")
    circuit.add(VoltageSource("src", motor.pos.node, -1, supply_branch, emf=7.4, resistance=0.0))
    motor.begin_step(None, {"omega": 0.0}, 0.0)
    x, _ = circuit.solve()
    out = motor.outputs(x)
    expected = (7.4 - 0.2) / 0.11  # (V - V_brush) / R
    print(f"stall current {out['current']:.2f} A (V/R = {7.4 / 0.11:.2f} A, (V-Vb)/R = {expected:.2f} A), torque {out['torque']:.4f} N m")
    assert out["current"] == pytest.approx(expected, rel=1e-3)
    assert out["current"] == pytest.approx(7.4 / 0.11, rel=0.04)
    assert out["torque"] == pytest.approx(60 / (2 * math.pi * 2100) * out["current"], rel=1e-9)


def test_motor_inductive_rise_backward_euler():
    circuit, motor = _model(DcBrushedMotor, "motor-540-brushed", "motor", "C:motor.M_NEG")
    supply_branch = circuit.branch("src")
    circuit.add(VoltageSource("src", motor.pos.node, -1, supply_branch, emf=7.4, resistance=0.0))
    x, currents = None, []
    for _ in range(10):
        motor.begin_step(1e-4, {"omega": 0.0}, 0.0)
        x, _ = circuit.solve(x)
        motor.end_step(1e-4, x, 0.0)
        currents.append(motor.s_current)
    steady = (7.4 - 0.2) / 0.11
    tau = 60e-6 / 0.11
    # Backward Euler of L di/dt = V - R i: i1 = (V - Vb) / (R + L/dt) (brush drop saturated).
    assert currents[0] == pytest.approx((7.4 - 0.2) / (0.11 + 60e-6 / 1e-4), rel=0.01)
    assert currents[-1] == pytest.approx(steady * (1 - math.exp(-1e-3 / tau)), rel=0.05)
    assert all(np.diff(currents) > 0)


def test_fuse_blows_on_overcurrent_not_on_rated_load():
    circuit, fuse = _model(FuseBlade, "fuse-atc-10a", "fuse", "C:fuse.B")

    class _Branch:
        a, b = fuse.pins["A"].node, -1
        index = circuit.resistor(fuse.pins["A"].node, -1, 0.007, "fuse:A-B")

    fuse.attach(_Branch, circuit)
    source = circuit.branch("src")
    src = circuit.add(VoltageSource("src", fuse.pins["A"].node, -1, source, emf=0.0, resistance=0.0))
    for amps, expect_blow in ((10.0, False), (20.0, True), (100.0, True)):
        fuse.reset()
        src.emf = amps * 0.007
        t, dt, x = 0.0, 1e-3, None
        while t < 5.0 and not fuse.s_blown:
            fuse.begin_step(dt, {}, t)
            x, _ = circuit.solve(x)
            fuse.end_step(dt, x, t)
            t += dt
        analytic = fuse.time_to_blow(amps)
        print(f"{amps:5.1f} A: blown={fuse.s_blown} after {t:.3f} s (analytic {analytic:.3f} s)")
        assert fuse.s_blown is expect_blow
        if expect_blow:
            assert t == pytest.approx(analytic, abs=2e-3)
    assert fuse.time_to_blow(100.0) == pytest.approx(120.0 / 100.0 ** 2, rel=0.02)  # adiabatic I2t limit
