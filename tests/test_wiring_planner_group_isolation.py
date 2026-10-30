"""Planning a signal lead must not relocate another group's power connectors."""
from types import SimpleNamespace

import numpy as np

from r3_catalog import load_catalog
from r3_wiring.planner import build_problem
from r3_wiring.router import FloatingMate


def test_unrelated_floating_pair_is_not_a_signal_group_decision_variable():
    catalog = load_catalog()
    harnesses = [SimpleNamespace(id=name, jacket="none", route=SimpleNamespace(via=[]))
                 for name in ("battery", "power", "signal")]
    project = SimpleNamespace(catalog=catalog, spec=SimpleNamespace(electrical=SimpleNamespace(harnesses=harnesses)))
    pair = FloatingMate("battery.B", "power.A", "xt60-female", "xt60-male",
                        np.array([0.2, 0.03, 0.04]), np.array([1.0, 0.0, 0.0]), 0.033, 0.016, 0.0)
    routing = SimpleNamespace(floating=[pair], wires={}, holes=[],
                              harnesses={h.id: SimpleNamespace(segments=[], holes=[]) for h in harnesses},
                              nodes={h.id: {} for h in harnesses})
    router = SimpleNamespace(opt=SimpleNamespace(voxel=0.003))
    _, signal_junctions = build_problem(project, router, routing, ["signal"])
    assert signal_junctions == {}
    _, power_junctions = build_problem(project, router, routing, ["battery", "power"])
    assert set(power_junctions) == {"F:battery.B|power.A"}
    np.testing.assert_array_equal(power_junctions["F:battery.B|power.A"].position, pair.center)
