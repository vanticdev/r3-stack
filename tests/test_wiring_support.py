"""Synthetic projects for the wiring tests (written to a temporary directory)."""

from __future__ import annotations

from pathlib import Path

import yaml

CATALOG = {
    "components": [
        {"id": "test-plate", "kind": "structure", "name": "Test base plate",
         "mechanical": {"shape": "box", "size": ["320 mm", "160 mm", "3 mm"], "mass": 0.3}},
        {"id": "test-block", "kind": "sensor", "name": "Obstacle block",
         "mechanical": {"shape": "box", "size": ["40 mm", "80 mm", "50 mm"], "mass": 0.1}},
        {"id": "test-arm", "kind": "structure", "name": "Steered arm",
         "mechanical": {"shape": "box", "size": ["20 mm", "20 mm", "20 mm"], "mass": 0.02}},
        {"id": "test-box", "kind": "sensor", "name": "Box with a 3-pin header",
         "mechanical": {"shape": "box", "size": ["20 mm", "20 mm", "20 mm"], "mass": 0.02},
         "pins": {"S": {"role": "gpio"}, "V": {"role": "power_in"}, "G": {"role": "ground"}},
         "connectors": {"J1": {"type": "header-1x3-2.54", "position": ["10 mm", 0, "10 mm"], "direction": [1, 0, 0],
                               "pins": {"1": "S", "2": "V", "3": "G"}}}},
        {"id": "test-box-rev", "kind": "sensor", "name": "Box with a 3-pin header facing -x",
         "mechanical": {"shape": "box", "size": ["20 mm", "20 mm", "20 mm"], "mass": 0.02},
         "pins": {"S": {"role": "gpio"}, "V": {"role": "power_in"}, "G": {"role": "ground"}},
         "connectors": {"J1": {"type": "header-1x3-2.54", "position": ["-10 mm", 0, "10 mm"], "direction": [-1, 0, 0],
                               "pins": {"1": "S", "2": "V", "3": "G"}}}},
    ]
}


def _wires(awg: int = 22) -> list[dict]:
    return [{"id": f"W{n}", "from": f"A.{n}", "to": f"B.{n}", "awg": awg, "color": c}
            for n, c in ((1, "white"), (2, "red"), (3, "black"))]


def write_project(root: Path, *, block: bool = True, moving_end: bool = False, slack: str = "20 mm") -> Path:
    """Two boxes 200 mm apart on a plate, optionally separated by a tall block.
    With ``moving_end`` the sink box is mounted on an arm steered +-0.8 rad
    about a vertical axis 60 mm away."""
    (root / "catalog" / "components").mkdir(parents=True, exist_ok=True)
    (root / "catalog" / "components" / "test.yaml").write_text(yaml.safe_dump(CATALOG))
    components = {
        "plate": {"type": "test-plate", "pose": {"xyz": ["100 mm", 0, 0]}},
        "src": {"type": "test-box", "mount": "plate", "pose": {"xyz": [0, 0, "3 mm"]}},
    }
    joints = []
    if moving_end:
        components["arm"] = {"type": "test-arm", "mount": "plate", "pose": {"xyz": ["200 mm", "-60 mm", "3 mm"]}}
        components["dst"] = {"type": "test-box-rev", "mount": "arm", "pose": {"xyz": ["200 mm", 0, "3 mm"]}}
        joints.append({"id": "steer", "type": "revolute", "parent": "plate", "child": "arm",
                       "origin": ["200 mm", "-60 mm", "13 mm"], "axis": [0, 0, 1], "limits": [-0.8, 0.8]})
    else:
        components["dst"] = {"type": "test-box-rev", "mount": "plate", "pose": {"xyz": ["200 mm", 0, "3 mm"]}}
    if block:
        components["block"] = {"type": "test-block", "mount": "plate", "pose": {"xyz": ["100 mm", 0, "3 mm"]}}
    spec = {
        "schema": "r3/robot@1",
        "robot": {"name": "wiring-test"},
        "components": components,
        "mechanical": {"joints": joints},
        "electrical": {"harnesses": [{
            "id": "lead",
            "connectors": {"A": {"type": "servo-3-female", "mates": "src.J1"},
                           "B": {"type": "servo-3-female", "mates": "dst.J1"}},
            "wires": _wires(),
            "route": {"slack": slack},
        }]},
    }
    (root / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return root
