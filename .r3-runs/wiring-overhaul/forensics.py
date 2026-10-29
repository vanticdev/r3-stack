"""Reproduce the review packet's clearance findings (commit 9c33004) on a given CAD build.

Usage: python .r3-runs/wiring-overhaul/forensics.py <project dir with build/cad>
Prints, at every point the packet reported, the legacy nearest-normal SDF, the winding-number
SDF and the exact distance, plus the CAD mesh audit."""

import json
import sys
from pathlib import Path

import numpy as np

from r3_model.project import Project
from r3_wiring.obstacles import build_obstacles
from r3_wiring.witness import audit_cad, exact_closest, legacy_nearest_normal_sdf

PACKET = Path(__file__).resolve().parents[1] / "review-wiring" / "result.json"


def main(root: str) -> None:
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    old = json.loads(PACKET.read_text())
    print(f"{'harness':13s} {'component':11s} {'packet':>8s} {'legacy':>8s} {'winding':>8s} {'exact':>8s}  (mm, part surface)")
    for d in old["diagnostics"]:
        if d["code"] != "wire_clearance_violation":
            continue
        o = obstacles.by_component(d["data"]["component"])
        p = np.asarray([d["data"]["position"]])
        exact = exact_closest(o.swept, p)[0].distance
        print(f"{d['subjects'][0]:13s} {o.component:11s} {d['data']['min_distance'] * 1e3:8.1f} "
              f"{legacy_nearest_normal_sdf(o.swept, p)[0] * 1e3:8.1f} {o.swept.sdf(p)[0] * 1e3:8.1f} {exact * 1e3:8.1f}")
    _, audit = audit_cad(project, obstacles)
    print("mesh audit:", {k: v for k, v in audit.items() if k != "parts"})


if __name__ == "__main__":
    main(sys.argv[1])
