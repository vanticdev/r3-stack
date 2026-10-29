"""Convert a scenario's ``recording.npz`` into the web playback JSON.

The API contract (``GET /api/projects/{pid}/sim/{scenario}?hz=30``) returns a
downsampled recording: the scalar time series plus per-frame world poses of
every MuJoCo body, so the browser can replay the vehicle without MuJoCo. The
recording stores raw ``qpos`` snapshots, so world poses come from MuJoCo's own
forward kinematics (``mj_kinematics``) on the scenario's ``vehicle.xml``.

Cable bodies (``cableNNsK_B_*``) are returned as polylines per bundle; bundle
metadata (harness, wires, colors) comes from ``r3_wiring.mujoco_cables.plan_cables``
when the R3 packages are importable, and is omitted otherwise.

Usage (shared venv, repository root on PYTHONPATH as for the r3 CLI):
    python apps/web/scripts/convert_recording.py examples/robots/rc-car full_throttle --hz 30 [--out file.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

CABLE_BODY = re.compile(r"^(cable\d+)(s\d+)_B_(first|last|\d+)$")
POSITION_DECIMALS = 5  # 10 µm
QUAT_DECIMALS = 5


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


def _cable_order(suffix: str) -> int:
    return -1 if suffix == "first" else 10**9 if suffix == "last" else int(suffix)


def _bundle_meta(project_root: Path) -> dict[str, dict[str, Any]]:
    try:
        from r3_model.project import Project
        from r3_wiring.mujoco_cables import plan_cables
    except ImportError:
        return {}
    routes_path = project_root / "build" / "wiring" / "routes.json"
    if not routes_path.exists():
        return {}
    try:
        plan = plan_cables(Project.load(project_root), json.loads(routes_path.read_text()))
    except Exception as error:  # metadata is optional; playback still works without it
        print(f"warning: cable metadata unavailable ({error})", file=sys.stderr)
        return {}
    return {b.id: {"harness": b.harness, "wires": list(b.wires), "colors": list(b.colors), "od": float(b.od)}
            for b in plan.bundles}


def convert(project_root: Path, scenario: str, hz: float = 30.0) -> dict[str, Any]:
    import mujoco

    scenario_dir = project_root / "build" / "sim" / scenario
    recording = np.load(scenario_dir / "recording.npz", allow_pickle=False)
    summary_path = scenario_dir / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}

    times = recording["t"]
    series_index = _downsample_indices(times, hz)
    scalar_keys = [k for k in recording.files
                   if recording[k].ndim == 1 and recording[k].shape == times.shape and recording[k].dtype.kind == "f"]
    series = {k: _round(recording[k][series_index], 6) for k in scalar_keys}

    model = mujoco.MjModel.from_xml_path(str(scenario_dir / "vehicle.xml"))
    data = mujoco.MjData(model)
    names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i) or f"body{i}" for i in range(model.nbody)]
    rigid = [i for i, n in enumerate(names) if i > 0 and not CABLE_BODY.match(n)]
    cable_bodies: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for i, name in enumerate(names):
        match = CABLE_BODY.match(name)
        if match:
            bundle, span, suffix = match.groups()
            cable_bodies.setdefault(bundle, {}).setdefault(span, []).append((_cable_order(suffix), i))

    # Nominal pose: forward kinematics at qpos0 (the q = 0 robot-frame layout the meshes are authored in).
    data.qpos[:] = model.qpos0
    mujoco.mj_kinematics(model, data)
    nominal = {names[i]: _round(np.concatenate([data.xpos[i], data.xquat[i]]), POSITION_DECIMALS) for i in rigid}

    snapshot_t = recording["snapshot_t"]
    snapshot_qpos = recording["snapshot_qpos"]
    if snapshot_qpos.shape[1] != model.nq:
        raise ValueError(f"recording qpos width {snapshot_qpos.shape[1]} != model nq {model.nq}")
    frame_index = _downsample_indices(snapshot_t, hz)
    poses: dict[str, list[list[float]]] = {names[i]: [] for i in rigid}
    cable_points: dict[str, list[list[list[float]]]] = {b: [] for b in cable_bodies}
    for k in frame_index:
        data.qpos[:] = snapshot_qpos[k]
        mujoco.mj_kinematics(model, data)
        for i in rigid:
            poses[names[i]].append(np.round(np.concatenate([data.xpos[i], np.round(data.xquat[i], QUAT_DECIMALS)]),
                                            POSITION_DECIMALS).tolist())
        for bundle, spans in cable_bodies.items():
            ordered = [i for span in sorted(spans) for _, i in sorted(spans[span])]
            cable_points[bundle].append(_round(data.xpos[ordered], POSITION_DECIMALS))

    meta = _bundle_meta(project_root) if cable_bodies else {}
    cables = [{"id": bundle, **meta.get(bundle, {}), "points": cable_points[bundle]} for bundle in sorted(cable_bodies)]
    logs = [json.loads(line) for line in recording["firmware_logs"].tolist() if line] if "firmware_logs" in recording.files else []
    events = [json.loads(e) for e in recording["events"].tolist() if e] if "events" in recording.files else []
    return {
        "schema": "r3/web-playback@1",
        "scenario": scenario,
        "hz": hz,
        "duration": float(summary.get("duration", float(times[-1]))),
        "series": {"t": _round(times[series_index], 6), **{k: v for k, v in series.items() if k != "t"}},
        "bodies": {"names": [names[i] for i in rigid], "nominal": nominal,
                   "t": _round(snapshot_t[frame_index], 6), "poses": poses},
        "cables": cables,
        "firmware_logs": logs,
        "events": events,
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
