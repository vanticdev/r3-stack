"""Spec additions: fasteners, interfaces, joint interfaces, transmission paths, mount graph."""

import copy
from pathlib import Path

import pytest
import yaml

from r3_model.spec import RobotSpec

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car" / "robot.yaml"


@pytest.fixture(scope="module")
def raw() -> dict:
    return yaml.safe_load(RC_CAR.read_text())


def _validate(data: dict) -> RobotSpec:
    return RobotSpec.model_validate(data)


def test_rc_car_spec_is_complete(raw):
    spec = _validate(raw)
    assert len(spec.mechanical.fasteners) >= 60
    kinds = {i.kind for i in spec.mechanical.interfaces}
    assert {"revolute_fit", "press_fit", "drive_flat", "ball_joint", "spline", "adhesive", "strap", "magnetic_target",
            "connector_retention"} <= kinds
    for joint in spec.mechanical.joints:
        assert joint.interface, joint.id
    assert "fastener:kp_fl" in spec.part_ids() and "nut:mast_1" in spec.part_ids()


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["mechanical"]["fasteners"][0].update(thread_into="nope"), "unknown part"),
    (lambda d: d["mechanical"]["fasteners"].append(dict(d["mechanical"]["fasteners"][0])), "fastener ids must be unique"),
    (lambda d: d["mechanical"]["joints"][0].pop("interface"), "must reference the interface"),
    (lambda d: d["mechanical"]["joints"][0].update(interface="hub_fl_typo"), "unknown interface"),
    (lambda d: d["mechanical"]["joints"][0].update(interface="stub_press_fl"), "is revolute but interface"),
    (lambda d: d["mechanical"]["transmissions"][0].update(path=["output_l", "motor_pinion"]), "must start at actuator"),
    (lambda d: d["mechanical"]["transmissions"][0].update(path=["motor_pinion", "wheel_drive_rl"]), "path breaks"),
    (lambda d: d["components"]["deck"].update(mount="pi"), "mount cycle"),
    (lambda d: d["mechanical"]["fasteners"][-1].pop("grip"), "grip length"),
    (lambda d: d["mechanical"]["interfaces"].append({"id": "gb_1", "kind": "adhesive", "parts": ["esc", "chassis"]}), "must differ"),
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
