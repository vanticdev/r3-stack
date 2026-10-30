"""Tool-independent carrier-HAT design checks: regulators, failsafe arbiter, IPC-2152."""

from __future__ import annotations

import math

import pytest

from r3_kicad import ipc2152
from pcb_helpers import buck_fixture, boost_fixture
from r3_kicad.arbiter import ArbiterParams, ArbiterState, FailsafeArbiter
from r3_kicad.regulators import (LM5175, TPS568230, buck_boost_efficiency, hold_up_time, regulated_output)


def test_ipc2152_round_trip_and_known_values():
    for current in (0.5, 1.0, 3.0, 5.0):
        width = ipc2152.required_width(current, 1.0, 10.0)
        assert ipc2152.max_current(width, 1.0, 10.0) == pytest.approx(current, rel=1e-6)
    # Brooks fit, 1 oz, 10 C: 1 A needs ~0.28 mm, 5 A ~4.6 mm; 20 C rise roughly halves the width.
    assert ipc2152.required_width(1.0) == pytest.approx(0.28, abs=0.01)
    assert ipc2152.required_width(5.0, 1.0, 20.0) == pytest.approx(2.54, abs=0.02)
    # R = rho L / A: 10 mm x 1 mm x 34.3 um copper ~ 5.0 mOhm at 20 C
    assert ipc2152.trace_resistance(10.0, 1.0) == pytest.approx(1.72e-8 * 0.01 / (1e-3 * 34.29e-6), rel=1e-3)


def test_buck_meets_declared_requirements():
    design = buck_fixture()
    v = design.values
    assert design.ic is TPS568230
    assert abs(v["v_out_nominal"] - design.req.v_out) < .01 * design.req.v_out
    assert v["v_out_min"] < design.req.v_out < v["v_out_max"]
    assert v["i_out_limit_min"] >= design.req.i_out
    assert not design.failed_checks, design.failed_checks
    assert v["v_in_dropout"] < design.req.v_in_min
    assert regulated_output(design, design.req.v_in_min, design.req.i_out) == pytest.approx(v["v_out_nominal"])
    assert v["c_out_effective"] == pytest.approx(design.c_out.effective)


def test_buck_boost_meets_declared_requirements_without_dropout():
    design = boost_fixture()
    v = design.values
    assert design.ic is LM5175 and design.topology == "buck_boost"
    assert v["v_out_nominal"] == pytest.approx(design.req.v_out, abs=.01)
    assert v["i_out_limit_min"] >= design.req.i_peak
    assert not design.failed_checks, design.failed_checks
    assert v["uvlo_falling"] < v["uvlo_rising"] < design.req.v_in_min
    assert v["f_sw"] == pytest.approx(design.req.f_sw, rel=.02)
    assert v["v_comp_buck_vin_transient"] > .3 and v["v_comp_boost_vin_min"] < 3
    assert buck_boost_efficiency(design, design.req.v_in_min, design.req.i_out) > .9
    assert buck_boost_efficiency(design, design.req.v_in_max, design.req.i_out) > .9
    assert design.r_sense <= min(v["r_sense_buck_max"], v["r_sense_boost_max"])


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


def test_arbiter_times_out_within_100_ms_plus_one_frame_and_brakes_before_neutral():
    params = ArbiterParams()
    arb = FailsafeArbiter()
    _run(arb, 0.0, 0.1, 0.01, 1500.0)
    assert arb.state is ArbiterState.PASS
    last_edge = arb.last_edge
    frames = _run(arb, 0.1, 4.0, None, 1900.0)                    # Pi hangs: heartbeat stops, PWM keeps commanding
    first_failsafe = next(t for t, us in frames if us != 1900.0)
    assert first_failsafe - last_edge <= params.timeout + params.frame + 1e-9
    # After forward driving the reverse pulse is the ESC's brake: brake for brake_hold, then neutral for good.
    failsafe = [(t, us) for t, us in frames if t >= first_failsafe]
    assert {us for t, us in failsafe if t < first_failsafe + params.brake_hold - 1e-9} == {params.brake_us}
    assert {us for t, us in failsafe if t >= first_failsafe + params.brake_hold + 1e-9} == {params.neutral_us}
    assert arb.failsafe_latency() == pytest.approx(0.12)


def test_arbiter_never_sends_a_reverse_pulse_the_esc_could_take_as_reverse():
    params = ArbiterParams()
    # Reversing (forward -> neutral -> brake -> neutral -> reverse): a reverse pulse would keep reversing.
    arb = FailsafeArbiter()
    _run(arb, 0.0, 0.1, 0.01, 1500.0)
    for command in (1800.0, 1500.0, 1200.0, 1500.0, 1200.0):
        _run(arb, round(arb.last_edge + 0.01, 4), round(arb.last_edge + 0.5, 4), 0.01, command)
    assert arb.esc.value == "reverse"
    frames = _run(arb, round(arb.last_edge + 0.01, 4), round(arb.last_edge + 1.0, 4), None, 1200.0)
    assert {us for _, us in frames if us != 1200.0} == {params.neutral_us}
    # Braking when the Pi hangs: the reverse pulse continues the brake.
    arb = FailsafeArbiter()
    _run(arb, 0.0, 0.1, 0.01, 1500.0)
    _run(arb, 0.1, 0.6, 0.01, 1800.0)
    _run(arb, 0.6, 0.8, 0.01, 1300.0)
    assert arb.esc.value == "brake"
    frames = _run(arb, 0.8, 1.2, None, 1300.0)
    assert params.brake_us in {us for _, us in frames}
    # brake_hold = 0 is the neutral-only contract.
    arb = FailsafeArbiter(ArbiterParams(brake_hold=0.0))
    _run(arb, 0.0, 0.1, 0.01, 1500.0)
    _run(arb, 0.1, 0.5, 0.01, 1800.0)
    frames = _run(arb, 0.5, 1.0, None, 1800.0)
    assert {us for _, us in frames if us != 1800.0} == {params.neutral_us}
