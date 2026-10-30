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
import sys
from pathlib import Path
from typing import Any


def convert(project_root: Path, scenario: str, hz: float = 30.0) -> dict[str, Any]:
    # Share the production decoder, including conductor mapping, fan-outs and
    # qpos0 body frames. The mock viewer must exercise the same playback contract.
    from r3_orchestrator.api.projects import decode_recording

    npz = project_root / "build" / "sim" / scenario / "recording.npz"
    return decode_recording(str(npz), npz.stat().st_mtime_ns, hz, True)


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
