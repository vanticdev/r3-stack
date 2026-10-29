"""Traction control / yaw assist A/B on the REAL R3 twin (``full_throttle``).

Runs ``r3_sim_worker`` twice on ``examples/robots/rc-car``: first the firmware as it
was before traction control (``traction.enabled`` and ``yaw_assist_enabled`` off and
the stall current limit lifted; launched with an explicit ``--config``), then the
stock firmware (HAL-offered config). Plots ground
truth from both recordings to
``examples/robots/rc-car/build/sim/full_throttle/traction_ab.png`` and prints the
key numbers. Takes about a minute. Usage: ``python tests/firmware_traction_evidence.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples/robots/rc-car"
FIRMWARE = PROJECT / "backend/python/rc_car_controller/main.py"
OUT = PROJECT / "build/sim/full_throttle"
PYTHONPATH = ["packages/robot-model", "packages/component-catalog", "packages/simulation", "packages/hardware-interface",
              "packages/wiring", "services/simulation-worker", "integrations/onshape", "integrations/kicad",
              "services/orchestrator"]
CAPTION = "rc_car_controller on the R3 twin (MuJoCo + electrical twin + virtual HAL), scenario full_throttle, seed 7"


def run_twin(software_cmd: list[str] | None) -> dict[str, np.ndarray]:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in PYTHONPATH))
    cmd = [sys.executable, "-m", "r3_sim_worker", str(PROJECT), "--scenario", "full_throttle", "--no-render", "--quiet"]
    if software_cmd:
        cmd += ["--software-cmd", *software_cmd]
    subprocess.run(cmd, cwd=ROOT, env=env, check=False)
    with np.load(OUT / "recording.npz", allow_pickle=True) as recording:
        return {key: recording[key] for key in ("t", "x", "y", "yaw", "speed", "motor_omega", "motor_current")}


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "assists_off.json"
        config.write_text(json.dumps({"config": {"mode": "manual",
                                                 "traction": {"enabled": False, "yaw_assist_enabled": False,
                                                              "motor_current_limit_a": 1000.0}}}))
        off = run_twin([sys.executable, str(FIRMWARE), "--config", str(config)])
        shutil.copy(OUT / "summary.json", Path(folder) / "summary_off.json")
        top_off = json.loads((Path(folder) / "summary_off.json").read_text())["metrics"]["top_speed_mps"]
    on = run_twin(None)  # stock firmware, leaves the stock artefacts in build/sim/full_throttle
    top_on = json.loads((OUT / "summary.json").read_text())["metrics"]["top_speed_mps"]

    figure, (speed_axis, path_axis, current_axis) = plt.subplots(1, 3, figsize=(17, 5))
    for run, label, color, top in ((off, "assists off", "C3", top_off), (on, "traction + yaw assist", "C0", top_on)):
        wheel = run["motor_omega"] / 9.066 * 0.055  # differential carrier speed at the tire (ratio 9.066, r 55 mm)
        speed_axis.plot(run["t"], run["speed"], color=color, label=f"{label}: ground (top {top:.2f} m/s)")
        speed_axis.plot(run["t"], wheel, color=color, ls=":", label=f"{label}: driven wheels (carrier)")
        path_axis.plot(run["x"], run["y"], color=color, label=label)
        current_axis.plot(run["t"], run["motor_current"], color=color,
                          label=f"{label}: peak {np.max(run['motor_current']):.1f} A")
        print(f"{label}: top speed {top:.3f} m/s, |y| max {np.max(np.abs(run['y'])):.3f} m, "
              f"|yaw| max {np.max(np.abs(run['yaw'])):.3f} rad, peak motor current {np.max(run['motor_current']):.1f} A")
    speed_axis.axhline(8.0, color="k", ls="--", lw=0.8, label="requirement 8 m/s")
    speed_axis.set(xlabel="t (s)", ylabel="speed (m/s)", title="ground speed vs driven-wheel speed")
    path_axis.set(xlabel="x east (m)", ylabel="y north (m)", title="trajectory (true)")
    path_axis.set_aspect("equal", adjustable="datalim")
    current_axis.axhline(60.0, color="k", ls="--", lw=0.8, label="XT60 burst / ESC continuous 60 A")
    current_axis.set(xlabel="t (s)", ylabel="motor current (A)", title="motor current (twin)")
    for axis in (speed_axis, path_axis, current_axis):
        axis.grid(alpha=0.3)
        axis.legend(fontsize=8)
    figure.text(0.01, 0.01, CAPTION, fontsize=7)
    figure.tight_layout(rect=(0, 0.03, 1, 1))
    figure.savefig(OUT / "traction_ab.png", dpi=130)
    plt.close(figure)
    print(OUT / "traction_ab.png")


if __name__ == "__main__":
    main()
