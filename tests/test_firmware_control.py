"""Control laws in isolation: pure pursuit in a kinematic bicycle sim, speed
control, sag-aware low-voltage protection and the arming/failsafe supervisor."""

from __future__ import annotations

import math

import firmware_fake_hal  # noqa: F401
import pytest
from control import (BatteryConfig, BatteryMonitor, LinkMonitor, PulseMap, PurePursuit, PurePursuitConfig,
                     SpeedController, SpeedPlanner, SpeedPlannerConfig, State, Supervisor, SupervisorConfig,
                     WaypointPath, stick_to_normalised)

WHEELBASE = 0.26
MAX_STEER = 0.45


def bicycle_sim(path: WaypointPath, laps: int, start: tuple[float, float, float], cruise: float,
                duration: float, dt: float = 0.01) -> tuple[list[tuple[float, float, float, float]], PurePursuit]:
    """Kinematic bicycle (rear-axle reference): x' = v cos psi, y' = v sin psi,
    psi' = v tan(delta) / L, with a 5 rad/s steering slew and first-order speed lag."""
    pursuit = PurePursuit(path, laps, PurePursuitConfig(wheelbase_m=WHEELBASE, max_steer_rad=MAX_STEER))
    planner = SpeedPlanner(path, laps, SpeedPlannerConfig(cruise_mps=cruise))
    x, y, yaw = start
    v = steer = 0.0
    trace = []
    t = 0.0
    while t < duration:
        command = pursuit.update(x, y, yaw, v)
        if command.finished:
            break
        v_ref = planner.update(dt, command.progress_m, pursuit.end_s, command.curvature)
        steer += max(-5.0 * dt, min(5.0 * dt, command.steer_rad - steer))
        v += (v_ref - v) * dt / 0.3
        yaw += v * math.tan(steer) / WHEELBASE * dt
        x += v * math.cos(yaw) * dt
        y += v * math.sin(yaw) * dt
        trace.append((t, x, y, command.cross_track_m))
        t += dt
    return trace, pursuit


def test_pure_pursuit_converges_on_straight_line() -> None:
    path = WaypointPath([(0.0, 0.0), (80.0, 0.0)], closed=False)
    # Start 2 m left of the line, pointing 30 degrees away from it.
    trace, pursuit = bicycle_sim(path, 1, (0.0, 2.0, math.radians(30.0)), cruise=4.0, duration=40.0)
    errors = [abs(y) for _, x, y, _ in trace]
    settle = next(i for i, (_, x, y, _) in enumerate(trace) if x > 15.0)
    assert max(errors[settle:]) < 0.05, "converges to the line within 15 m"
    assert max(errors) < 3.0, "no wide overshoot"
    assert trace[-1][1] > 79.0 and pursuit.progress >= pursuit.end_s - 0.6


def test_pure_pursuit_tracks_rectangle_course() -> None:
    corners = [(5.0, 0.0), (35.0, 0.0), (35.0, 15.0), (5.0, 15.0)]
    path = WaypointPath(corners, closed=True)
    trace, pursuit = bicycle_sim(path, 2, (0.0, 0.0, 0.0), cruise=4.0, duration=120.0)
    # Cross-track error against the true polyline once on the course.
    s, worst = 0.0, 0.0
    for t, x, y, _ in trace:
        if x < 5.0 and t < 3.0:
            continue
        s, cte = path.project(x, y, s, 1.0, 6.0)
        worst = max(worst, abs(cte))
    assert worst < 1.5, f"robot.yaml expect max_cross_track_error_m 1.5, got {worst:.2f}"
    assert pursuit.progress >= pursuit.end_s - 0.6, "both laps completed"
    assert path.length == pytest.approx(90.0)


def test_path_projection_and_corners() -> None:
    path = WaypointPath([(0, 0), (10, 0), (10, 10)], closed=False)
    s, cte = path.project(4.0, 1.5, 0.0, 1.0, 10.0)
    assert s == pytest.approx(4.0) and cte == pytest.approx(1.5)
    assert path.corners() == [(pytest.approx(10.0), pytest.approx(math.pi / 2))]
    closed = WaypointPath([(0, 0), (10, 0), (10, 10), (0, 10)], closed=True)
    assert len(closed.corners()) == 4
    assert closed.point_at(25.0) == pytest.approx((5.0, 10.0))
    assert closed.point_at(45.0) == pytest.approx((5.0, 0.0)), "closed paths wrap per lap"


def test_pulse_mapping() -> None:
    pulses = PulseMap()
    assert pulses.esc_pulse(0.0) == 1500 and pulses.esc_pulse(1.0) == 2000 and pulses.esc_pulse(-1.0) == 1000
    assert pulses.steer_pulse(1.0) == 1878 and pulses.steer_pulse(-1.0) == 1122 and pulses.steer_pulse(0) == 1500
    assert pulses.steer_from_angle(0.45) == 1.0 and pulses.steer_from_angle(-1.0) == -1.0
    assert stick_to_normalised(1500.0 + 512.0) == 1.0 and stick_to_normalised(1505.0) == 0.0


def test_speed_controller_feed_forward_and_no_reverse() -> None:
    controller = SpeedController()
    u = controller.update(0.01, 4.0, 4.0)
    assert u == pytest.approx(0.04 + 0.4, abs=1e-6), "steady state = deadband + v_ref / v_full"
    assert controller.update(0.01, 0.0, 0.1) == 0.0, "never brakes (reverses) at standstill"
    controller.reset()
    assert controller.update(0.01, 0.0, 3.0) < 0.0, "brakes while rolling forward"


def battery_run(voltage_of_t, throttle_of_t, duration: float, dt: float = 0.05) -> tuple[BatteryMonitor, list[float]]:
    monitor = BatteryMonitor(BatteryConfig(limit_v=7.0, cutoff_v=6.6))
    scales, t = [], 0.0
    while t < duration:
        scales.append(monitor.update(t, dt, voltage_of_t(t), throttle_of_t(t)))
        if monitor.cutoff:
            break
        t += dt
    return monitor, scales


def test_transient_sag_does_not_cut_off() -> None:
    # 7.4 V pack sagging to 6.3 V for 0.3 s on hard acceleration, every 3 s.
    monitor, scales = battery_run(lambda t: 6.3 if 1.0 <= (t % 3.0) < 1.3 else 7.4, lambda t: 1.0, 30.0)
    assert not monitor.cutoff
    assert min(scales) > 0.25


def test_low_voltage_limit_scales_throttle() -> None:
    monitor, scales = battery_run(lambda t: 6.8, lambda t: 0.5, 1.0)
    assert not monitor.cutoff and monitor.limiting
    assert scales[-1] == pytest.approx(0.25 + 0.75 * 0.5, abs=0.02)  # halfway between cutoff and limit


def test_sustained_under_voltage_cuts_off_and_latches() -> None:
    monitor, _ = battery_run(lambda t: 7.2 - 0.1 * t, lambda t: 0.5, 30.0)
    assert monitor.cutoff and monitor.cutoff_reason == "sustained_under_load"
    # Hold time and filtering: the raw voltage crossed 6.6 V at t = 6 s.
    assert monitor.update(100.0, 0.05, 8.0, 0.0) == 0.0, "cutoff latches even if voltage recovers"


def test_resting_voltage_and_emergency_floor() -> None:
    monitor, _ = battery_run(lambda t: 6.75, lambda t: 0.0, 5.0)
    assert monitor.cutoff and monitor.cutoff_reason == "resting_voltage"
    monitor, _ = battery_run(lambda t: 7.4 if t < 1.0 else 6.0, lambda t: 1.0, 5.0)
    assert monitor.cutoff and monitor.cutoff_reason == "emergency_floor"


def test_supervisor_requires_arm_switch_cycle_and_brakes_on_link_loss() -> None:
    sup = Supervisor(SupervisorConfig())
    step = lambda t, link, arm, speed=0.0: sup.update(t, link, arm, 1500.0, True, speed, False, False).state
    assert step(0.0, True, 2000.0) == State.DISARMED, "boot -> disarmed"
    assert step(0.1, True, 2000.0) == State.DISARMED, "switch high at boot does not arm"
    assert step(0.2, True, 1000.0) == State.DISARMED
    assert step(0.3, True, 2000.0) == State.ARMED
    assert step(5.0, False, 2000.0, speed=4.0) == State.BRAKING
    assert step(5.5, False, 2000.0, speed=0.1) == State.FAILSAFE
    assert step(6.0, True, 2000.0) == State.DISARMED, "link restored but not re-armed"
    assert step(6.1, True, 2000.0) == State.DISARMED
    assert sup.failsafe_events == 1


def test_link_monitor_timeout_and_lq_zero() -> None:
    link = LinkMonitor(0.25)
    link.on_channels(1.0, [1500.0] * 16)
    assert link.ok(1.2) and not link.ok(1.26)
    link.on_link_statistics(1.2, 0, -110, -5)
    assert not link.ok(1.21), "fresh LQ = 0 report means the link is gone"
