"""Traction control, launch control, stall current limit and gyro yaw assist.

Unit tests drive the controllers directly; closed-loop tests run the complete
rc_car_controller against the fake HAL's slip model (two-mass drivetrain, falling
tire curve, rear-axle spin-out), which reproduces the twin's ``full_throttle``
failure when the assists are off.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import firmware_fake_hal as fake
import pytest
from main import RcCarController
from settings import DEFAULT_CONFIG, channel_map, load_settings
from traction import (BrakeSlipController, DcMotorModel, GroundSpeedEstimator, LaunchLimiter, SlipController, TractionConfig,
                      YawStabilizer)

WHEEL_RADIUS, WHEELBASE, MAX_STEER = 0.055, 0.26, 0.45
ASSISTS_OFF = {"enabled": False, "yaw_assist_enabled": False}


# --- unit: motor model and slip controller --------------------------------------

def test_motor_model_round_trips_current_and_duty() -> None:
    cfg = TractionConfig()
    motor = DcMotorModel(cfg, WHEEL_RADIUS)
    assert motor.k_e == pytest.approx(60.0 / (2.0 * math.pi * 2100.0))
    for current, speed in ((10.0, 0.0), (25.0, 3.0), (5.0, 7.5)):
        duty = motor.duty_for_current(current, 8.2, speed)
        assert motor.current(duty, 8.2, speed) == pytest.approx(current)
    # ESC deadband rescaling d = (u - 0.03) / 0.97 and its inverse.
    assert motor.duty_for_throttle(motor.throttle_for_duty(0.4)) == pytest.approx(0.4)
    assert motor.duty_for_throttle(0.02) == 0.0
    # Force -> current: F r / (N eta k_t) + I_0.
    expected = 7.0 * WHEEL_RADIUS / (9.066 * 0.88 * motor.k_e) + 1.6
    assert motor.current_for_force(7.0) == pytest.approx(expected)


def test_slip_controller_cuts_force_on_wheel_spin_and_restores_it_on_grip() -> None:
    cfg = TractionConfig()
    slip = SlipController(cfg, DcMotorModel(cfg, WHEEL_RADIUS))
    dt, v_ground = 0.01, 4.0
    gripping = slip.ceiling(dt, v_ground, v_ground, 8.2)
    force_gripping = slip.force_budget
    # The ceiling grows while the wheel holds less than the target slip (grip available)...
    for _ in range(50):
        slip.ceiling(dt, v_ground, v_ground, 8.2)
    assert slip.force_budget > force_gripping
    # ...and falls once the wheel spins 50 % faster than the ground.
    spinning = [slip.ceiling(dt, v_ground, 1.5 * v_ground, 8.2) for _ in range(30)]
    assert slip.slip == pytest.approx(0.5)
    assert slip.force_budget < 0.5 * force_gripping
    # The throttle ceiling is in duty terms: a faster wheel needs more duty for the
    # same current (back-EMF), so compare the commanded current instead.
    assert slip.current_budget < DcMotorModel(cfg, WHEEL_RADIUS).current_for_force(force_gripping)
    assert all(0.0 <= c <= 1.0 for c in spinning) and 0.0 < gripping < 1.0
    assert slip.force_budget >= 0.0 and slip.integral * cfg.slip_ki_n_per_m >= -0.5 * 0.85 * slip.grip_force - 1e-9
    # Wheel spin ending (the tire gripping again) re-opens the budget within a second.
    for _ in range(100):
        slip.ceiling(dt, v_ground, v_ground, 8.2)
    assert slip.force_budget > force_gripping


def test_slip_controller_ignores_encoder_lag_at_launch() -> None:
    """At launch the 4-pulse encoder reads 0 while the car already moves; that must
    not look like 'grip available' and wind the integrator up."""
    cfg = TractionConfig()
    slip = SlipController(cfg, DcMotorModel(cfg, WHEEL_RADIUS))
    for _ in range(40):
        slip.ceiling(0.01, 0.8, 0.0, 8.2)
    lagging = slip.integral
    slip.reset()
    for _ in range(40):
        slip.ceiling(0.01, 0.8, 0.8, 8.2)
    assert lagging == pytest.approx(slip.integral)


def test_stall_current_limit_bounds_the_motor_current() -> None:
    cfg = TractionConfig(motor_current_limit_a=15.0, slip_trim_max_n=50.0, max_force_fraction=10.0)
    motor = DcMotorModel(cfg, WHEEL_RADIUS)
    slip = SlipController(cfg, motor)
    for _ in range(400):  # stalled wheel, controller pushing for force
        ceiling = slip.ceiling(0.01, 0.0, 0.0, 8.4)
    assert slip.current_limited
    assert motor.current(motor.duty_for_throttle(ceiling), 8.4, 0.0) == pytest.approx(15.0, rel=1e-6)
    # Stall protection alone (traction control disabled) gives the same bound.
    assert slip.current_limit_ceiling(8.4, 0.0, 0.0) == pytest.approx(ceiling)


# --- unit: ground speed, launch and yaw ------------------------------------------

def test_ground_speed_follows_accel_and_gps_and_ignores_a_spinning_wheel() -> None:
    est = GroundSpeedEstimator()
    dt, accel, bias = 0.01, 3.0, 0.2
    for _ in range(150):  # stationary: learn the accelerometer bias (mount tilt + zero-g offset)
        est.predict(dt, bias)
        est.update_stationary(dt, bias)
    est.update_gps(0.02)  # receiver alive; speed below the usable threshold
    assert est.bias == pytest.approx(bias, abs=0.02) and abs(est.speed) < 0.01
    v = 0.0
    for k in range(200):  # 2 s at 3 m/s^2; the driven wheel spins 40 % fast
        v += accel * dt
        est.predict(dt, accel + bias)
        if k % 10 == 9:
            est.update_gps(v)
        assert not est.update_wheel(1.4 * v, throttle=1.0, free_rolling=False)
    assert est.speed == pytest.approx(v, abs=0.1)
    # Free-rolling wheel readings are used.
    assert est.update_wheel(v, throttle=0.0, free_rolling=True)
    # Without GPS the wheel is used inside a one-sided gate: slower than the estimate passes.
    for _ in range(150):
        est.predict(dt, bias)
    assert est.since_gps > 1.0
    assert not est.update_wheel(est.speed + 3.0, throttle=1.0, free_rolling=False)
    assert est.update_wheel(est.speed - 0.05, throttle=1.0, free_rolling=False)


def test_launch_limiter_slews_only_at_low_speed() -> None:
    launch = LaunchLimiter(TractionConfig(launch_slew_per_s=3.0, launch_speed_mps=2.0))
    outputs = [launch.update(0.01, 1.0, 0.5) for _ in range(40)]
    assert outputs[0] == pytest.approx(0.03) and outputs[9] == pytest.approx(0.30)
    assert outputs[-1] == pytest.approx(1.0)
    launch.reset()
    assert launch.update(0.01, 1.0, 2.5) == 1.0          # above launch speed: no slew
    assert launch.update(0.01, -0.5, 0.5) == -0.5         # braking is never delayed


def test_yaw_stabilizer_counter_steers_with_speed_scheduled_gain() -> None:
    cfg = TractionConfig(steer_lag_s=0.0, yaw_integral_gain_per_s=0.0)
    yaw = YawStabilizer(cfg, WHEELBASE, MAX_STEER)
    # Straight-ahead command, car yawing left (+) at 0.5 rad/s: steer right (-).
    corrected = yaw.update(0.01, 0.0, 4.0, 0.5)
    assert yaw.reference == 0.0 and corrected < 0.0
    expected = -cfg.yaw_gain * WHEELBASE / 4.0 * (0.5 - cfg.yaw_deadband_rps)
    assert corrected == pytest.approx(expected)
    # Same yaw error at twice the speed: half the steer (constant loop gain k = v/L * dk/de).
    yaw.reset()
    assert yaw.update(0.01, 0.0, 8.0, 0.5) == pytest.approx(expected / 2.0)
    # Bicycle reference: w_ref = v tan(delta) / L; tracking it needs no correction.
    yaw.reset()
    delta = 0.05  # w_ref = 0.77 rad/s, inside the friction bound mu g / v = 2.2 rad/s
    w_ref = 4.0 * math.tan(delta) / WHEELBASE
    assert yaw.update(0.01, delta, 4.0, w_ref) == pytest.approx(delta)
    # Below the minimum speed the gyro is ignored.
    yaw.reset()
    assert yaw.update(0.01, 0.1, 0.3, 2.0) == 0.1
    # The correction is bounded.
    yaw.reset()
    assert yaw.update(0.01, 0.0, 1.0, 10.0) == pytest.approx(-cfg.yaw_max_correction_rad)


def test_yaw_heading_hold_integrates_lost_heading_and_cuts_force() -> None:
    cfg = TractionConfig(steer_lag_s=0.0, yaw_gain=0.0)
    yaw = YawStabilizer(cfg, WHEELBASE, MAX_STEER)
    for _ in range(10):  # 0.1 s at -0.4 rad/s: 0.04 rad of heading lost to the right
        corrected = yaw.update(0.01, 0.0, 5.0, -0.4)
    assert yaw.integral == pytest.approx(0.04, rel=1e-6)
    assert corrected == pytest.approx(WHEELBASE / 5.0 * cfg.yaw_integral_gain_per_s * 0.04)
    # Large yaw error: forward force is reduced to give the rear tires lateral grip back.
    yaw.update(0.01, 0.0, 5.0, -1.0)
    assert yaw.force_scale() < 1.0
    yaw.update(0.01, 0.0, 5.0, 0.0)
    assert yaw.force_scale() == 1.0


# --- closed loop with the fake HAL's slip model ----------------------------------

def run_manual(tmp_path: Path, traction: dict, params: fake.CarParams, duration: float,
               throttle=lambda t: 0.0 if t < 1.0 else 1.0, steering=None) -> tuple[fake.FakeCarHal, RcCarController]:
    hal = fake.FakeCarHal(fake.pilot_arm_at(0.5, throttle=throttle, steering=steering), params=params)
    path = tmp_path / "firmware.json"
    path.write_text(json.dumps(fake.firmware_document(None, mode="manual", traction=traction)))
    controller = RcCarController(hal, load_settings(str(path), environ={}))
    controller.run(duration)
    return hal, controller


def slip_ratios(hal: fake.FakeCarHal, after_t: float) -> list[float]:
    return [(wheel - v) / max(v, 1.0) for t, v, wheel, _, _ in hal.slip_trace if t > after_t]


def test_full_throttle_launch_holds_slip_and_drives_straight(tmp_path: Path) -> None:
    params = fake.CarParams(slip_model=True)
    hal, controller = run_manual(tmp_path, {}, params, 8.0)
    slips = slip_ratios(hal, 1.5)
    assert max(slips) < 0.25, "wheel spin is caught near the target slip"
    assert sum(slips) / len(slips) < 0.1
    assert max(abs(row[3]) for row in hal.trace) < 0.05, "heading stays within 3 degrees"
    assert abs(hal.car.y) < 0.5
    assert max(row[1] for row in hal.slip_trace) > 8.0
    assert hal.events("traction_limit")
    # Same car without the assists: the rear breaks loose and the car spins out.
    hal_off, _ = run_manual(tmp_path, ASSISTS_OFF, fake.CarParams(slip_model=True), 8.0)
    assert max(slip_ratios(hal_off, 1.5)) > 1.0
    assert max(abs(row[3]) for row in hal_off.slip_trace) > 3.0, "yaw rate of a spin-out"


def test_yaw_assist_recovers_heading_after_a_disturbance(tmp_path: Path) -> None:
    def disturbance(t: float) -> float:
        return 0.6 if 4.0 <= t < 4.5 else 0.0  # a 0.3 rad yaw kick (bump, gust)

    def half(t: float) -> float:
        return 0.0 if t < 1.0 else 0.5

    hal, _ = run_manual(tmp_path, {}, fake.CarParams(slip_model=True, yaw_disturbance=disturbance), 7.0, half)
    hal_off, _ = run_manual(tmp_path, {"yaw_assist_enabled": False},
                            fake.CarParams(slip_model=True, yaw_disturbance=disturbance), 7.0, half)

    def yaw_at(h: fake.FakeCarHal, t: float) -> float:
        return min(h.trace, key=lambda row: abs(row[0] - t))[3]

    assert yaw_at(hal_off, 6.99) == pytest.approx(0.3, abs=0.02), "without assist the heading is lost"
    assert yaw_at(hal, 4.5) < 0.6 * yaw_at(hal_off, 4.5), "counter-steer during the kick"
    assert abs(yaw_at(hal, 6.99)) < 0.05, "heading hold returns the car to its course"


def test_motor_current_limit_at_stall(tmp_path: Path) -> None:
    params = fake.CarParams(slip_model=True, blocked=True)
    hal, controller = run_manual(tmp_path, {"motor_current_limit_a": 12.0, "slip_trim_max_n": 40.0}, params, 4.0)
    assert max(row[4] for row in hal.slip_trace) <= 12.0 * 1.02
    assert hal.events("motor_current_limit")
    # Stall protection stays on with traction control disabled (default 50 A limit).
    hal_off, _ = run_manual(tmp_path, ASSISTS_OFF, fake.CarParams(slip_model=True, blocked=True), 3.0)
    assert 40.0 < max(row[4] for row in hal_off.slip_trace) <= 50.0 * 1.02


# --- configuration ---------------------------------------------------------------

def test_channel_map_comes_from_crsf_channels() -> None:
    assert channel_map(DEFAULT_CONFIG) == {"steering": 1, "throttle": 2, "arm": 5}
    assert channel_map({"crsf_channels": {"steering": 4, "throttle": 3}, "arm_channel": 7}) == \
        {"steering": 4, "throttle": 3, "arm": 7}
    with pytest.raises(ValueError, match="throttle"):
        channel_map({"crsf_channels": {"steering": 1, "arm": 5}})


def test_remapped_channels_drive_manual_mode(tmp_path: Path) -> None:
    channels = {"steering": 3, "throttle": 4, "arm": 6}
    pilot = fake.pilot_arm_at(0.5, throttle=lambda t: 0.0 if t < 1.0 else 0.5, steering=lambda t: 1.0, channels=channels)
    hal = fake.FakeCarHal(pilot)
    path = tmp_path / "firmware.json"
    path.write_text(json.dumps(fake.firmware_document(None, mode="manual", crsf_channels=channels, traction=ASSISTS_OFF)))
    RcCarController(hal, load_settings(str(path), environ={})).run(2.0)
    full_left = DEFAULT_CONFIG["steering_pulse_us"]["left"]
    assert hal.pulse_us(2) > 1700.0 and hal.pulse_us(3) > full_left - 0.05 * (full_left - 1500.0)


def test_flat_hal_document_lifts_mission_and_origin() -> None:
    """The twin's HAL offers software.config with the scenario mission and gps_origin merged in."""
    mission = {"type": "waypoints", "laps": 2, "waypoints": [[5, 0], [35, 0], [35, 15]]}
    settings = load_settings(None, environ={}, hal_document={
        "mode": "autonomous", "mission": mission, "gps_origin": {"lat": 37.4, "lon": -122.1, "alt": 30.0},
        "traction": {"target_slip": 0.12}})
    assert settings.mission == mission and settings.geodetic_origin["lat"] == 37.4
    assert "mission" not in settings.config and "gps_origin" not in settings.config
    assert TractionConfig.from_mapping(settings.config["traction"]).target_slip == 0.12
    assert settings.config["traction"]["motor_current_limit_a"] == 50.0, "defaults fill the rest"


# --- unit: anti-lock failsafe brake ---------------------------------------------

def test_brake_slip_controller_releases_a_locking_wheel_and_never_reaches_neutral() -> None:
    cfg = TractionConfig()
    brake = BrakeSlipController(cfg)
    dt = 0.01
    first = brake.update(dt, 8.0, 7.9)                      # rolling: the brake builds up
    for _ in range(20):
        brake.update(dt, 8.0, 7.9)
    assert brake.fraction > first
    for _ in range(50):                                     # wheel locking (slip 0.75): the brake backs off
        brake.update(dt, 8.0, 2.0)
    assert brake.fraction == pytest.approx(cfg.brake_min_fraction)  # floored, never neutral (would arm reverse)
    assert brake.update(dt, 0.4, 0.0) == 1.0                # slow: full brake
    brake.reset()
    assert brake.update(dt, 4.0, 0.0) < 0.01             # still release a locked axle at cruise speed


def test_brake_command_stays_on_the_brake_side_of_the_esc_deadband() -> None:
    from traction import TractionControl

    traction = TractionControl(TractionConfig(), WHEEL_RADIUS, WHEELBASE, MAX_STEER)
    traction.ground.v = 6.0
    out = traction.brake_command(0.01, 1.0, 0.0)            # heavy slip: minimum brake
    assert out.throttle < -TractionConfig().esc_deadband


def test_failsafe_prediction_does_not_fuse_delayed_gnss_speed():
    from traction import TractionControl
    traction = TractionControl(TractionConfig(), WHEEL_RADIUS, WHEELBASE, MAX_STEER)
    traction.ground.v = 8.0
    traction.ground.since_gps = 0.0  # fresh velocity fix immediately before braking
    for _ in range(50):
        traction.update_speed(.01, -4.0, 6.0, -.04, False, gps_speed=8.0, braking=True)
    assert traction.ground.speed == pytest.approx(6.0)
    # Steering stays centred even with a nonzero measured yaw rate and an accumulated yaw correction.
    traction.yaw.update(.01, .2, 6.0, .5)
    assert traction.brake_command(.01, 5.9, .5).steer_rad == 0.0
