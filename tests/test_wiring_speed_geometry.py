"""Accelerated queries retain exhaustive distances, witness ordering and cutoffs."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial import cKDTree

from r3_wiring.geometry import Box, Cylinder, MeshShape, SweptShape, rotation_about
from r3_wiring.grid import FIELD_CUTOFF, VoxelGrid, compute_fields
from r3_wiring.obstacles import Obstacle, ObstacleSet
from r3_wiring.planner import CROSSING_GAP, occupancy_field
from r3_wiring.witness import _mesh_closest, closest_on_triangles
from test_wiring_witness import box_mesh


def _shapes():
    box = Box(np.zeros(3), np.eye(3), np.array([.02, .01, .005]))
    cylinder = Cylinder(np.array([.03, 0., 0.]), np.eye(3), .012, .02)
    mesh = MeshShape(*box_mesh(center=(.01, .02, 0.)))
    inward = MeshShape(*box_mesh(inward=True, center=(-.02, .01, 0.)))
    return [box, cylinder, mesh, inward] + [
        mesh.transformed(rotation_about([0, 1, 0], a), np.array([0., 0., .015]))
        for a in np.linspace(-.5, .5, 11)]


@pytest.mark.parametrize('cutoff', [None, .005, .05, .2])
def test_swept_broad_phase_equals_exhaustive_signed_minimum(cutoff):
    shapes = _shapes()
    points = np.vstack([np.random.default_rng(1).uniform(-.08, .08, (500, 3)),
                        np.zeros((1, 3)), shapes[2].vertices])
    swept = SweptShape(shapes)
    if cutoff is None:
        expected = np.min([s.sdf(points) for s in shapes], axis=0)
        actual = swept.sdf(points)
    else:
        expected = np.minimum(np.min([s.sdf_bounded(points, cutoff) for s in shapes], axis=0), cutoff)
        actual = swept.sdf_bounded(points, cutoff)
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('keepout', [False, True])
def test_obstacle_minimum_preserves_distances_and_first_name_at_cutoff(keepout):
    shapes = _shapes()
    obstacles = [Obstacle(str(i), 'structure', s, s, i * .0001, False, 'base', True)
                 for i, s in enumerate(shapes)]
    points = np.random.default_rng(2).uniform(-.5, .5, (500, 3))
    values = np.asarray([o.shape.sdf_bounded(points, .05) - (o.keepout if keepout else 0.) for o in obstacles])
    expected = values.min(axis=0)
    names = [obstacles[i].component for i in values.argmin(axis=0)]
    actual, which = ObstacleSet(obstacles, None).min_distance(points, obstacles, keepout=keepout, cutoff=.05)
    np.testing.assert_array_equal(actual, expected)
    assert which == names


def test_indexed_closest_points_match_original_exhaustive_sphere_filter():
    import trimesh

    model = trimesh.creation.icosphere(subdivisions=2, radius=.02)
    mesh = MeshShape(model.vertices, model.faces)
    points = np.vstack([np.random.default_rng(3).uniform(-.03, .03, (100, 3)), mesh.vertices])
    tri = mesh.vertices[mesh.faces]
    centers = tri.mean(axis=1)
    radii = np.linalg.norm(tri - centers[:, None], axis=2).max(axis=1)
    upper, _ = mesh._tree.query(points)
    expected = []
    for p, u in zip(points, upper):
        near = np.nonzero(np.linalg.norm(centers - p, axis=1) - radii <= u + 1e-12)[0]
        q = closest_on_triangles(p, tri[near, 0], tri[near, 1], tri[near, 2])
        expected.append(q[np.argmin(np.linalg.norm(q - p, axis=1))])
    for _ in range(2):
        _, actual = _mesh_closest(mesh, points)
        np.testing.assert_array_equal(actual, expected)


def test_grouped_occupancy_equals_original_per_run_fields():
    from r3_wiring.bundles import SEPARATION, TERMINATION_ZONE

    grid = VoxelGrid(np.array([-.03, -.03, -.03]), (21, 21, 21), .006)
    rng = np.random.default_rng(4)
    occupied = [(rng.uniform(-.02, .02, (15, 3)), r, {cat})
                for r in (.001, .002) for cat in ('signal', 'power') for _ in range(3)]
    occupied += occupied[:2]  # duplicate shared cores
    exits = [np.zeros(3)]
    centers = grid.centers()
    terminal = np.linalg.norm(centers, axis=1) <= TERMINATION_ZONE
    expected = np.full(grid.size, FIELD_CUTOFF)
    for points, radius, cats in occupied:
        separation = max([SEPARATION.get(frozenset({'motor', c}), 0.) for c in cats] + [0.])
        near = np.all((centers >= points.min(axis=0) - FIELD_CUTOFF) &
                      (centers <= points.max(axis=0) + FIELD_CUTOFF), axis=1)
        d = np.full(grid.size, FIELD_CUTOFF)
        d[near], _ = cKDTree(points).query(centers[near], distance_upper_bound=FIELD_CUTOFF)
        margin = np.where(terminal, 0., separation) if separation else 0.
        np.minimum(expected, np.minimum(d, FIELD_CUTOFF) - radius - margin - CROSSING_GAP, out=expected)
    actual = occupancy_field(grid, occupied, {'motor'}, termination_exits=exits)
    np.testing.assert_array_equal(actual, expected)


def test_voxel_cache_uses_geometry_grid_and_cutoff_not_component_names(tmp_path, monkeypatch):
    shape = _shapes()[2]
    obstacle = Obstacle('fixture', 'structure', shape, shape, .002, False, 'base', True)
    obstacles = ObstacleSet([obstacle], None)
    grid = VoxelGrid(np.array([-.03, -.03, -.03]), (11, 11, 11), .006)
    original = compute_fields(grid, obstacles, {'fixture'}, cache_dir=tmp_path)
    sdf = shape.sdf_bounded
    calls = []

    def recorded(*args):
        calls.append(True)
        return sdf(*args)

    monkeypatch.setattr(shape, 'sdf_bounded', recorded)
    actual = compute_fields(grid, obstacles, {'fixture'}, cache_dir=tmp_path)
    assert not calls
    np.testing.assert_array_equal(original.static_raw, actual.static_raw)
    np.testing.assert_array_equal(original.by_component['fixture'], actual.by_component['fixture'])
    grid.voxel = .007
    compute_fields(grid, obstacles, cache_dir=tmp_path)
    assert calls


@pytest.mark.parametrize('radius', [0., .0005, .003, .025, .05, .06])
def test_installation_envelope_matches_original_predicate_and_blocker(radius):
    from r3_wiring.supports import _envelope

    shapes = _shapes()
    swept = SweptShape(shapes)
    obstacles = [Obstacle('sweep', 'structure', swept, swept, .002, True, 'base', True)] + [
        Obstacle(str(i), 'structure', s, s, .002, False, 'base', True) for i, s in enumerate(shapes)]
    rng = np.random.default_rng(11)
    for points in [rng.uniform(-.1, .1, (20, 3)) for _ in range(10)]:
        expected = (True, '')
        for o in obstacles:
            if o.component == '0':
                continue
            shape = o.swept if o.moving else o.shape
            if float(shape.sdf_bounded(points, .05).min()) < radius:
                expected = (False, o.component)
                break
        assert _envelope(points, ObstacleSet(obstacles, None), {'0'}, radius) == expected
