"""Convert a scenario's ``recording.npz`` into the API's playback JSON (dev mock data).

``GET /api/projects/{pid}/sim/{scenario}?hz=30`` returns ``recording`` in the shape documented in
docs/architecture/api.md: ``{hz, duration, channels, series, firmware_logs, events, frames}``, where
``series`` resamples every 1-D channel to ``hz`` and ``frames`` holds per-frame world poses of every
MuJoCo body (``bodies``), the body carrying each component (``component_bodies``) and cable polylines
(``cables``). The recording stores raw ``qpos`` snapshots, so poses come from MuJoCo's own forward
kinematics (``mj_kinematics``) on the scenario's ``vehicle.xml``. ``frames.rest`` (an addition) holds
each body's pose at ``qpos0``, the frame the robot-frame CAD meshes are authored in.

Usage (shared venv, repository root on PYTHONPATH as for the r3 CLI):
    python apps/web/scripts/convert_recording.py examples/robots/rc-car full_throttle --hz 30 [--out file.json]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

CABLE_BODY = re.compile(r"^(cable\w*?)_B_")
POSITION_DECIMALS = 5  # 10 µm


def _downsample_indices(times: np.ndarray, hz: float) -> np.ndarray:
    """Indices of the samples nearest to a uniform 1/hz grid (first and last sample always kept)."""
    if hz <= 0 or len(times) < 3 or (times[-1] - times[0]) * hz >= len(times):
        return np.arange(len(times))
    grid = np.arange(times[0], times[-1], 1.0 / hz)
    right = np.clip(np.searchsorted(times, grid), 1, len(times) - 1)
    nearest = np.where(np.abs(times[right] - grid) < np.abs(times[right - 1] - grid), right, right - 1)
    return np.unique(np.concatenate([nearest, [len(times) - 1]]))


def _round(values: np.ndarray, decimals: int) -> list[Any]:
    return np.round(values.astype(float), decimals).tolist()


def convert(project_root: Path, scenario: str, hz: float = 30.0) -> dict[str, Any]:
    import mujoco

    scenario_dir = project_root / "build" / "sim" / scenario
    recording = np.load(scenario_dir / "recording.npz", allow_pickle=False)
    times = recording["t"]
    hz = min(max(hz, 0.5), 200.0)
    duration = float(times[-1]) if len(times) else 0.0
    grid = np.arange(0.0, duration + 1e-9, 1.0 / hz)
    series: dict[str, list[Any]] = {"t": _round(grid, 4)}
    for key in recording.files:
        values = recording[key]
        if key == "t" or values.ndim != 1 or values.shape != times.shape or values.dtype.kind not in "fiu":
            continue
        series[key] = [v if math.isfinite(v) else None for v in _round(np.interp(grid, times, values.astype(float)), 6)]

    model = mujoco.MjModel.from_xml_path(str(scenario_dir / "vehicle.xml"))
    data = mujoco.MjData(model)
    names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i) or f"body{i}" for i in range(model.nbody)]
    rigid: list[int] = []
    cable_ids: dict[str, list[int]] = {}
    for i, name in enumerate(names[1:], start=1):
        match = CABLE_BODY.match(name)
        if match:
            cable_ids.setdefault(match.group(1), []).append(i)
        else:
            rigid.append(i)
    component_bodies: dict[str, str] = {}
    for g in range(model.ngeom):
        geom = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
        if geom.startswith("vis_"):
            component_bodies[geom[4:]] = names[model.geom_bodyid[g]]
    rgba = {}
    for cable, ids in cable_ids.items():
        geoms = [g for g in range(model.ngeom) if model.geom_bodyid[g] == ids[0]]
        rgba[cable] = _round(model.geom_rgba[geoms[0]], 3) if geoms else None

    def pose(i: int) -> list[float]:
        return _round(np.concatenate([data.xpos[i], data.xquat[i]]), POSITION_DECIMALS)

    data.qpos[:] = model.qpos0
    mujoco.mj_kinematics(model, data)
    rest = {names[i]: pose(i) for i in rigid}

    snapshot_t = recording["snapshot_t"]
    snapshot_qpos = recording["snapshot_qpos"]
    if snapshot_qpos.shape[1] != model.nq:
        raise ValueError(f"recording qpos width {snapshot_qpos.shape[1]} != model nq {model.nq}")
    frame_index = _downsample_indices(snapshot_t, hz)
    bodies: dict[str, list[list[float]]] = {names[i]: [] for i in rigid}
    cables: dict[str, list[list[list[float]]]] = {c: [] for c in cable_ids}
    for k in frame_index:
        data.qpos[:] = snapshot_qpos[k]
        mujoco.mj_kinematics(model, data)
        for i in rigid:
            bodies[names[i]].append(pose(i))
        for cable, ids in cable_ids.items():
            cables[cable].append(_round(data.xpos[ids], 4))

    logs = [json.loads(line) for line in recording["firmware_logs"].tolist() if line] if "firmware_logs" in recording.files else []
    events = [json.loads(e) for e in recording["events"].tolist() if e] if "events" in recording.files else []
    return {
        "hz": hz, "duration": duration, "channels": sorted(k for k in series if k != "t"), "series": series,
        "firmware_logs": logs[:2000], "events": events[:2000],
        "frames": {"t": _round(snapshot_t[frame_index], 4), "format": "x y z qw qx qy qz (world frame)", "bodies": bodies,
                   "component_bodies": component_bodies, "rest": rest,
                   "cables": {c: {"rgba": rgba[c], "points": cables[c]} for c in sorted(cable_ids)}},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("scenario")
    parser.add_argument("--hz", type=float, default=30.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    payload = convert(args.project, args.scenario, args.hz)
    text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
