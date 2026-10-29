"""The complete rc_car_controller against the in-process fake car (lockstep).

Covers the radio failsafe on frame loss, autonomous rectangle laps through the
EKF on noisy GPS/IMU/encoder data, low-voltage limiting and cutoff, manual
mode, config loading and run-to-run determinism.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path

import firmware_fake_hal as fake
import pytest
from control import State, WaypointPath
from main import RcCarController
from settings import load_settings

RECTANGLE = {"type": "waypoints", "laps": 2, "speed": 4.0, "waypoints": [[5, 0], [35, 0], [35, 15], [5, 15]]}
STRAIGHT = {"type": "waypoints", "laps": 1, "speed": 4.0, "waypoints": [[5, 0], [400, 0]]}


def make_controller(tmp_path: Path, hal: fake.FakeCarHal, mission: dict | None, **config: object) -> RcCarController:
    path = tmp_path / "firmware.json"
    path.write_text(json.dumps(fake.firmware_document(mission, **config)))
    return RcCarController(hal, load_settings(str(path), environ={}))


def state_changes(hal: fake.FakeCarHal) -> list[tuple[float, str]]:
    return [(r.t, r.fields["to"]) for r in hal.events("state")]


def test_radio_failsafe_brakes_then_neutral_and_disarms(tmp_path: Path) -> None:
    link_lost_at, link_back_at = 8.0, 14.0
    arm = lambda t: 2000.0 if (1.0 <= t < 16.0 or t >= 17.0) else 1000.0
    hal = fake.FakeCarHal(lambda t: {5: arm(t)}, link_up=lambda t: not (link_lost_at <= t < link_back_at))
    controller = make_controller(tmp_path, hal, STRAIGHT)
    controller.run(20.0)

    failsafe = hal.events("radio_failsafe")
    assert len(failsafe) == 1
    last_frame = max(t for t in (i / 150.0 for i in range(int(link_lost_at * 150) + 1)) if t < link_lost_at)
    detection_delay = failsafe[0].t - last_frame
    # Frames are read on 10 ms ticks: detection <= timeout + read latency (1 tick) + one tick.
    assert 0.25 < detection_delay <= 0.25 + 0.02 + 1e-6, f"got {detection_delay:.3f} s"
    speed_at_loss = failsafe[0].fields["speed_mps"]
    assert speed_at_loss > 3.5

    trace = hal.trace
    braking = [row for row in trace if failsafe[0].t < row[0] <= failsafe[0].t + 0.3]
    assert all(row[5] < 1500.0 for row in braking[2:]), "active brake (ESC pulse below neutral)"
    stop = next(row for row in trace if row[0] > failsafe[0].t and row[4] < 0.05)
    loss_position = next(row for row in trace if row[0] >= link_lost_at)[1]
    stop_distance = stop[1] - loss_position
    # 0.25 s at ~4 m/s before detection + v^2 / (2 * 4.4 m/s^2) braking in the fake car.
    assert stop_distance < 0.26 * speed_at_loss + speed_at_loss ** 2 / (2 * 4.0)
    after_stop = [row for row in trace if stop[0] + 0.5 < row[0] < link_back_at]
    assert after_stop and all(row[5] == 1500.0 for row in after_stop), "neutral after braking"

    changes = state_changes(hal)
    assert ("braking" in [s for _, s in changes]) and ("failsafe" in [s for _, s in changes])
    restored = [t for t, s in changes if s == "disarmed" and t > link_back_at]
    assert restored, "link restored -> disarmed"
    rearmed = [t for t, s in changes if s == "armed" and t > link_back_at]
    assert rearmed and rearmed[0] >= 17.0, "re-arms only after the switch is cycled low -> high"


def test_autonomous_rectangle_two_laps_through_estimator(tmp_path: Path) -> None:
    hal = fake.FakeCarHal(fake.pilot_arm_at(1.0))
    controller = make_controller(tmp_path, hal, RECTANGLE)
    controller.run(80.0)
    complete = hal.events("mission_complete")
    assert complete and complete[0].fields["laps"] == 2
    assert [r.fields["lap"] for r in hal.events("lap_completed")] == [1]
    assert controller.supervisor.failsafe_events == 0
    path = WaypointPath([tuple(p) for p in RECTANGLE["waypoints"]], closed=True)
    armed_t = next(t for t, s in state_changes(hal) if s == "armed")
    s, worst = 0.0, 0.0
    for t, x, y, *_ in hal.trace:
        if armed_t + 2.0 < t < complete[0].t:
            s, cte = path.project(x, y, s, 1.0, 6.0)
            worst = max(worst, abs(cte))
    assert worst < 1.5, f"true cross-track error {worst:.2f} m (robot.yaml limit 1.5 m)"
    final = hal.car
    assert math.hypot(final.x - 5.0, final.y) < 2.0 and final.v == 0.0, "stops at the start/finish waypoint"
    telemetry = hal.events("telemetry")
    assert len(telemetry) == pytest.approx(80.0 * 5, abs=2), "5 Hz telemetry"
    assert {"x", "y", "v", "v_ref", "cross_track_m", "vbat", "lq", "state"} <= set(telemetry[-1].fields)


def test_low_voltage_limit_then_cutoff(tmp_path: Path) -> None:
    params = fake.CarParams(battery_ocv=7.5, battery_ocv_drop_per_s=0.02, battery_r_int=0.04)
    hal = fake.FakeCarHal(fake.pilot_arm_at(1.0), params=params)
    controller = make_controller(tmp_path, hal, {**RECTANGLE, "laps": 10})
    voltages: list[float] = []
    original = hal.advance_to

    def advance(t: float) -> None:
        original(t)
        voltages.append(hal.battery_voltage())

    hal.advance_to = advance  # type: ignore[method-assign]
    controller.run(60.0)
    limit = hal.events("low_voltage_limit")
    cutoff = hal.events("low_voltage_cutoff")
    assert limit and cutoff and limit[0].t < cutoff[0].t
    assert controller.battery.cutoff
    assert controller.supervisor.state == State.CUTOFF
    assert min(voltages) > 6.0, "cells never below 3.0 V under load"
    after = [row for row in hal.trace if row[0] > cutoff[0].t + 3.0]
    assert after and all(row[5] == 1500.0 for row in after) and after[-1][4] == 0.0


def test_manual_mode_maps_sticks_and_failsafes(tmp_path: Path) -> None:
    def pilot(t: float) -> dict[int, float]:
        throttle = 0.0 if t < 1.0 else 0.5
        return {2: 1500.0 + 512.0 * throttle, 1: 1500.0 + 512.0 * (1.0 if 2.0 <= t < 3.0 else 0.0)}

    hal = fake.FakeCarHal(pilot, link_up=lambda t: t < 5.0)
    controller = make_controller(tmp_path, hal, None, mode="manual")
    esc_at, servo_at = {}, {}
    original = controller.step

    def step(now: float) -> None:
        original(now)
        esc_at[round(now, 2)], servo_at[round(now, 2)] = hal.pulse_us(2), hal.pulse_us(3)

    controller.step = step  # type: ignore[method-assign]
    controller.run(8.0)
    assert esc_at[0.9] == 1500.0
    # 0.5 stick with a 2 % deadband -> (0.5 - 0.02) / 0.98 = 0.49 of forward travel.
    assert esc_at[1.5] == pytest.approx(1500.0 + 500.0 * 0.48 / 0.98, abs=1.0)
    assert servo_at[2.5] == pytest.approx(1878.0, abs=0.5) and servo_at[1.5] == 1500.0
    assert hal.events("radio_failsafe") and esc_at[7.9] == 1500.0
    assert controller.supervisor.state == State.FAILSAFE


def test_config_from_blitz_base64_and_hal_bindings(tmp_path: Path) -> None:
    document = {"config": {"control_rate_hz": 50, "mode": "manual"}, "bindings": {"crsf": {"device": "/dev/ttyAMA9"}}}
    path = tmp_path / "desired-config.txt"
    path.write_text(base64.b64encode(json.dumps(document).encode()).decode())
    settings = load_settings(None, environ={"BLITZ_CONFIG_PATH": str(path)},
                             hal_bindings={"gps": {"device": "/dev/ttyS0", "baud": "115200"}})
    assert settings.config["control_rate_hz"] == 50 and settings.config["mode"] == "manual"
    assert settings.config["low_voltage_cutoff_v"] == 6.6, "defaults fill unspecified keys"
    assert settings.bindings["crsf"]["device"] == "/dev/ttyAMA9" and settings.bindings["crsf"]["baud"] == 420000
    assert settings.bindings["gps"]["device"] == "/dev/ttyS0"
    assert settings.sources[-1] == "hal_bindings"


def test_robot_yaml_document_matches_firmware_defaults() -> None:
    from settings import DEFAULT_CONFIG, document_from_robot_yaml
    robot = Path(__file__).resolve().parents[1] / "examples/robots/rc-car/robot.yaml"
    document = document_from_robot_yaml(robot, scenario="full_throttle")
    for key, value in document["config"].items():
        if key != "mode":
            assert DEFAULT_CONFIG[key] == value, key
    assert document["config"]["mode"] == "manual" and document["bindings"]["esc_pwm"]["frequency"] == 50.0
    assert document_from_robot_yaml(robot, scenario="autonomous_course")["mission"]["laps"] == 2


def test_lockstep_runs_are_deterministic(tmp_path: Path) -> None:
    def run() -> list[tuple]:
        hal = fake.FakeCarHal(fake.pilot_arm_at(1.0), seed=11)
        make_controller(tmp_path, hal, RECTANGLE).run(12.0)
        return [(r.t, r.message, json.dumps(r.fields, sort_keys=True)) for r in hal.logs]

    assert run() == run()
