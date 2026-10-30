"""Large generic constrained routing stays sparse and retains physical audits."""
from types import SimpleNamespace

import numpy as np
from scipy import sparse

import r3_wiring.planner as planner
from test_wiring_overhaul import _corner


def test_large_constrained_system_uses_native_sparse_solver():
    count = 320
    target = np.linspace(.01, .02, count)
    result = planner._constrained(lambda x: float((x - target) @ (x - target)), np.zeros(count),
        lambda x: 2 * (x - target), [(-1., 1.)] * count,
        lambda x: x - .005, lambda x: sparse.eye(count, format='csr'), 300)
    assert result.success, result.message
    assert np.min(result.x) >= .005
    np.testing.assert_allclose(result.x, target, atol=1e-6)


def test_sparse_routing_fallback_keeps_curvature_endpoints_and_audit(monkeypatch):
    minimize = planner.minimize
    monkeypatch.setattr(planner, 'DENSE_SLSQP_MAX_VARIABLES', 0)

    def fail_penalty(fun, x, *args, **kwargs):
        # Exercise a real fallback after an unproductive initial solve.
        if kwargs['method'] == 'L-BFGS-B':
            return SimpleNamespace(x=x.copy(), nit=0)
        return minimize(fun, x, *args, **kwargs)

    monkeypatch.setattr(planner, 'minimize', fail_penalty)
    plan = _corner(.012)
    assert plan.stats['elastic_solver'] == 'trust-constr'
    assert plan.feasible, [v.as_dict() for v in plan.violations]
    assert not plan.violations
    assert planner.vertex_radii(plan.segments[0].points).min() >= .98 * .012
    np.testing.assert_allclose(plan.segments[0].points[[0, -1]], [[0, .02, 0], [.06, .08, 0]])
