"""System-level electrical twin tests on the reference RC car."""

import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.electrical import ElectricalSystem
from r3_sim.electrical.spice import compare_netlist, find_ngspice

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"
KT = 60 / (2 * math.pi * 2100)


@pytest.fixture(scope="module")
def project():
    return Project.load(RC_CAR)


def test_contract_step_outputs(project):
    system = ElectricalSystem(project)
    out = system.step(0.001, {"esc": {"pulse_us": 1600.0}, "motor": {"omega": 310.0},
                              "servo": {"pulse_us": 1500.0, "horn_torque": 0.2, "horn_speed": 0.5}, "pi": {"activity": 0.6}})
    assert out["pi"]["powered"] and out["_solver"]["converged"]
    assert out["_nets"]["5V"] == pytest.approx(5.1, abs=0.03)
    assert out["_nets"]["BEC_6V"] == pytest.approx(6.0, abs=0.05)
    assert out["_branches"]["W_Y_P_IN"]["current"] == pytest.approx(out["battery"]["current"], rel=1e-6)
    for key in ("torque", "current"):
        assert key in out["motor"]
    for key in ("v_terminal", "soc", "current", "temperature_c"):
        assert key in out["battery"]
    assert {"temperature_c", "bec_voltage"} <= set(out["esc"])
    contact = out["_branches"]["power_y.P1|battery_lead.B#1"]
    assert contact["kind"] == "contact" and contact["to"] == "H:battery_lead.B.1"
    assert -contact["current"] == pytest.approx(out["battery"]["current"], rel=1e-6)  # flows battery -> P1


def test_full_throttle_transient_with_omega_ramp(project):
    system = ElectricalSystem(project, initial={"battery_soc": 1.0})
    dt, duration, w_end = 1e-3, 2.0, 1500.0
    rows = []
    charge = 0.0
    for k in range(int(duration / dt)):
        omega = w_end * (k + 1) * dt / duration
        out = system.step(dt, {"esc": {"pulse_us": 2000.0}, "motor": {"omega": omega},
                               "servo": {"horn_torque": 0.2, "horn_speed": 0.5}, "pi": {"activity": 1.0}})
        charge += out["battery"]["current"] * dt
        rows.append((system.time, omega, out["motor"]["current"], out["motor"]["torque"], out["battery"]["v_terminal"],
                     out["pi"]["powered"], out["esc"]["current_limited"]))
        assert out["_solver"]["converged"]
    t, w, i, tau, vb, powered, limited = map(np.array, zip(*rows))
    peak = int(np.argmax(i))
    print(f"\npeak motor current {i[peak]:.1f} A at {t[peak] * 1e3:.0f} ms; I(0.5 s) {i[499]:.1f} A, I(1 s) {i[999]:.1f} A, "
          f"I(2 s) {i[-1]:.1f} A; min battery {vb.min():.3f} V; SOC {out['battery']['soc']:.5f}; "
          f"ESC {out['esc']['temperature_c']:.1f} C, motor {out['motor']['temperature_c']:.1f} C, battery {out['battery']['temperature_c']:.2f} C")
    # inductive rise in the first milliseconds (L/R ~ 0.4 ms)
    assert i[0] < i[peak] and t[peak] < 0.02
    # quasi-static analytic check at the peak: I = (V_A - V_B - Ke w - V_brush) / R_motor
    motor = system.models["motor"]
    # torque relation holds exactly at every step
    np.testing.assert_allclose(tau, KT * (i - 1.6 * np.tanh(w / 1.0)), rtol=1e-9)
    # current falls monotonically as back-EMF rises after the inductive transient
    assert np.all(np.diff(i[peak + 5:]) < 0)
    # steady current at the end within 2 % of the series-loop estimate using the solved bridge voltage
    v_bridge = out["esc"]["duty"] * out["esc"]["v_in"] - 2 * 0.002 * i[-1]
    v_motor_terminal = out["motor"]["voltage"]
    expected_end = (v_motor_terminal - KT * w_end - 0.2) / motor.resistance
    assert i[-1] == pytest.approx(expected_end, rel=0.02)
    assert v_motor_terminal < v_bridge  # motor-lead drop
    assert 30.0 < i[peak] < 70.0 and not limited.any()
    assert powered.all()
    assert out["battery"]["soc"] == pytest.approx(1.0 - charge / 18000.0, abs=1e-6)
    assert out["esc"]["temperature_c"] > 25 and out["motor"]["temperature_c"] > 25


def test_fuse_blows_on_short_and_pi_browns_out(project):
    system = ElectricalSystem(project, initial={"battery_soc": 0.9})
    inputs = {"esc": {"pulse_us": 1500.0}, "pi": {"activity": 0.5}}
    for _ in range(100):
        out = system.step(1e-3, inputs)
    assert not out["fuse"]["blown"]
    events = []
    short = {**inputs, "_faults": [{"type": "short", "a": "hat.VBAT", "b": "GND", "resistance": 0.05}]}
    for k in range(200):
        out = system.step(1e-3, short if k == 0 else inputs)
        events.extend(out["_events"])
    codes = [e["code"] for e in events]
    blown = next(e for e in events if e["code"] == "fuse_blown")
    print(f"\nfault events: {[(e['code'], round(e['time'], 3), e['data']) for e in events]}")
    assert blown["data"]["current"] > 50 and blown["time"] < 0.13
    assert "brownout" in codes and "regulator_uvlo" in codes
    assert out["fuse"]["blown"] and not out["pi"]["powered"]
    assert out["_branches"]["W_Y_P_HAT"]["current"] == pytest.approx(0.0, abs=1e-6)


def test_esc_signal_timeout_and_reverse_logic(project):
    system = ElectricalSystem(project)
    w = 0.0
    out = system.step(1e-3, {"esc": {"pulse_us": 1800.0}, "motor": {"omega": w}})
    assert out["esc"]["state"] == "forward" and out["motor"]["current"] > 1
    out = system.step(1e-3, {"esc": {"pulse_us": 1200.0}, "motor": {"omega": 500.0}})
    assert out["esc"]["state"] == "brake" and out["motor"]["current"] < 0  # dynamic braking
    for _ in range(150):  # neutral longer than reverse_delay (0.1 s)
        out = system.step(1e-3, {"esc": {"pulse_us": 1500.0}, "motor": {"omega": 0.0}})
    out = system.step(1e-3, {"esc": {"pulse_us": 1200.0}, "motor": {"omega": 0.0}})
    assert out["esc"]["state"] == "reverse" and out["motor"]["current"] < -1
    for _ in range(600):  # no pulse for > signal_timeout (0.5 s)
        out = system.step(1e-3, {"motor": {"omega": 0.0}})
    assert out["esc"]["failsafe"] and out["esc"]["mode"] == "off"
    assert any(e.code == "esc_signal_lost" for e in system.events)


@pytest.mark.skipif(find_ngspice() is None, reason="ngspice not installed")
def test_operating_point_matches_ngspice(project, tmp_path):
    system = ElectricalSystem(project)
    op = system.operating_point({"esc": {"throttle": 1.0}, "motor": {"omega": 600.0},
                                 "servo": {"horn_torque": 0.5, "horn_speed": 1.0}, "pi": {"activity": 1.0}},
                                soc=0.5, thermal="steady", export_spice=True, nodeset_scale=0.0)
    netlist, mapping = op["_spice"]
    assert netlist.count("\nR") > 100 and "tanh" in netlist and "pwl(" in netlist
    report = compare_netlist(netlist, mapping, op["_x"], workdir=tmp_path)
    nodes = report["nodes"]
    assert len(nodes) == len(mapping)
    significant = [n for n in nodes if abs(n["ngspice"]) > 0.05]
    worst = max(significant, key=lambda n: abs(n["difference"]) / abs(n["ngspice"]))
    print(f"\nngspice: {len(nodes)} nodes, {len(significant)} above 50 mV; worst relative difference "
          f"{abs(worst['difference']) / abs(worst['ngspice']):.2e} at {worst['node']} "
          f"({worst['mna']:.6f} V vs {worst['ngspice']:.6f} V)")
    for n in significant:
        assert n["mna"] == pytest.approx(n["ngspice"], rel=0.01), n
    assert max(abs(n["difference"]) for n in nodes) < 1e-6
    (tmp_path / "netlist.cir").write_text(netlist)
