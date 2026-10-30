"""A branch without pins must not consume half the pin fan-out's available run."""
import numpy as np
from r3_connectors.pins import PinEnd
from r3_wiring.router import HarnessRouter, NodePoint, fan_curve, curvature_radius


def test_one_ended_fanout_uses_available_straight_run_without_shrinking_bend_limit():
    node = NodePoint('power', 'controller', 'connector', exit=np.zeros(3),
                     direction=np.array([0., 1., 0.]), fan_length=.0207)
    points = np.array([[0., 0., 0.], [0., .022, 0.]])
    end = PinEnd('power.controller', '1', np.array([.0025, 0., 0.]),
                 node.direction, np.array([.0025, 0., 0.]), 'solder_cup')
    single = HarnessRouter._breakout(node, points, opposite_fanout=False)
    shared = HarnessRouter._breakout(node, points, opposite_fanout=True)
    radius = lambda bo: np.min(curvature_radius(np.vstack([
        fan_curve(end, bo['position'], bo['direction']), bo['position']])))
    assert radius(single) >= .0132  # unchanged physical minimum for 14 AWG
    assert radius(shared) < .0132  # the former false allocation produces a real bad curve
    assert shared['arc'] * 2 < .022  # two actual pin fan-outs still cannot overlap
