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
    finding = next(d for d in report.diagnostics if d.code in ("wire_too_short", "connector_strain")
                   and "steer" in d.data["joints"])
    assert finding.data["max_pin_distance"] > 0 and "steer" in finding.data["joints"]


def test_native_mesh_obstacles_ignore_stale_generated_cad(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from native_wiring_fixture import publish_wiring_pull

    root = write_project(tmp_path, block=True)
    shape = BRepPrimAPI_MakeBox(gp_Pnt(-20, -60, 0), 40, 120, 50).Shape()
    publish_wiring_pull(root, primary_shapes={"block": shape})
    stale = root / "build/cad"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "cad_model.json").write_text(json.dumps({"parts": {"block": {"mesh": "missing.stl"}}}))
    obstacles, diagnostics = build_obstacles(Project.load(root))
    block = obstacles.by_component("block")
    assert block.source == "pulled_stl" and not obstacles.estimated
    box = Box(np.array([0.1, 0.0, 0.028]), np.eye(3), np.array([0.02, 0.06, 0.025]))
    probes = np.array([[0.1, 0.0, 0.028], [0.1, 0.0, 0.07], [0.15, 0.0, 0.028]])
    assert np.allclose(block.shape.sdf(probes), box.sdf(probes), atol=0.0015)
    assert not any(d.code == "cad_geometry_estimated" for d in diagnostics)
