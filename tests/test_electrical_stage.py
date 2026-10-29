"""Run the electrical stage on the reference RC car and print its diagnostics."""

import json
import shutil

import pytest

from conftest import copy_rc_car
from r3_model.project import Project
from r3_sim.electrical.stage import run
from r3_sim.electrical.ngspice import find_library

pytestmark = pytest.mark.skipif(find_library() is None, reason="libngspice not installed")


def _run_and_check(root, routed: bool):
    project = Project.load(root)
    result = run(project)
    print(f"\nelectrical stage ok={result.ok} in {result.duration_s:.1f} s")
    for d in result.diagnostics:
        print(f"  [{d.severity.value:7}] {d.code}: {d.message}")
    build = root / "build" / "electrical"
    for name in ("operating_points.json", "power_tree.png", "wire_currents.png", "runtime.png",
                 "full_throttle_transient.png", "result.json"):
        assert (build / name).exists(), name
    document = json.loads((build / "operating_points.json").read_text())
    assert len(document["operating_points"]) == 15
    codes = {d.code for d in result.diagnostics}
    assert (root / "build" / "wiring" / "routes.json").exists() == routed
    # Without a wiring-stage run the lengths are estimates; with one they come from routes.json.
    assert ("wire_length_estimated" in codes) == (not routed)
    for d in result.diagnostics:
        assert all(isinstance(v, (int, float, dict)) or v is None for v in d.data.values()), d
    # cruise at 4 m/s must be cheap, stall must be a burst far above it
    cruise = document["operating_points"]["cruise@nominal"]["components"]["battery"]["current"]
    stall = document["operating_points"]["motor_stall@full"]["components"]["battery"]["current"]
    assert 0.5 < cruise < 5.0 and stall > 10 * cruise
    assert document["runtime"]["runtime"] > 15 * 60
    assert result.assumptions and result.inputs["robot_yaml_sha256"]
    return result


def test_electrical_stage_on_rc_car(tmp_path):
    """A fresh copy of the RC car (no build artifacts): wire lengths are estimated."""
    _run_and_check(copy_rc_car(tmp_path / "rc-car"), routed=False)


def test_electrical_stage_uses_routed_wire_lengths(rc_car_wiring, tmp_path):
    """With the wiring stage's routes.json the lengths (and installed ampacity) come from routing."""
    root = copy_rc_car(tmp_path / "rc-car")
    (root / "build" / "wiring").mkdir(parents=True)
    shutil.copy(rc_car_wiring.root / "build" / "wiring" / "routes.json", root / "build" / "wiring" / "routes.json")
    _run_and_check(root, routed=True)
