"""Render firmware evidence plots into ``build/software/``.

Runs the real ``rc_car_controller`` against the in-process kinematic test double
(``firmware_fake_hal``), not the digital twin, and plots: the rectangle course
(true path vs EKF estimate), the radio-failsafe braking sequence, and low-voltage
limiting/cutoff. Usage: ``python tests/firmware_evidence.py``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "packages/hardware-interface")]

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import firmware_fake_hal as fake  # noqa: E402
from main import RcCarController  # noqa: E402
from settings import load_settings  # noqa: E402

OUT = ROOT / "build" / "software"
RECTANGLE = {"type": "waypoints", "laps": 2, "speed": 4.0, "waypoints": [[5, 0], [35, 0], [35, 15], [5, 15]]}
STRAIGHT = {"type": "waypoints", "laps": 1, "speed": 4.0, "waypoints": [[5, 0], [400, 0]]}
CAPTION = "rc_car_controller vs in-process kinematic test double (tests/firmware_fake_hal.py), not the R3 twin"


def run(hal: fake.FakeCarHal, mission: dict, duration: float, **config: object) -> RcCarController:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "firmware.json"
        path.write_text(json.dumps(fake.firmware_document(mission, **config)))
        controller = RcCarController(hal, load_settings(str(path), environ={}))
    controller.run(duration)
    return controller


def telemetry(hal: fake.FakeCarHal, *keys: str) -> list[list[float]]:
    rows = hal.events("telemetry")
    return [[r.t for r in rows]] + [[r.fields[k] if r.fields[k] is not None else float("nan") for r in rows] for k in keys]


def rectangle_plot() -> dict:
    hal = fake.FakeCarHal(fake.pilot_arm_at(1.0))
    run(hal, RECTANGLE, 70.0)
    t, ex, ey, cte, v, v_ref = telemetry(hal, "x", "y", "cross_track_m", "v", "v_ref")
    figure, (path_axis, speed_axis) = plt.subplots(1, 2, figsize=(13, 5))
    corners = RECTANGLE["waypoints"] + RECTANGLE["waypoints"][:1]
    path_axis.plot(*zip(*corners), "k--", lw=1, label="waypoints")
    path_axis.plot([r[1] for r in hal.trace], [r[2] for r in hal.trace], lw=1.5, label="true trajectory")
    path_axis.plot(ex, ey, ".", ms=2, label="EKF estimate (5 Hz telemetry)")
    path_axis.set(xlabel="east x [m]", ylabel="north y [m]", title="Autonomous course, 2 laps (pure pursuit)", aspect="equal")
    path_axis.legend(loc="center")
    speed_axis.plot([r[0] for r in hal.trace], [r[4] for r in hal.trace], lw=1, label="true speed")
    speed_axis.plot(t, v_ref, label="speed reference")
    speed_axis.plot(t, cte, label="cross-track error (EKF) [m]")
    speed_axis.set(xlabel="time [s]", ylabel="m/s, m", title="Speed control and cross-track error")
    speed_axis.legend()
    figure.suptitle(CAPTION, fontsize=9)
    figure.tight_layout()
    figure.savefig(OUT / "firmware_rectangle.png", dpi=130)
    plt.close(figure)
    return {"max_abs_cross_track_ekf_m": max(abs(c) for c in cte if c == c)}


def failsafe_plot() -> dict:
    lost = 8.0
    hal = fake.FakeCarHal(fake.pilot_arm_at(1.0), link_up=lambda t: t < lost)
    run(hal, STRAIGHT, 12.0)
    event = hal.events("radio_failsafe")[0]
    trace = [r for r in hal.trace if 6.0 <= r[0] <= 11.0]
    figure, axis = plt.subplots(figsize=(9, 4.5))
    axis.plot([r[0] for r in trace], [r[4] for r in trace], label="true speed [m/s]")
    second = axis.twinx()
    second.plot([r[0] for r in trace], [r[5] for r in trace], "C3", label="ESC pulse [us]")
    second.set_ylabel("ESC pulse [us]")
    axis.axvline(lost, color="k", ls=":", label="last CRSF frame")
    axis.axvline(event.t, color="C1", ls="--", label=f"failsafe detected (+{event.t - lost:.2f} s)")
    loss_x = next(r[1] for r in hal.trace if r[0] >= lost)
    stop = next(r for r in hal.trace if r[0] > event.t and r[4] < 0.05)
    axis.set(xlabel="time [s]", ylabel="speed [m/s]",
             title=f"Radio failsafe: brake then neutral; stopped {stop[1] - loss_x:.2f} m after link loss")
    axis.legend(loc="upper right")
    figure.text(0.01, 0.01, CAPTION, fontsize=7)
    figure.tight_layout()
    figure.savefig(OUT / "firmware_failsafe.png", dpi=130)
    plt.close(figure)
    return {"stop_distance_after_link_loss_m": stop[1] - loss_x, "detection_delay_s": event.t - lost}


def battery_plot() -> dict:
    params = fake.CarParams(battery_ocv=7.5, battery_ocv_drop_per_s=0.02, battery_r_int=0.04)
    hal = fake.FakeCarHal(fake.pilot_arm_at(1.0), params=params)
    controller = run(hal, {**RECTANGLE, "laps": 10}, 60.0)
    t, vbat, vf, scale, v = telemetry(hal, "vbat", "vbat_filtered", "throttle_scale", "v")
    figure, axis = plt.subplots(figsize=(9, 4.5))
    axis.plot(t, vbat, lw=0.8, label="INA226 bus voltage")
    axis.plot(t, vf, label="filtered (tau 0.5 s)")
    axis.axhline(7.0, color="C1", ls="--", label="low_voltage_limit_v 7.0")
    axis.axhline(6.6, color="C3", ls="--", label="low_voltage_cutoff_v 6.6")
    second = axis.twinx()
    second.plot(t, scale, "C2", label="throttle ceiling")
    second.plot(t, v, "C4", lw=0.8, label="speed [m/s]")
    second.set_ylabel("throttle scale, speed")
    cutoff = hal.events("low_voltage_cutoff")
    axis.set(xlabel="time [s]", ylabel="volts",
             title=f"Low-voltage limiting and cutoff ({controller.battery.cutoff_reason} at {cutoff[0].t:.1f} s)")
    axis.legend(loc="lower left", fontsize=8)
    second.legend(loc="upper right", fontsize=8)
    figure.text(0.01, 0.01, CAPTION, fontsize=7)
    figure.tight_layout()
    figure.savefig(OUT / "firmware_low_voltage.png", dpi=130)
    plt.close(figure)
    before = [value for time, value in zip(t, vbat) if time <= cutoff[0].t and value == value]
    return {"limit_t_s": hal.events("low_voltage_limit")[0].t, "cutoff_t_s": cutoff[0].t,
            "cutoff_reason": controller.battery.cutoff_reason, "min_bus_voltage_before_cutoff_v": min(before)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"rectangle": rectangle_plot(), "failsafe": failsafe_plot(), "low_voltage": battery_plot(),
               "note": CAPTION}
    (OUT / "firmware_evidence.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
