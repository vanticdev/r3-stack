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


def _fixed_exit_problem(move=0.0):
    from test_wiring_overhaul import _field

    fields = _field()
    fields.hardware = fields.grid.centers()[:, 0]
    points = np.array([[0.0, 0.0, 0.0], [0.06, 0.0, 0.0]])
    junctions = {
        'a': planner.Junction('a', 'via', points[0], move=move),
        'b': planner.Junction('b', 'via', points[-1]),
    }
    segment = planner.Segment('h', 0, 'a', 'b', points, ['w'], .003, .001)
    return segment, junctions, fields


def test_fixed_blocked_exit_returns_failure_at_proven_lower_bound(monkeypatch):
    minimize = planner.minimize
    methods = []

    def recorded(*args, **kwargs):
        methods.append(kwargs['method'])
        assert kwargs['method'] != 'SLSQP'
        return minimize(*args, **kwargs)

    monkeypatch.setattr(planner, 'minimize', recorded)
    segment, junctions, fields = _fixed_exit_problem()
    plan = planner.plan_bundle([segment], junctions, fields, .008)
    assert methods and not plan.feasible
    assert any(v.kind == 'hardware_clearance' and v.achieved < v.required for v in plan.violations)
    assert plan.stats['fixed_clearance_lower_bound'] > 0
    assert plan.stats['final_violation'] <= plan.stats['fixed_clearance_lower_bound'] + 1e-10
    np.testing.assert_allclose(plan.segments[0].points[0], [0, 0, 0])


def test_movable_blocked_exit_is_not_a_fixed_lower_bound():
    segment, junctions, fields = _fixed_exit_problem(move=.01)
    problem = planner._Problem([segment], junctions, fields, .008, {0: .003})
    assert problem.max_violation(problem.x0_array) > 0
    assert problem.fixed_clearance_lower_bound() == 0
