"""Signed-distance credibility (item 4): analytic fixtures for closure, winding,
units and transforms; witnesses; MuJoCo cross-check; the legacy sign bug."""

import math

import numpy as np
import pytest

from r3_wiring.geometry import Box, Cylinder, MeshShape, SweptShape, rotation_about
from r3_wiring.obstacles import Obstacle
from r3_wiring.witness import (audit_mesh, exact_closest, legacy_nearest_normal_sdf, measure, mujoco_distance)


def box_mesh(half=(0.02, 0.01, 0.005), center=(0.0, 0.0, 0.0), rotation=None, inward=False, open_face=False):
    """Closed, outward-wound triangulated box (12 triangles)."""
    hx, hy, hz = half
    v = np.array([[sx * hx, sy * hy, sz * hz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float)
    # Outward quads (vertex index = 4 * ix + 2 * iy + iz).
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    faces = []
    for a, b, c, d in quads:
        faces += [(a, b, c), (a, c, d)]
    faces = np.array(faces)
    if open_face:
        faces = faces[2:]
    if inward:
        faces = faces[:, ::-1]
    rotation = np.eye(3) if rotation is None else rotation
    return v @ rotation.T + np.asarray(center), faces


def analytic(half, center=(0, 0, 0), rotation=None):
    return Box(np.asarray(center, dtype=float), np.eye(3) if rotation is None else rotation, np.asarray(half))


QUERY = np.array([[0.05, 0.0, 0.0], [0.0, 0.03, 0.0], [0.03, 0.02, 0.01], [-0.3, -0.2, 0.15],  # outside, corners
                  [0.0, 0.0, 0.0], [0.015, 0.005, 0.002], [0.019, -0.009, 0.004]])  # inside


def test_mesh_sdf_matches_analytic_box_inside_and_outside():
    v, f = box_mesh()
    mesh, box = MeshShape(v, f, spacing=0.0005), analytic((0.02, 0.01, 0.005))
    expected = box.sdf(QUERY)
    exact = np.array([c.distance for c in exact_closest(mesh, QUERY)])
    np.testing.assert_allclose(exact, expected, atol=1e-9)  # exact point-to-triangle distance
    np.testing.assert_allclose(mesh.sdf(QUERY), expected, atol=0.0005)  # router field: sampling error only
    assert np.all(np.sign(mesh.sdf(QUERY)) == np.sign(expected))


def test_witness_points_lie_on_the_surface_and_realise_the_distance():
    v, f = box_mesh()
    mesh = MeshShape(v, f)
    for c, q in zip(exact_closest(mesh, QUERY), QUERY):
        assert abs(analytic((0.02, 0.01, 0.005)).sdf(c.point[None])[0]) < 1e-9  # on the surface
        assert abs(np.linalg.norm(q - c.point) - abs(c.distance)) < 1e-9


def test_transformed_mesh_matches_transformed_analytic_box():
    rotation = rotation_about([0.3, -0.5, 0.8], 0.7)
    center = np.array([0.21, -0.04, 0.06])
    v, f = box_mesh(center=center, rotation=rotation)
    mesh = MeshShape(v, f, spacing=0.0005)
    box = analytic((0.02, 0.01, 0.005), center, rotation)
    query = QUERY @ rotation.T + center
    np.testing.assert_allclose([c.distance for c in exact_closest(mesh, query)], box.sdf(query), atol=1e-9)
    # A rigidly moved MeshShape (samples moved, not re-sampled) gives the same field.
    moved = MeshShape(*box_mesh(), spacing=0.0005).transformed(rotation, center)
    np.testing.assert_allclose(moved.sdf(query), box.sdf(query), atol=0.0005)


def test_legacy_nearest_normal_sign_flips_at_edges_and_corners():
    """Nearest-normal signing can report distant points as deep penetrations."""
    v, f = box_mesh()
    mesh = MeshShape(v, f)
    # 180 mm in front of the end faces, a fraction of a sample step below an edge: the nearest
    # sample is an edge sample shared with the face whose plane the point is behind.
    far = np.array([[sx * 0.2, y, sz * (0.005 - off)] for y in np.linspace(-0.009, 0.009, 7)
                    for off in (1e-4, 3e-4) for sx in (-1, 1) for sz in (-1, 1)])
    truth = analytic((0.02, 0.01, 0.005)).sdf(far)
    assert np.all(truth > 0.15)
    legacy = legacy_nearest_normal_sdf(mesh, far)
    assert np.allclose(np.abs(legacy), truth, atol=0.001)  # the magnitude is right ...
    assert np.any(legacy < 0)  # ... the sign is not
    assert np.all(mesh.sdf(far) > 0)  # winding number: always right for a closed mesh


def test_audit_detects_open_inward_and_unit_errors():
    v, f = box_mesh()
    part = {"volume": 0.04 * 0.02 * 0.01, "bbox_min": (-0.02, -0.01, -0.005), "bbox_max": (0.02, 0.01, 0.005)}
    assert audit_mesh("ok", v, f, part).problems == []
    assert any("inward" in p for p in audit_mesh("inv", *box_mesh(inward=True), part).problems)
    assert any("not closed" in p for p in audit_mesh("open", *box_mesh(open_face=True), part).problems)
    assert any("units" in p for p in audit_mesh("mm", v * 1000.0, f, part).problems)
    shifted = audit_mesh("moved", v + [0.003, 0, 0], f, part)
    assert any("placement" in p for p in shifted.problems)


def test_inward_wound_mesh_flips_the_winding_sign():
    """Closure and winding matter for the sign: the audit must catch an inverted mesh."""
    mesh = MeshShape(*box_mesh(inward=True))
    assert mesh.winding_number(np.zeros((1, 3)))[0] == pytest.approx(-1.0, abs=1e-9)
    assert mesh.sdf(np.zeros((1, 3)))[0] > 0  # reported outside: wrong, hence cad_mesh_invalid


def test_cylinder_and_swept_exact_distance():
    cyl = Cylinder(np.array([0.0, 0.0, 0.0]), np.eye(3), 0.01, 0.02)
    q = np.array([[0.02, 0.0, 0.0], [0.0, 0.0, 0.05], [0.0, 0.002, 0.0], [0.03, 0.0, 0.03]])
    np.testing.assert_allclose([c.distance for c in exact_closest(cyl, q)], cyl.sdf(q), atol=1e-12)
    poses = [cyl.transformed(rotation_about([0, 1, 0], a), np.zeros(3)) for a in np.linspace(0, math.pi / 2, 10)]
    swept = SweptShape(poses)
    exact = exact_closest(swept, q)
    np.testing.assert_allclose([c.distance for c in exact], swept.sdf(q), atol=1e-12)
    assert all(0 <= c.pose < len(poses) for c in exact)


def test_mujoco_distance_agrees_with_exact_for_meshes_and_primitives():
    rotation = rotation_about([0, 0, 1], 0.4)
    v, f = box_mesh(center=(0.1, 0.0, 0.05), rotation=rotation)
    mesh = MeshShape(v, f)
    box = analytic((0.02, 0.01, 0.005), (0.1, 0.0, 0.05), rotation)
    for center, radius in (((0.16, 0.02, 0.05), 0.001), ((0.1, 0.0, 0.08), 0.002), ((0.11, 0.0, 0.05), 0.001)):
        p = np.array(center)
        truth = float(box.sdf(p[None])[0]) - radius
        for shape in (mesh, box):
            d, _ = mujoco_distance(shape, p, radius)
            if truth > 0:
                assert d == pytest.approx(truth, abs=2e-5)
            else:
                assert d < 0  # inside: MuJoCo reports a penetration


def test_measure_returns_witness_pair_and_agreement():
    v, f = box_mesh(center=(0.0, 0.0, 0.0))
    mesh = MeshShape(v, f)
    obstacle = Obstacle("block", "structure", mesh, mesh, 0.0, False, "base", True, source="cad_mesh")
    line = np.column_stack([np.linspace(-0.05, 0.05, 101), np.full(101, 0.02), np.zeros(101)])
    m = measure(obstacle, line, 0.001, swept=False)
    assert m.exact == pytest.approx(0.01 - 0.001, abs=1e-9)
    assert m.agree and m.mujoco == pytest.approx(m.exact, abs=2e-5)
    assert m.part_point[1] == pytest.approx(0.01) and m.wire_point[1] == pytest.approx(0.019)
    # A broken router value (the legacy sign) is caught as a disagreement.
    broken = measure(obstacle, line, 0.001, swept=False, router_value=-m.exact)
    assert not broken.agree


def test_unavailable_collision_witness_blocks_release_without_crashing():
    from types import SimpleNamespace
    from r3_model.diagnostics import Diagnostic
    from r3_wiring.obstacles import ObstacleSet
    from r3_wiring.witness import verify_clearances
    from r3_wiring.manufacturing import release_status
    mesh = MeshShape(*box_mesh(open_face=True))
    obstacle = Obstacle('coupon', 'structure', mesh, mesh, 0., False, 'base', False)
    endpoint = SimpleNamespace(exempt=set(), termination_length=0.)
    wire = SimpleNamespace(path=np.array([[-.03, 0., 0.], [.03, 0., 0.]]),
                           bundle_radius=np.array([.001, .001]), harness='lead', from_node='A', to_node='B')
    routing = SimpleNamespace(wires={'W': wire}, nodes={'lead': {'A': endpoint, 'B': endpoint}}, holes=[])
    diagnostic = Diagnostic.error('wire_intersects_component', 'Synthetic intersection',
                                  subjects=['lead', 'W', 'coupon'], data={'component': 'coupon', 'penetration': .002})
    report, witnesses = verify_clearances([diagnostic], ObstacleSet([obstacle], None), routing)
    finding = next(d for d in report if d.code == 'geometry_uncertain')
    assert finding.severity.value == 'error'
    assert finding.data['replaces'] == 'wire_intersects_component'
    assert finding.data['distance_mujoco'] is None
    assert not witnesses, 'Unavailable geometry must not fabricate a witness pair'
    project = SimpleNamespace(spec=SimpleNamespace(electrical=SimpleNamespace(
        harnesses=[SimpleNamespace(id='lead', wires=[SimpleNamespace(id='W')])])) )
    assert release_status(project, report)['status'] == 'unreleased'
