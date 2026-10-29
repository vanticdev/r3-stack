import json
import sys
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.evaluation import lap_progress
from r3_sim.scenario import run_scenario

ROOT = Path(__file__).resolve().parents[1]
RC_CAR = ROOT / "examples" / "robots" / "rc-car"
CONTROLLER = ROOT / "tests" / "sim_test_controller.py"


def test_lap_progress_counts_course_laps():
    waypoints = [[0, 0], [10, 0], [10, 5], [0, 5]]
    loop = np.array([[0, 0], [10, 0], [10, 5], [0, 5]], dtype=float)
    path = np.vstack([np.linspace(loop[i], loop[(i + 1) % 4], 50) for i in range(4)] * 2)
    t = np.arange(len(path)) * 0.1
    laps, start, end = lap_progress(path, t, waypoints, closed=True)
    assert laps == 2 and start == 0.0 and end is not None


def test_full_throttle_end_to_end_with_test_controller(tmp_path):
    project = Project.load(RC_CAR)
    outcome = run_scenario(project, "full_throttle", [sys.executable, str(CONTROLLER)], out_dir=tmp_path, render=False)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert outcome.passed, summary["checks"]
    top = summary["metrics"]["top_speed_mps"]
    assert 8.0 <= top < 10.5
    assert summary["software"]["launches"][0]["connected"]
    assert summary["metrics"]["hal_requests"] > 1000
    assert summary["metrics"]["brownouts"] == 0
    assert any(log["fields"].get("event") == "start" for log in summary["firmware_logs"])
    recording = np.load(tmp_path / "recording.npz")
    for key in ("t", "x", "y", "speed", "battery_v", "battery_i", "soc", "rail_5v", "rssi", "lq", "motor_current", "steer",
                "wire_clearance"):
        assert key in recording.files
    for plot in ("trajectory", "speed", "battery", "rssi_vs_distance", "currents"):
        assert (tmp_path / f"{plot}.png").stat().st_size > 10_000
