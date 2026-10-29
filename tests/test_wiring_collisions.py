"""Housing, parent USB-block and separate bundle collision regressions."""

from types import SimpleNamespace

import numpy as np
import pytest

from r3_connectors.frames import Frame
from r3_model.project import Project
from r3_wiring.bodies import Kinematics
from r3_wiring.checks import HarnessChecker
from r3_wiring.geometry import Box, MeshShape
from r3_wiring.obstacles import Obstacle, ObstacleSet, cable_obstacles
from r3_wiring.router import HarnessRouter, NodePoint, RouterOptions
from test_wiring_support import write_project


def _wire(name, harness, path, start="A", end="B", radius=0.001):
    path = np.asarray(path, float)
    return SimpleNamespace(wire_id=name, harness=harness, path=path, from_node=start, to_node=end,
                           bundle_radius=np.full(len(path), radius), fanout=(0, 0), od=2 * radius)


def _node(harness, name, point, kind="free", **kwargs):
    return NodePoint(harness, name, kind, exit=np.asarray(point, float), **kwargs)


def _checker(tmp_path, wires, nodes, segments=None, obstacles=None):
    project = Project.load(write_project(tmp_path, block=False))
    obstacles = ObstacleSet(obstacles or [], Kinematics(project.spec))
    harnesses = {h: SimpleNamespace(segments=(segments or {}).get(h, [])) for h in nodes}
    routing = SimpleNamespace(nodes=nodes, wires={w.wire_id: w for w in wires}, holes=[], harnesses=harnesses)
    return HarnessChecker(project, obstacles, routing)


def test_inline_xt_pair_blocks_another_harness_entering_top_face(tmp_path):
    # XT30 proxy shell occupies x=+-8, y=+-4, z=-12.5..0 mm.
    frame = Frame.identity()
    half = _node("power", "XT", [0, 0, -0.0125], "floating", connector_type="xt30-male",
                 face=np.zeros(3), frame=frame, direction=np.array([0., 0., -1.]), od=0.004)
    partner = _node("battery", "XT", [0, 0, 0.012], "floating", connector_type="xt30-female",
                    face=np.zeros(3), frame=frame.mated(), direction=np.array([0., 0., 1.]), od=0.004)
    wire = _wire("WHITE", "signal", [[0, -0.02, -0.006], [0, 0.02, -0.006]])
    nodes = {"power": {"XT": half}, "battery": {"XT": partner},
             "signal": {"A": _node("signal", "A", wire.path[0]), "B": _node("signal", "B", wire.path[-1])}}
    checker = _checker(tmp_path, [wire], nodes)
    checker._clearance_and_intersection()
    finding = next(d for d in checker.diagnostics if d.code == "wire_intersects_connector" and
                   d.data["connector"] == "power.XT")
    assert finding.data["penetration"] > 0.001
    body = checker.obstacles.by_component("power.XT")
    assert isinstance(body.shape, MeshShape) and body.source == "connector_proxy"


def test_only_own_entry_zone_is_exempt_not_the_housing(tmp_path):
    node = _node("lead", "A", [0, 0, -0.0125], "connector", connector_type="xt30-male",
                 face=np.zeros(3), frame=Frame.identity(), direction=np.array([0., 0., -1.]), od=0.002)
    wire = _wire("W", "lead", [[0, -0.02, -0.006], [0, 0.02, -0.006]])
    nodes = {"lead": {"A": node, "B": _node("lead", "B", wire.path[-1])}}
    checker = _checker(tmp_path, [wire], nodes)
    checker._clearance_and_intersection()
    assert any(d.code == "wire_intersects_connector" for d in checker.diagnostics)
    wire.path = np.array([[0, 0, -0.0125], [0, 0, -0.03]])
    checker = _checker(tmp_path, [wire], nodes)
    checker._clearance_and_intersection()
    assert not checker.diagnostics


def test_jst_lead_cannot_use_hat_exemption_for_parent_usb_block(tmp_path):
    wire = _wire("RX_WHITE", "rx", [[0, 0, 0], [0.03, 0, 0]])
    node = _node("rx", "A", wire.path[0], "connector", connector_type="integral",
                 component="hat", exempt={"hat"}, lead_length=0.03)
    shape = Box(np.array([0.01, 0, 0]), np.eye(3), np.array([0.01, 0.005, 0.005]))
    block = Obstacle("pi", "computer", shape, shape, 0, False, "robot", False)
    checker = _checker(tmp_path, [wire], {"rx": {"A": node, "B": _node("rx", "B", wire.path[-1])}},
                       obstacles=[block])
    checker._clearance_and_intersection()
    finding = next(d for d in checker.diagnostics if d.code == "wire_intersects_component")
    assert finding.data["component"] == "pi" and finding.data["penetration"] > 0.005


def test_connector_inside_static_component_except_owner(tmp_path):
    node = _node("power", "XT", [0, 0, -0.0125], "floating", connector_type="xt30-male",
                 face=np.zeros(3), frame=Frame.identity(), direction=np.array([0., 0., -1.]), component="owner")
    shape = Box(np.zeros(3), np.eye(3), np.full(3, 0.03))
    parts = [Obstacle(cid, "computer", shape, shape, 0, False, "robot", False) for cid in ("owner", "block")]
    checker = _checker(tmp_path, [], {"power": {"XT": node}}, obstacles=parts)
    checker._connector_clearance()
    findings = [d for d in checker.diagnostics if d.code == "connector_intersects_component"]
    assert len(findings) == 1 and findings[0].data["component"] == "block"
    assert findings[0].data["penetration"] > 0.01


@pytest.mark.parametrize("same_harness", [False, True])
def test_crossing_bundle_surfaces_with_variable_radii(tmp_path, same_harness):
    a = _wire("W1", "first", [[-0.02, 0, 0], [0.02, 0, 0]])
    b = _wire("W2", "first" if same_harness else "second", [[0, -0.02, 0.002], [0, 0.02, 0.002]], "C", "D")
    a.bundle_radius[:] = 0.003  # the conductor alone (r=1 mm) would not collide
    nodes = {h: {} for h in (a.harness, b.harness)}
    for w in (a, b):
        nodes[w.harness].update({w.from_node: _node(w.harness, w.from_node, w.path[0]),
                                 w.to_node: _node(w.harness, w.to_node, w.path[-1])})
    checker = _checker(tmp_path, [a, b], nodes)
    checker._wire_intersections()
    finding = next(d for d in checker.diagnostics if d.code == "wire_intersects_wire")
    assert finding.data["penetration"] == pytest.approx(0.002)
    assert finding.data["radius_a"] == 0.003 and finding.data["units"] == "m"


def test_shared_core_is_one_physical_bundle(tmp_path):
    a = _wire("W1", "lead", [[-0.02, 0, 0], [0.02, 0, 0]])
    b = _wire("W2", "lead", a.path)
    nodes = {"lead": {"A": _node("lead", "A", a.path[0]), "B": _node("lead", "B", a.path[-1])}}
    shared = {"lead": [{"points": a.path, "wires": ["W1", "W2"]}]}
    checker = _checker(tmp_path, [a, b], nodes, shared)
    checker._wire_intersections()
    assert not checker.diagnostics


def test_real_splice_join_is_local_and_does_not_hide_remote_crossing(tmp_path):
    a = _wire("W1", "lead", [[0, 0, 0], [0.02, 0, 0]], "SP", "A")
    b = _wire("W2", "lead", [[0, 0, 0], [0, 0.02, 0]], "SP", "B")
    nodes = {"lead": {"SP": _node("lead", "SP", [0, 0, 0], "splice", od=0.003),
                      "A": _node("lead", "A", a.path[-1]), "B": _node("lead", "B", b.path[-1])}}
    checker = _checker(tmp_path, [a, b], nodes)
    checker._wire_intersections()
    checker._clearance_and_intersection()
    assert not checker.diagnostics
    b.path = np.array([[0, 0, 0], [0.02, 0.02, 0], [0.02, -0.02, 0]])
    b.bundle_radius = np.full(3, 0.001)
    checker = _checker(tmp_path, [a, b], nodes)
    checker._wire_intersections()
    assert any(d.code == "wire_intersects_wire" for d in checker.diagnostics)


@pytest.mark.parametrize("incident", [True, False])
def test_splice_entry_admits_its_bundle_surface_only_for_incident_wires(tmp_path, incident):
    # A large bundle still touches its own sleeve after its centreline leaves
    # the entry zone. The entry aperture must include that bundle's radius.
    wire = _wire("W", "lead", [[0, 0, 0], [0.0072, 0, 0]], "SP" if incident else "A", "B", radius=0.004)
    nodes = {"lead": {"SP": _node("lead", "SP", [0, 0, 0], "splice", od=0.008),
                      "A": _node("lead", "A", wire.path[0]), "B": _node("lead", "B", wire.path[-1])}}
    checker = _checker(tmp_path, [wire], nodes)
    checker._clearance_and_intersection()
    assert bool(checker.diagnostics) is not incident


def test_router_cable_field_opens_only_incident_entry(tmp_path):
    project = Project.load(write_project(tmp_path, block=False))
    from r3_wiring.obstacles import build_obstacles

    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    node = _node("power", "XT", [0.1, 0, 0.030], "floating", connector_type="xt30-male",
                 face=np.array([0.1, 0, 0.0425]), frame=Frame(np.eye(3), np.array([0.1, 0, 0.0425])),
                 direction=np.array([0., 0., -1.]), od=0.004)
    nodes = {"power": {"XT": node}}
    own, foreign = router.cable_field(nodes, {"power"}), router.cable_field(nodes, {"signal"})
    body = cable_obstacles(project, nodes)[0]
    centers = router.grid.centers()
    inside = body.shape.sdf_bounded(centers, 0.005) < -0.0005
    assert inside.any() and np.all(foreign[inside] < 0)
    assert np.all(own[inside & ~body.entry_mask(centers)] < 0)


def test_planner_does_not_relax_foreign_hardware_in_a_termination_zone():
    from r3_wiring.grid import VoxelGrid
    from r3_wiring.planner import ClearanceField, Junction, Segment, plan_bundle

    grid = VoxelGrid(np.array([-0.03, -0.03, -0.03]), (31, 31, 31), 0.003)
    free = np.full(grid.size, 0.05)
    block = Box(np.zeros(3), np.eye(3), np.full(3, 0.006))
    fields = ClearanceField(grid, free, free, free, hardware=block.sdf(grid.centers()))
    points = np.array([[0., 0., 0.], [0.02, 0., 0.]])
    junctions = {"a": Junction("a", "connector", points[0], exempt={"owner"}, termination_length=0.02),
                 "b": Junction("b", "free", points[-1])}
    segment = Segment("lead", 0, "a", "b", points, ["W"], 0.002, 0.004)
    plan = plan_bundle([segment], junctions, fields, 0.008, max_iterations=10)
    assert not plan.feasible
    assert any(v.kind == "hardware_clearance" and v.achieved < 0 for v in plan.violations)


def test_planning_group_excludes_unrelated_floating_pairs(small_wiring):
    import copy
    from r3_wiring.planner import build_problem
    from r3_wiring.router import FloatingMate

    routing = copy.deepcopy(small_wiring.bundle["routing"])
    routing.floating.append(FloatingMate("other.A", "third.B", "xt30-male", "xt30-female",
                                          np.array([0.1, 0, 0.05]), np.array([1., 0., 0.]), 0.025, 0.015, 0))
    _, junctions = build_problem(small_wiring.project, small_wiring.bundle["router"], routing, ["lead"])
    assert "F:other.A|third.B" not in junctions


def test_router_routes_large_bundles_first_and_reserves_their_surfaces(tmp_path):
    from r3_model.spec import Harness
    from r3_wiring.obstacles import build_obstacles

    project = Project.load(write_project(tmp_path, block=False))
    def harness(hid, awg, points):
        return Harness.model_validate({"id": hid, "connectors": {
            "A": {"type": "integral", "position": points[0]}, "B": {"type": "integral", "position": points[1]}},
            "wires": [{"id": f"{hid}_{n}", "from": f"A.{n}", "to": f"B.{n}", "awg": awg}
                      for n in (1, 2)]})
    project.spec.electrical.harnesses = [
        harness("thin", 28, [[0.1, -0.06, 0.045], [0.1, 0.06, 0.045]]),
        harness("thick", 12, [[0.03, 0, 0.045], [0.17, 0, 0.045]])]
    # Rebuild the graph after replacing the synthetic authored harnesses.
    project.__dict__.pop("_graph", None)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    routing = router.route()
    assert routing.stats["route_order"] == ["thick", "thin"]
    assert not any(w.failed for w in routing.wires.values())
    checker = HarnessChecker(project, obstacles, routing)
    checker._wire_intersections()
    assert not checker.diagnostics


@pytest.mark.parametrize("code", ["wire_intersects_connector", "connector_intersects_component", "wire_intersects_wire"])
def test_new_collision_codes_block_manufacturing_release(tmp_path, code):
    from r3_model.diagnostics import Diagnostic
    from r3_wiring.manufacturing import release_status

    project = Project.load(write_project(tmp_path, block=False))
    release = release_status(project, [Diagnostic.error(code, "collision", subjects=["lead", "W1"],
                                                        data={"penetration": 0.002, "units": "m"})])
    assert release["status"] == "unreleased"
    assert not release["harnesses"]["lead"]["valid"]
    assert not release["wires"]["W1"]["valid"]
    assert code in release["blocking_codes"]
