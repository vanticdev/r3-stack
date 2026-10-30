"""Do not run a dense elastic solve after finding a fully feasible cable."""
import numpy as np
import r3_wiring.planner as planner
from test_wiring_overhaul import _corner


def test_buildable_penalty_solution_skips_elastic_solver_without_skipping_audit(monkeypatch):
    minimize = planner.minimize
    methods = []
    def recorded(*args, **kwargs):
        methods.append(kwargs['method'])
        assert kwargs['method'] != 'SLSQP'
        return minimize(*args, **kwargs)
    monkeypatch.setattr(planner, 'minimize', recorded)
    plan = _corner(.012)
    assert methods and plan.feasible and not plan.violations
    assert plan.stats['slsqp_iterations'] == 0
    assert planner.vertex_radii(plan.segments[0].points).min() >= .98 * .012
    np.testing.assert_allclose(plan.segments[0].points[[0, -1]], [[0, .02, 0], [.06, .08, 0]])
