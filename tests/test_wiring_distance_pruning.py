"""AABB pruning preserves signed minima, keepouts and component attribution."""
import numpy as np
import pytest

from r3_wiring.geometry import Box, Cylinder, MeshShape, SweptShape, axis_matrix
from r3_wiring.obstacles import Obstacle, ObstacleSet


def exhaustive(points, obstacles, *, keepout=False, swept=False, exclude=(), cutoff=.2):
    best = np.full(len(points), np.inf)
    names = [''] * len(points)
    for obstacle in obstacles.obstacles:
        if obstacle.component in exclude:
            continue
        shape = obstacle.swept if swept else obstacle.shape
        value = shape.sdf_bounded(points, cutoff) - (obstacle.keepout if keepout else 0.)
        better = value < best
        best[better] = value[better]
        for index in np.flatnonzero(better):names[index] = obstacle.component
    return best, names


def obstacle(name, shape, keepout=0., swept=None):
    return Obstacle(name, 'part', shape, swept or shape, keepout, swept is not None, 'base', False)


@pytest.mark.parametrize('keepout', [False, True])
@pytest.mark.parametrize('swept', [False, True])
def test_signed_minima_and_first_winner_match_exhaustive_queries(keepout, swept):
    import trimesh
    box = Box(np.zeros(3), np.eye(3), np.array([.01, .02, .03]))
    cylinder = Cylinder(np.array([.04, 0., 0.]), axis_matrix('x'), .01, .02)
    mesh = trimesh.creation.icosphere(subdivisions=1, radius=.012)
    shape = MeshShape(mesh.vertices + [.08, 0., 0.], mesh.faces)
    moving = SweptShape([shape, shape.transformed(np.eye(3), np.array([0., .02, 0.]))])
    obstacles = ObstacleSet([obstacle('near', box), obstacle('tie', box),
                             obstacle('cylinder', cylinder, .007),
                             obstacle('mesh', shape, .003, moving)], None)
    points = np.vstack([np.random.default_rng(19).uniform(-.15, .15, (120, 3)),
                        np.array([[0., 0., 0.], [.01, 0., 0.], [.08, 0., 0.], [1., 1., 1.]])])
    for cutoff in (.005, .2):
        for exclude in ((), ('near',), ('near', 'tie')):
            expected, owners = exhaustive(points, obstacles, keepout=keepout, swept=swept,
                                           exclude=exclude, cutoff=cutoff)
            actual, names = obstacles.min_distance(points, obstacles.obstacles, keepout=keepout,
                                                   swept=swept, exclude=set(exclude), cutoff=cutoff)
            np.testing.assert_array_equal(actual, expected)
            assert names == owners


def test_far_mesh_queries_are_skipped_without_changing_the_answer(monkeypatch):
    near = Box(np.zeros(3), np.eye(3), np.array([.01, .01, .01]))
    far = Box(np.array([1., 0., 0.]), np.eye(3), np.array([.01, .01, .01]))
    obstacles = ObstacleSet([obstacle('near', near), obstacle('far', far)], None)
    points = np.array([[.011, 0., 0.], [0., 0., 0.], [.01, 0., 0.]])
    expected, owners = exhaustive(points, obstacles)
    def forbidden(*args):raise AssertionError('Far shape cannot improve the known near distance')
    monkeypatch.setattr(far, 'sdf_bounded', forbidden)
    actual, names = obstacles.min_distance(points, obstacles.obstacles)
    np.testing.assert_array_equal(actual, expected)
    assert names == owners


def test_keepout_can_make_a_farther_surface_the_minimum():
    a = Box(np.zeros(3), np.eye(3), np.ones(3) * .01)
    b = Box(np.array([.025, 0., 0.]), np.eye(3), np.ones(3) * .01)
    obstacles = ObstacleSet([obstacle('a', a), obstacle('b', b, .02)], None)
    point = np.array([[.011, 0., 0.]])
    expected, owners = exhaustive(point, obstacles, keepout=True)
    actual, names = obstacles.min_distance(point, obstacles.obstacles, keepout=True)
    np.testing.assert_array_equal(actual, expected)
    assert names == owners == ['b']
