"""Tool-independent carrier-HAT design checks: regulators, failsafe arbiter, IPC-2152."""

from __future__ import annotations

import math

import pytest

from r3_kicad import ipc2152
from r3_kicad.arbiter import ArbiterParams, ArbiterState, FailsafeArbiter
from r3_kicad.regulators import TPS56528, TPS568230, hold_up_time, pi_rail, regulated_output, servo_rail


def test_ipc2152_round_trip_and_known_values():
    for current in (0.5, 1.0, 3.0, 5.0):
        width = ipc2152.required_width(current, 1.0, 10.0)
        assert ipc2152.max_current(width, 1.0, 10.0) == pytest.approx(current, rel=1e-6)
    # Brooks fit, 1 oz, 10 C: 1 A needs ~0.28 mm, 5 A ~4.6 mm; 20 C rise roughly halves the width.
    assert ipc2152.required_width(1.0) == pytest.approx(0.28, abs=0.01)
    assert ipc2152.required_width(5.0, 1.0, 20.0) == pytest.approx(2.54, abs=0.02)
    # R = rho L / A: 10 mm x 1 mm x 34.3 um copper ~ 5.0 mOhm at 20 C
    assert ipc2152.trace_resistance(10.0, 1.0) == pytest.approx(1.72e-8 * 0.01 / (1e-3 * 34.29e-6), rel=1e-3)


def test_pi_rail_meets_brief():
    design = pi_rail()
    v = design.values
    assert design.ic is TPS568230
    assert TPS568230.uvlo_rising_max <= 4.5                       # brief: UVLO <= 4.5 V
    assert 4.75 < v["v_out_min"] and v["v_out_max"] < 5.35        # Pi 5 V rail limits (USB 5 V +-5 %)
    assert v["v_out_nominal"] == pytest.approx(5.105, abs=0.01)
    assert v["i_out_limit_min"] >= 5.0                            # 5 A continuous within the valley limit
    assert not design.failed_checks, design.failed_checks
    # Stays regulated down to ~5.9 V input at 5 A; at 6 V input it still delivers the set point.
    assert v["v_in_dropout"] < 6.0
    assert regulated_output(design, 6.0, 5.0) == pytest.approx(v["v_out_nominal"])
    # Output capacitance inside TI's Table 3 range for 5 V (66-500 uF).
    assert 66e-6 <= v["c_out_effective"] <= 500e-6


def test_servo_rail_meets_brief():
    design = servo_rail()
    v = design.values
    assert design.ic is TPS56528
    assert v["v_out_nominal"] == pytest.approx(6.05, abs=0.02)
    assert v["i_out_limit_min"] >= 5.0                            # >= 5 A peak
    assert v["i_out_limit_max"] < 10.0                            # and a real limit for a stalled/shorted servo
    assert v["i_l_peak_at_ocl"] < design.inductor.i_sat
    assert not design.failed_checks, design.failed_checks
    # Dropout: below ~7.8 V at 2.5 A the rail follows Dmax * Vin.
    assert regulated_output(design, 6.0, 2.5) < 5.0
    assert regulated_output(design, 8.4, 2.5) == pytest.approx(v["v_out_nominal"])


def test_hold_up_time_formula():
    # 100 uF from 5 V to 4 V at 1 W: C (V0^2 - V1^2) / 2P = 100e-6 * 9 / 2 = 450 us
    assert hold_up_time(100e-6, 5.0, 4.0, 1.0) == pytest.approx(450e-6)


def _run(arbiter: FailsafeArbiter, t0: float, t1: float, heartbeat_period: float | None, command_us: float,
         frame: float = 0.02) -> list[tuple[float, float | None]]:
    out = []
    t = t0
    next_edge = t0 if heartbeat_period else math.inf
    while t < t1 - 1e-9:
        while heartbeat_period and next_edge <= t + 1e-12:
            arbiter.heartbeat_edge(next_edge)
            next_edge += heartbeat_period
        out.append((round(t, 4), arbiter.output_pulse(t, command_us)))
        t += frame
    return out


def test_arbiter_boots_in_failsafe_and_rearms_only_at_neutral():
    arb = FailsafeArbiter()
    assert arb.state is ArbiterState.FAILSAFE
    frames = _run(arb, 0.0, 0.2, 0.01, 1800.0)                    # alive, but commanding throttle
    assert all(us == 1500.0 for _, us in frames)                  # no re-arm into a non-neutral command
    frames = _run(arb, 0.2, 0.3, 0.01, 1500.0)
    assert arb.state is ArbiterState.PASS
    frames = _run(arb, 0.3, 0.4, 0.01, 1700.0)
    assert all(us == 1700.0 for _, us in frames)


def test_arbiter_times_out_within_100_ms_plus_one_frame():
    arb = FailsafeArbiter()
    _run(arb, 0.0, 0.1, 0.01, 1500.0)
    assert arb.state is ArbiterState.PASS
    last_edge = arb.last_edge
    frames = _run(arb, 0.1, 0.5, None, 1900.0)                    # Pi hangs: heartbeat stops, PWM keeps commanding
    first_neutral = next(t for t, us in frames if us == 1500.0)
    assert first_neutral - last_edge <= ArbiterParams().timeout + ArbiterParams().frame + 1e-9
    assert all(us == 1500.0 for t, us in frames if t >= first_neutral)
    assert arb.failsafe_latency() == pytest.approx(0.12)
