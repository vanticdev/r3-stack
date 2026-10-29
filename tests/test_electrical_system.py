"""System-level electrical twin tests on the reference RC car."""

import math
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.electrical import ElectricalSystem
from r3_sim.electrical.ngspice import find_library

pytestmark = pytest.mark.skipif(find_library() is None, reason="libngspice not installed")

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
    assert out["_nets"]["SERVO_6V"] == pytest.approx(6.05, abs=0.05)  # HAT TPS56528 servo rail
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
                     out["pi"]["powered"], out["esc"]["current_limited"], out["motor"]["current_mean"]))
        assert out["_solver"]["converged"]
    t, w, i, tau, vb, powered, limited, i_mean = map(np.array, zip(*rows))
    print(f"\nngspice lockstep: {system.stats}")
    peak = int(np.argmax(i))
    print(f"\npeak motor current {i[peak]:.1f} A at {t[peak] * 1e3:.0f} ms; I(0.5 s) {i[499]:.1f} A, I(1 s) {i[999]:.1f} A, "
          f"I(2 s) {i[-1]:.1f} A; min battery {vb.min():.3f} V; SOC {out['battery']['soc']:.5f}; "
          f"ESC {out['esc']['temperature_c']:.1f} C, motor {out['motor']['temperature_c']:.1f} C, battery {out['battery']['temperature_c']:.2f} C")
    # inductive rise in the first milliseconds (L/R ~ 0.4 ms)
    assert i[0] < i[peak] and t[peak] < 0.02
    # quasi-static analytic check at the peak: I = (V_A - V_B - Ke w - V_brush) / R_motor
    motor = system.models["motor"]
    # torque relation holds exactly at every step, with the winding current averaged over the step
    # (the torque impulse MuJoCo integrates over the zero-order-hold step)
    np.testing.assert_allclose(tau, KT * (i_mean - 1.6 * np.tanh(w / 1.0)), rtol=1e-9)
    np.testing.assert_allclose(i_mean[10:], 0.5 * (i[9:-1] + i[10:]), rtol=0.01)  # smooth after the inductive rise
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


def test_initial_state_and_no_signal_pulse(project):
    """Sim contract: ``initial={"battery_soc": ...}`` (extra keys ignored) and ``pulse_us <= 0`` = no signal."""
    system = ElectricalSystem(project, initial={"battery_soc": 0.5, "mode": "manual", "gps_origin": [0, 0, 0]})
    out = system.step(1e-3, {"esc": {"pulse_us": 1500.0}})
    assert out["battery"]["soc"] == pytest.approx(0.5, abs=1e-6)
    assert out["battery"]["v_ocv"] == pytest.approx(2 * 3.82, abs=1e-6)
    for _ in range(520):  # pulse 0 for longer than signal_timeout (0.5 s)
        out = system.step(1e-3, {"esc": {"pulse_us": 0.0}, "servo": {"pulse_us": 0.0}})
    assert not out["esc"]["signal_valid"] and out["esc"]["failsafe"] and out["esc"]["mode"] == "off"
    assert any(e.code == "esc_signal_lost" for e in system.events)
    system.close()


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


def test_hand_calculations_agree_with_ngspice(project):
    """Closed-form hand calculations, evaluated with the catalog parameters and
    the routed/estimated conductor resistances, against ngspice operating points."""
    system = ElectricalSystem(project)
    stall = system.operating_point({"esc": {"throttle": 1.0}, "motor": {"omega": 0.0},
                                     "servo": {"horn_torque": 0.2, "horn_speed": 0.5}, "pi": {"activity": 1.0}}, soc=0.5)
    battery, motor, esc = system.models["battery"], system.models["motor"], system.models["esc"]
    i_m = stall["motor"]["current"]
    # 1. Motor loop at stall: I = (d V_in - V_brush tanh(I / 0.1)) / (2 r_on + R_leads + R_motor(25 C)),
    #    R_leads = the conductors that carry exactly the motor current.
    leads = sum(b["resistance"] for b in stall["_branches"].values() if abs(abs(b["current"]) - abs(i_m)) < 1e-9 * abs(i_m))
    v_in = stall["esc"]["v_in"]
    hand = (1.0 * v_in - 0.2 * math.tanh(i_m / 0.1)) / (2 * esc.r_on + leads + motor.resistance_at(25.0))
    print(f"\nstall: ngspice {i_m:.6f} A, hand {hand:.6f} A (V_in {v_in:.4f} V, leads {leads * 1e3:.3f} mohm)")
    assert i_m == pytest.approx(hand, rel=1e-6)
    assert stall["motor"]["torque"] == pytest.approx(KT * i_m, rel=1e-9)
    # 2. Battery Thevenin at DC: V = 2 OCV(0.5) - I (R0 + R1) at 25 C.
    i_b = stall["battery"]["current"]
    v_hand = 2 * 3.82 - i_b * (battery.r0_ref + battery.r1_ref)
    assert stall["battery"]["v_terminal"] == pytest.approx(v_hand, rel=1e-6)
    # 3. Buck power balance: V_in I_in = V_out I_out / eta(I_out) + V_in I_q.
    reg = stall["hat"]["regulator"]
    core = system.models["hat"].core
    p_in = reg["v_out"] * reg["i_out"] / reg["efficiency"] + reg["v_in"] * core.quiescent
    assert reg["power_in"] == pytest.approx(p_in, rel=1e-4)
    # 4. Kirchhoff: the battery current leaves through the battery lead.
    assert stall["_branches"]["W_BAT_P"]["current"] == pytest.approx(i_b, rel=1e-9)
    # 5. Electrothermal steady state of a wire: dT = I^2 R(T) R_th solved in closed form (Branch.steady_rise).
    cruise = system.operating_point({"esc": {"throttle": 0.3}, "motor": {"omega": 300.0}, "pi": {"activity": 0.7}},
                                    soc=0.5, thermal="steady")
    wire = system.network.branches["W_BAT_P"]
    entry = cruise["_branches"]["W_BAT_P"]
    rise = wire.steady_rise(entry["current"], system.ambient_c)
    print(f"W_BAT_P at {entry['current']:.3f} A: ngspice rise {entry['temperature_rise']:.6f} K, closed form {rise:.6f} K")
    assert entry["temperature_rise"] == pytest.approx(rise, rel=1e-6)
    assert entry["resistance"] == pytest.approx(wire.resistance_at(system.ambient_c + rise), rel=1e-9)
    system.close()


def test_ngspice_runs_in_lockstep_with_the_master_clock(project):
    system = ElectricalSystem(project)
    for k in range(50):
        out = system.step(1e-3, {"esc": {"pulse_us": 1700.0}, "motor": {"omega": 2.0 * k}})
        assert out["_solver"]["spice_time"] == pytest.approx((k + 1) * 1e-3, abs=1e-12)
    assert system.stats["steps"] == 50 and system.stats["accepted_points"] >= 50
    deck = system.to_spice("tran")
    assert "external" in deck and ".subckt esc_brushed" in deck and ".ic v(" in deck
    system.close()


def test_pwm_switched_bridge_resolves_ripple_and_matches_the_averaged_mean(project):
    results = {}
    for mode in ("averaged", "switched"):
        system = ElectricalSystem(project, esc_model=mode)
        for _ in range(200):  # 50 % throttle at constant speed: steady state after the L/R transient
            out = system.step(1e-3, {"esc": {"pulse_us": 1750.0}, "motor": {"omega": 600.0}})
        results[mode] = (out["motor"]["current"], out["motor"]["current_mean"], out["motor"]["torque"], system.stats)
        system.close()
    (avg_end, avg_mean, avg_torque, _), (sw_end, sw_mean, sw_torque, stats) = results["averaged"], results["switched"]
    print(f"\naveraged: I {avg_mean:.3f} A, torque {avg_torque:.5f}; switched: step mean {sw_mean:.3f} A, "
          f"sample at the PWM edge {sw_end:.3f} A, torque {sw_torque:.5f}; {stats}")
    assert abs(avg_end - avg_mean) < 0.01  # no ripple in the averaged model
    assert abs(sw_end - sw_mean) > 0.5  # 1 kHz ripple resolved by ngspice
    assert sw_mean == pytest.approx(avg_mean, rel=0.1)
    assert stats["accepted_points"] > 10 * stats["steps"]  # every PWM corner is a time point
