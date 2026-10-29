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
        {"id": "test-pack", "kind": "battery", "name": "Pack with a two-wire pigtail",
         "mechanical": {"shape": "box", "size": ["60 mm", "40 mm", "20 mm"], "mass": 0.1},
         "pins": {"POS": {"role": "power_out"}, "NEG": {"role": "ground"}},
         "connectors": {"J1": {"type": "integral", "position": ["30 mm", 0, "10 mm"], "direction": [1, 0, 0],
                               "pins": {"1": "POS", "2": "NEG"}}}},
        {"id": "test-sink", "kind": "sensor", "name": "Load with an XT30 PCB socket facing -x",
         "mechanical": {"shape": "box", "size": ["20 mm", "20 mm", "20 mm"], "mass": 0.02},
         "pins": {"POS": {"role": "power_in"}, "NEG": {"role": "ground"}},
         "connectors": {"J1": {"type": "xt30pw-male", "position": ["-10 mm", 0, "10 mm"], "direction": [-1, 0, 0],
                               "pins": {"1": "POS", "2": "NEG"}}}},
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


def write_branch_project(root: Path) -> Path:
    """Every termination kind on a plate: a pack's integral pigtail ending in a floating
    XT60 pair, a Y harness splitting at two splices to two loads with XT30 PCB sockets."""
    (root / "catalog" / "components").mkdir(parents=True, exist_ok=True)
    (root / "catalog" / "components" / "test.yaml").write_text(yaml.safe_dump(CATALOG))
    components = {
        "plate": {"type": "test-plate", "pose": {"xyz": ["100 mm", 0, 0]}},
        "pack": {"type": "test-pack", "mount": "plate", "pose": {"xyz": [0, 0, "3 mm"]}},
        "load_l": {"type": "test-sink", "mount": "plate", "pose": {"xyz": ["210 mm", "45 mm", "3 mm"]}},
        "load_r": {"type": "test-sink", "mount": "plate", "pose": {"xyz": ["210 mm", "-45 mm", "3 mm"]}},
    }
    spec = {
        "schema": "r3/robot@1",
        "robot": {"name": "wiring-branch-test"},
        "components": components,
        "mechanical": {"joints": []},
        "electrical": {"harnesses": [
            {"id": "pig",
             "connectors": {"A": {"type": "integral", "mates": "pack.J1"}, "B": {"type": "xt60-female"}},
             "wires": [{"id": "W_PIG_P", "from": "A.1", "to": "B.1", "awg": 12, "color": "red"},
                       {"id": "W_PIG_N", "from": "A.2", "to": "B.2", "awg": 12, "color": "black"}],
             "route": {"slack": "20 mm"}},
            {"id": "ysplit",
             "connectors": {"P1": {"type": "xt60-male", "mates": "pig.B"},
                            "D1": {"type": "xt30-female", "mates": "load_l.J1"},
                            "D2": {"type": "xt30-female", "mates": "load_r.J1"}},
             "splices": {"SP": {"position": ["165 mm", "8 mm", "15 mm"]}, "SN": {"position": ["165 mm", "-8 mm", "15 mm"]}},
             "wires": [{"id": "W_Y_P", "from": "P1.1", "to": "SP", "awg": 14, "color": "red"},
                       {"id": "W_Y_N", "from": "P1.2", "to": "SN", "awg": 14, "color": "black"},
                       {"id": "W_L_P", "from": "SP", "to": "D1.1", "awg": 20, "color": "red"},
                       {"id": "W_L_N", "from": "SN", "to": "D1.2", "awg": 20, "color": "black"},
                       {"id": "W_R_P", "from": "SP", "to": "D2.1", "awg": 20, "color": "red"},
                       {"id": "W_R_N", "from": "SN", "to": "D2.2", "awg": 20, "color": "black"}],
             "route": {"slack": "20 mm"}},
        ]},
    }
    (root / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return root
