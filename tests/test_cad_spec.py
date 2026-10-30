"""Strict qualification metadata references; shapes and assembly poses come from pull."""

import copy
from pathlib import Path

import pytest
import yaml

from r3_model.spec import RobotSpec
from r3_model.project import Project

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "cad" / "robot_world_frame_v0.yaml"


@pytest.fixture(scope="module")
def raw() -> dict:
    data = {"robot": {"name": "qualification reference fixture"},
        "components": {"base": {"type": "generic"}, "support": {"type": "generic", "mount": "base"},
            "sleeve": {"type": "generic", "mount": "support"}, "rotor": {"type": "generic", "mount": "sleeve"}},
        "mechanical": {"interfaces": [
            {"id": "sleeve_press", "kind": "press_fit", "parts": ["support", "sleeve"], "axis_point": [0,0,0], "axis_dir": [0,0,1]},
            {"id": "rotor_pivot", "kind": "revolute_fit", "parts": ["sleeve", "rotor"], "axis_point": [0,0,0], "axis_dir": [0,0,1]}],
            "joints": [{"id": "pivot", "type": "continuous", "parent": "sleeve", "child": "rotor",
                        "origin": [0,0,0], "interface": "rotor_pivot"}],
            "fasteners": [{"id": f"support_mount_{i}", "type": "generic", "position": [0,0,0],
                            "clamps": ["base"], "thread_into": "support"} for i in (1,2)]}}
    data["components"]["drive"] = {"type": "motor-540-brushed", "mount": "base"}
    data["mechanical"]["interfaces"].extend([
        {"id": kind, "kind": kind, "parts": ["support", "rotor"], "axis_point": [0, 0, 0], "axis_dir": [0, 0, 1]}
        for kind in ("drive_flat", "ball_joint", "spline", "adhesive", "strap", "magnetic_target", "connector_retention")])
    data["mechanical"]["interfaces"].append({"id": "coupling", "kind": "drive_flat", "parts": ["drive", "support"], "axis_point": [0, 0, 0], "axis_dir": [0, 0, 1]})
    data["mechanical"]["interfaces"].append({"id": "strap_link", "kind": "strap", "parts": ["base", "sleeve"]})
    data["mechanical"]["transmissions"] = [{"id": "motion", "type": "direct", "actuator": "drive", "joints": ["pivot"], "path": ["coupling", "sleeve_press", "rotor_pivot"]}]
    data["mechanical"]["fasteners"].append({"id": "clamp", "type": "shcs-m3", "length": .02,
        "position": [0, 0, 0], "clamps": ["base"], "nut": "nut-m3-nyloc", "grip": .004})
    data["mechanical"]["joints"][0].update(type="revolute", limits=[-.6, .6])
    return data


def _validate(data: dict) -> RobotSpec:
    return RobotSpec.model_validate(data)


def test_bench_spec_is_complete(raw):
    spec = _validate(raw)
    assert len(spec.mechanical.fasteners) == 3
    kinds = {i.kind for i in spec.mechanical.interfaces}
    assert {"revolute_fit", "press_fit", "drive_flat", "ball_joint", "spline", "adhesive", "strap", "magnetic_target",
            "connector_retention"} <= kinds
    for joint in spec.mechanical.joints:
        assert joint.interface, joint.id
    assert "fastener:support_mount_1" in spec.part_ids() and "nut:clamp" in spec.part_ids()


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["mechanical"]["fasteners"][0].update(thread_into="nope"), "unknown part"),
    (lambda d: d["mechanical"]["fasteners"].append(dict(d["mechanical"]["fasteners"][0])), "fastener ids must be unique"),
    (lambda d: d["mechanical"]["joints"][0].pop("interface"), "must reference the interface"),
    (lambda d: d["mechanical"]["joints"][0].update(interface="missing"), "unknown interface"),
    (lambda d: d["mechanical"]["joints"][0].update(interface="sleeve_press"), "is revolute but interface"),
    (lambda d: d["mechanical"]["transmissions"][0].update(path=["rotor_pivot", "coupling"]), "must start at actuator"),
    (lambda d: d["mechanical"]["transmissions"][0].update(path=["coupling", "strap_link"]), "path breaks"),
    (lambda d: d["components"]["base"].update(mount="rotor"), "mount cycle"),
    (lambda d: d["mechanical"]["fasteners"][-1].pop("grip"), "grip length"),
    (lambda d: d["mechanical"]["interfaces"].append({"id": "support_mount_1", "kind": "adhesive", "parts": ["base", "support"]}), "must differ"),
])
def test_invalid_mechanical_references_are_rejected(raw, mutate, message):
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(ValueError, match=message):
        _validate(data)


def test_legacy_specs_without_interfaces_stay_valid(raw):
    data = copy.deepcopy(raw)
    data["mechanical"] = {"joints": [{k: v for k, v in j.items() if k != "interface"} for j in raw["mechanical"]["joints"]],
                          "transmissions": [{k: v for k, v in t.items() if k != "path"} for t in raw["mechanical"]["transmissions"]]}
    spec = _validate(data)
    assert spec.mechanical.fasteners == [] and spec.mechanical.interfaces == []
