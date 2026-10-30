"""A nearby round tie post remains usable when a plate is closer to the cable."""

import numpy as np

from r3_wiring.geometry import Box, Cylinder
from r3_wiring.obstacles import Obstacle, ObstacleSet
from r3_wiring.supports import Post, _candidates, load_catalog


def test_round_post_is_considered_when_plate_is_nearest():
    plate_shape = Box(np.array([0.0, 0.0, -0.003]), np.eye(3), np.array([0.1, 0.1, 0.003]))
    rotation = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    post_shape = Cylinder(np.array([0.0, 0.010, 0.006]), rotation, 0.0035, 0.04)
    plate = Obstacle('plate', 'structure', plate_shape, plate_shape, 0.0, False, 'base', True, plate_axis=2)
    post = Obstacle('post', 'structure', post_shape, post_shape, 0.0, False, 'base', True)
    obstacles = ObstacleSet([plate, post], None)
    points = np.array([[-0.03, 0.0, 0.006], [0.03, 0.0, 0.006]])
    types = tuple(t for t in load_catalog()[0] if t.kind == 'post_tie')
    posts = {'post': [Post('post', np.array([-0.04, 0.010, 0.006]), np.array([1.0, 0.0, 0.0]), 0.0035, 0.08)]}
    candidates = _candidates(points, 0.06, 0.001, [plate, post], obstacles, types, 0.002, 0.01, 0.06, [], posts)
    assert candidates
    assert {c['host'].component if hasattr(c['host'], 'component') else c['host'] for c in candidates} == {'post'}
    assert all(c['checks']['tie_loop']['ok'] for c in candidates)
