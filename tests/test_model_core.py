from pathlib import Path

import pytest

from r3_model.project import Project
from r3_model.units import to_si

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


def test_units():
    assert to_si("25 mm") == pytest.approx(0.025)
    assert to_si("5000 mAh") == pytest.approx(18000.0)
    assert to_si("90 deg") == pytest.approx(1.5707963)
    assert to_si(3) == 3.0


def test_rc_car_graph_has_no_errors():
    project = Project.load(RC_CAR)
    assert project.graph.diagnostics == []


def test_bindings_reach_devices():
    project = Project.load(RC_CAR)
    graph = project.graph
    for binding in project.spec.software.bindings.values():
        expects = binding.expects if len(binding.expects) == len(binding.pins) else binding.expects * len(binding.pins)
        for pin, target in zip(binding.pins, expects):
            assert graph.connected(pin, target, through_internal=True), (pin, target)


def test_fuse_separates_hat_from_battery_net():
    graph = Project.load(RC_CAR).graph
    assert graph.connected("battery.POS", "hat.VBAT", through_internal=True)
    assert not graph.connected("battery.POS", "hat.VBAT")
