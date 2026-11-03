"""Progress observation must not change feasible dense or sparse solver results."""
import logging

import numpy as np
import pytest
from scipy import sparse

import r3_wiring.planner as planner


@pytest.mark.parametrize('count', [3, 320])
def test_progress_reports_iterations_without_changing_the_constrained_solution(monkeypatch, caplog, count):
    ticks = iter(range(0, 100000, 31))
    monkeypatch.setattr(planner.time, 'monotonic', lambda: next(ticks))
    target = np.linspace(.01, .02, count)
    start = np.zeros(count)
    objective = lambda x: float((x - target) @ (x - target))
    gradient = lambda x: 2 * (x - target)
    constraints = lambda x: x - .005
    jacobian = lambda x: sparse.eye(count, format='csr')
    baseline = planner._constrained(objective, start, gradient, [(-1., 1.)] * count,
                                    constraints, jacobian, 300)
    with caplog.at_level(logging.INFO, logger=planner.__name__):
        progress = planner._PlannerProgress('synthetic', count)
        observed = planner._constrained(objective, start, gradient, [(-1., 1.)] * count,
                                        constraints, jacobian, 300, progress)
    assert baseline.success and observed.success
    assert observed.nit == baseline.nit
    np.testing.assert_array_equal(observed.x, baseline.x)
    assert constraints(observed.x).min() >= 0
    np.testing.assert_allclose(observed.x, target, atol=1e-6)
    assert progress.iterations > 0
    assert any('iteration' in record.message for record in caplog.records)
    np.testing.assert_array_equal(start, np.zeros(count))
