from pathlib import Path

import pytest

from r3_model.project import Project
from r3_model.units import to_si

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"


def test_units():
    assert to_si("25 mm") == pytest.approx(0.025)
    assert to_si("5000 mAh") == pytest.approx(18000.0)
    assert to_si("90 deg") == pytest.approx(1.5707963)
    assert to_si(3) == 3.0


def test_millihenry_is_equivalent_to_microhenry_and_base_si():
    assert to_si('1 mH') == pytest.approx(to_si('1000 uH'))
    assert to_si('1 mH') == pytest.approx(to_si('0.001 H'))
    with pytest.raises(ValueError, match='unknown unit'):
        to_si('1 mh')


def test_bench_graph_has_no_errors():
    project = Project.load(BENCH)
    assert project.graph.diagnostics == []


def test_bindings_reach_devices():
    project = Project.load(BENCH)
    graph = project.graph
    for binding in project.spec.software.bindings.values():
        expects = binding.expects if len(binding.expects) == len(binding.pins) else binding.expects * len(binding.pins)
        for pin, target in zip(binding.pins, expects):
            assert graph.connected(pin, target, through_internal=True), (pin, target)


def test_fuse_separates_hat_from_battery_net():
    graph = Project.load(BENCH).graph
    assert graph.connected("battery.POS", "controller.VBAT", through_internal=True)
    assert not graph.connected("battery.POS", "controller.VBAT")
