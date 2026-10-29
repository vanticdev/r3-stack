"""Router geometry: obstacle avoidance, bend radius, cut length, CAD obstacles, moving ends."""

import json
import math

import numpy as np
import pytest

from r3_model.project import Project
from r3_wiring.checks import run_checks
from r3_wiring.geometry import Box
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import route_project
from r3_wiring.smoothing import radius_at_scale
from test_wiring_support import write_project


@pytest.fixture(scope="module")
def blocked(tmp_path_factory):
    project = Project.load(write_project(tmp_path_factory.mktemp("blocked"), block=True))
    obstacles, _ = build_obstacles(project)
    routing = route_project(project, obstacles)
    return project, obstacles, routing, run_checks(project, obstacles, routing)


def test_router_avoids_obstacle(blocked):
    project, obstacles, routing, report = blocked
    block = obstacles.by_component("block")
    for wire in routing.wires.values():
        assert not wire.failed
        dense = np.vstack([np.linspace(a, b, 20) for a, b in zip(wire.path[:-1], wire.path[1:])])
        clearance = block.shape.sdf(dense) - wire.bundle_radius.max()
        # The wire surface never enters the block, and stays out of its keepout (small sampling tolerance).
        assert clearance.min() >= block.keepout - 0.0005, clearance.min()
        straight = np.linalg.norm(wire.path[-1] - wire.path[0])
        assert wire.routed_length > straight + 0.02  # it had to go around or over
    assert not [d for d in report.diagnostics if d.code == "wire_intersects_component"]


def test_bend_radius_enforced(blocked):
    project, obstacles, routing, report = blocked
    assert not [d for d in report.diagnostics if d.code == "wire_bend_radius_violation"]
    factor = project.catalog.wire(22).min_bend_radius_factor
    for wire in routing.wires.values():
        required = factor * 2 * wire.bundle_radius.max()
        radius = radius_at_scale(wire.path)  # independent geometric measurement of the final path
        assert radius.min() >= 0.95 * required, (wire.wire_id, radius.min(), required)
        assert min(b.radius for b in wire.bends) >= 0.98 * required


def test_cut_length_covers_route(blocked):
    project, obstacles, routing, report = blocked
    slack = project.spec.electrical.harnesses[0].route.slack
    for wire in routing.wires.values():
        assert wire.cut_length >= wire.routed_length + slack
        assert wire.termination_allowance > 0
        assert math.isclose(wire.cut_length, wire.routed_length + slack + wire.termination_allowance)


def test_bundle_shares_one_path(blocked):
    _, _, routing, _ = blocked
    # Conductors of one bundle share its core exactly and fan out to their own pins at the ends.
    cores = [w.core for w in routing.wires.values()]
    assert all(c.shape == cores[0].shape and np.allclose(c, cores[0]) for c in cores[1:])
    for w in routing.wires.values():
        for end, point in zip(w.ends, (w.path[0], w.path[-1])):
            if end is not None:
                assert np.linalg.norm(point - end.position) < 1e-9
    assert routing.harnesses["lead"].tie_points, "ties expected along the plate"


def test_moving_endpoint_too_short(tmp_path):
    project = Project.load(write_project(tmp_path, block=False, moving_end=True, slack="5 mm"))
    obstacles, _ = build_obstacles(project)
    assert obstacles.by_component("dst").moving
    routing = route_project(project, obstacles)
    report = run_checks(project, obstacles, routing)
    codes = {d.code for d in report.diagnostics}
    assert "wire_too_short" in codes or "connector_strain" in codes
    finding = next(d for d in report.diagnostics if d.code in ("wire_too_short", "connector_strain"))
    assert finding.data["max_pin_distance"] > 0 and "steer" in finding.data["joints"]


def test_cad_mesh_obstacles(tmp_path):
    root = write_project(tmp_path, block=True)
    meshes = root / "build" / "cad" / "meshes"
    meshes.mkdir(parents=True)
    box = Box(np.array([0.1, 0.0, 0.028]), np.eye(3), np.array([0.02, 0.06, 0.025]))
    corners = box.corners()
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    lines = ["solid block"]
    for a, b, c, d in quads:
        for tri in ((a, b, c), (a, c, d)):
            p = corners[list(tri)]
            n = np.cross(p[1] - p[0], p[2] - p[0])
            if n @ (p.mean(axis=0) - box.center) < 0:
                p = p[::-1]
            lines += ["facet normal 0 0 0", " outer loop"] + [f"  vertex {v[0]} {v[1]} {v[2]}" for v in p] + [" endloop", "endfacet"]
    (meshes / "block.stl").write_text("\n".join(lines + ["endsolid block"]))
    (root / "build" / "cad" / "cad_model.json").write_text(json.dumps({"parts": {"block": {"mesh": "meshes/block.stl"}}}))
    obstacles, diagnostics = build_obstacles(Project.load(root))
    block = obstacles.by_component("block")
    assert block.source == "cad_mesh"
    probes = np.array([[0.1, 0.0, 0.028], [0.1, 0.0, 0.07], [0.15, 0.0, 0.028]])
    assert np.allclose(block.shape.sdf(probes), box.sdf(probes), atol=0.0015)
    estimated = next(d for d in diagnostics if d.code == "cad_geometry_estimated")
    assert "block" not in estimated.subjects
