"""Router geometry: obstacle avoidance, bend radius, cut length, CAD obstacles, moving ends,
and the router -> planner pipeline keeping every conductor on its pins."""

import json
import math

import numpy as np
import pytest

from r3_model.project import Project
from r3_wiring.checks import run_checks
from r3_wiring.geometry import Box
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import HarnessRouter, RouterOptions, route_project
from r3_wiring.smoothing import radius_at_scale
from test_wiring_support import write_project


@pytest.fixture(scope="module")
def routed_blocked(tmp_path_factory):
    """Voxel router alone (``plan=False``): keepouts are a router search constraint."""
    project = Project.load(write_project(tmp_path_factory.mktemp("blocked"), block=True))
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    return project, obstacles, router, router.route()


@pytest.fixture(scope="module")
def blocked(routed_blocked):
    project, obstacles, _, routing = routed_blocked
    return project, obstacles, routing, run_checks(project, obstacles, routing)


@pytest.fixture(scope="module")
def planned(small_wiring):
    """Router + planner (two passes, the second through cable supports) on the same layout."""
    bundle = small_wiring.bundle
    return (small_wiring.project, bundle["obstacles"], bundle["routing"], bundle["planning"],
            {w: (path, bundle["routing"].wires[w].fanout) for w, path in bundle["before"].items()})


def test_planner_keeps_pin_fanouts_and_plans_from_breakouts(planned):
    project, obstacles, routing, report, before = planned
    block = obstacles.by_component("block")
    assert report.plans and report.stats and report.stats["first_pass"]
    cores = []
    for wire in routing.wires.values():
        path, (head, tail) = before[wire.wire_id]
        assert wire.fanout == (head, tail) and head > 0 and tail > 0
        # Fan-outs (pin exit up to the breakout) are unchanged; both ends stay on their pins.
        assert np.array_equal(wire.path[: head + 1], path[: head + 1])
        assert np.array_equal(wire.path[len(wire.path) - tail - 1:], path[len(path) - tail - 1:])
        for end, point in zip(wire.ends, (wire.path[0], wire.path[-1])):
            assert np.linalg.norm(point - end.position) < 1e-4
        core = wire.path[head: len(wire.path) - tail]
        assert np.allclose(core, wire.core)
        cores.append(core)
        # The planned core leaves each breakout along the fan-out's arrival tangent.
        for k in (0, 1):
            pts = core if k == 0 else core[::-1]
            fan_end = wire.path[head - 1] if k == 0 else wire.path[len(wire.path) - tail]
            arrival = pts[0] - fan_end
            leave = pts[1] - pts[0]
            assert arrival @ leave / (np.linalg.norm(arrival) * np.linalg.norm(leave)) > math.cos(math.radians(20))
        # The planner holds raw geometry + STATIC_MARGIN at its (trilinear-field) vertices; chords stay clear.
        dense = np.vstack([np.linspace(a, b, 20) for a, b in zip(wire.path[:-1], wire.path[1:])])
        assert (block.shape.sdf(dense) - wire.bundle_radius.max()).min() > 0.0
    assert all(c.shape == cores[0].shape and np.allclose(c, cores[0]) for c in cores[1:])


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
    gauge = project.catalog.wire(22)
    factor, od = gauge.min_bend_radius_factor, gauge.outer_diameter
    for wire in routing.wires.values():
        # This fixture is an unjacketed group: the innermost conductor must
        # retain factor * wire OD, with the bundle offset added at its core.
        required = factor * od + (2 * wire.bundle_radius.max() - od) / 2
        radius = radius_at_scale(wire.path)  # independent geometric measurement of the final path
        assert radius.min() >= 0.95 * factor * od, (wire.wire_id, radius.min(), factor * od)
        assert radius_at_scale(wire.core).min() >= 0.95 * required
        assert all(b.radius >= 0.98 * b.required for b in wire.bends)


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
