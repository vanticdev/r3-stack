"""Wiring overhaul (items 5-9): curvature-constrained planning, supports as hardware,
cut length and slack, bundles and derating, the manufacturing package and the release gate."""

import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from r3_model.project import Project
from r3_wiring.bundles import ambient_factor, analyze_bundles, bundle_factor, category, sharp_edges
from r3_wiring.cable_sim import CableSimulator
from r3_wiring.cutlength import cut_tolerance, pull_out_limit, termination
from r3_wiring.geometry import Box, MeshShape
from r3_wiring.grid import VoxelGrid
from r3_wiring.manufacturing import release_status, write_package
from r3_wiring.planner import ClearanceField, Junction, Segment, bundle_bend_radius, plan_bundle, trilinear, vertex_radii
from r3_wiring.supports import load_catalog, max_unsupported_span
from test_wiring_witness import box_mesh

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"


# ------------------------------------------------------------------ planner
def _field(block: Box | None = None, room: Box | None = None) -> ClearanceField:
    """Free space, minus ``block``, and (optionally) only inside ``room``."""
    grid = VoxelGrid(np.array([-0.05, -0.05, -0.05]), (51, 51, 34), 0.003)
    centers = grid.centers()
    static = np.minimum(block.sdf(centers), 0.05) if block is not None else np.full(grid.size, 0.05)
    if room is not None:
        static = np.minimum(static, -room.sdf(centers))
    return ClearanceField(grid, static, np.full(grid.size, 0.05), static)


def _corner(radius: float, block: Box | None = None, room: Box | None = None):
    pts = np.array([[0.0, 0.02, 0.0], [0.06, 0.02, 0.0], [0.06, 0.08, 0.0]])
    junctions = {"a": Junction("a", "connector", pts[0], np.array([1.0, 0, 0]), lead=0.005, termination_length=0.01,
                               exempt={"x"}),
                 "b": Junction("b", "connector", pts[-1], np.array([0, -1.0, 0]), lead=0.005, termination_length=0.01,
                               exempt={"y"})}
    seg = Segment("h", 0, "a", "b", pts, ["w1", "w2"], 0.003, radius, 1.0, 1.0)
    return plan_bundle([seg], junctions, _field(block, room), 0.008)


def test_trilinear_gradient_matches_finite_differences():
    field = _field(Box(np.array([0.02, 0.0, 0.0]), np.eye(3), np.array([0.01, 0.01, 0.02])))
    p = np.array([[0.0047, 0.0131, 0.0023], [0.0351, -0.0062, 0.0113]])  # inside voxel cells (not on faces)
    value, grad = trilinear(field.grid, field.static, p)
    for axis in range(3):
        d = np.zeros(3)
        d[axis] = 1e-6
        fd = (trilinear(field.grid, field.static, p + d)[0] - trilinear(field.grid, field.static, p - d)[0]) / 2e-6
        np.testing.assert_allclose(grad[:, axis], fd, atol=1e-6)


def test_planner_turns_a_sharp_corner_into_a_buildable_bend():
    plan = _corner(0.012)
    assert plan.feasible, [v.as_dict() for v in plan.violations]
    p = plan.segments[0].points
    assert vertex_radii(p).min() >= 0.98 * 0.012  # the routed polyline had a 90 deg corner (radius 0)
    np.testing.assert_allclose(p[0], [0.0, 0.02, 0.0])
    assert np.dot(p[2] - p[1], [1.0, 0, 0]) > 0.99 * np.linalg.norm(p[2] - p[1])  # leaves along the exit axis


def test_planner_reports_an_infeasible_bend_with_its_constraint():
    # A 90 mm radius cannot turn 90 deg inside an 80 x 80 mm room around the two exits.
    room = Box(np.array([0.03, 0.05, 0.0]), np.eye(3), np.array([0.04, 0.04, 0.015]))
    plan = _corner(0.09, room=room)
    assert not plan.feasible
    kinds = {v.kind for v in plan.violations}
    assert kinds & {"curvature", "tangent"}
    v = next(v for v in plan.violations if v.kind in ("curvature", "tangent"))
    assert v.required == pytest.approx(0.09, rel=1e-6) and v.achieved < v.required and v.action
    # An infeasible bend must remain a local engineering finding. Failed
    # elastic iterations previously won by stretching a motor lead to 192 m,
    # making the downstream 10 mm-resolution MuJoCo model unconstructible.
    grid = _field().grid
    points = np.vstack([segment.points for segment in plan.segments])
    assert np.all(points >= grid.origin - 1e-9)
    assert np.all(points <= grid.origin + (np.asarray(grid.shape) - 1) * grid.voxel + 1e-9)


def test_bundle_bend_radius_rule():
    catalog = Project.load(BENCH).catalog
    od12 = float(catalog.wire(12).outer_diameter)
    assert bundle_bend_radius(catalog, [12], od12, False) == pytest.approx(4 * od12)
    # Loose pair: the inner conductor keeps 4 x its own OD (SAE AS50881: largest wire).
    assert bundle_bend_radius(catalog, [12, 12], 2 * od12, False) == pytest.approx(4 * od12 + od12 / 2)
    assert bundle_bend_radius(catalog, [12, 12], 2 * od12, True) == pytest.approx(8 * od12)


# ------------------------------------------------------------------ supports
def test_support_catalog_and_span_limit():
    types, ties = load_catalog()
    kinds = {t.kind for t in types}
    assert kinds >= {"adhesive_tie_mount", "screw_tie_mount", "p_clip", "post_tie"}
    assert {t.motion for t in types} == {"pinned", "clamped"}
    assert ties["plt1m"]["loop_tensile"] == pytest.approx(80.0)
    # delta = w L^4 / (384 EI): the returned span sags exactly the allowed 3 mm at 4 g.
    ei, m = 1.8e-5, 0.018
    span = max_unsupported_span(ei, m, cap=10.0)
    assert m * 9.80665 * 4.0 * span ** 4 / (384 * ei) == pytest.approx(0.003, rel=1e-9)
    assert max_unsupported_span(4e-4, 0.09) == pytest.approx(0.1)  # capped


# ------------------------------------------------------------------ cut length / terminations
def test_terminations_and_pull_limits():
    assert termination("jst-xh-3-housing").contact["mpn"] == "SXH-001T-P0.6"
    assert termination(None).method == "splice"
    assert pull_out_limit(22) == pytest.approx(35.6) and pull_out_limit(12) > pull_out_limit(18)
    assert cut_tolerance(0.2) == 0.003 and cut_tolerance(0.5) == 0.005


# ------------------------------------------------------------------ bundles
def test_derating_factors():
    assert bundle_factor(1, 12) == 1.0
    assert bundle_factor(2, 12) == pytest.approx(30 / 40)  # NEC 310.16 / 310.17 at 90 C
    assert bundle_factor(5, 12) == pytest.approx(0.75 * 0.8)  # NEC 310.15(C)(1), 4-6 conductors
    assert ambient_factor(25.0) == pytest.approx(1.0)
    assert ambient_factor(40.0) == pytest.approx(math.sqrt(15 / 30))
    assert category("MOTOR_A", 14) == "motor" and category("VBAT", 12) == "power" and category("GPS_TX", 28) == "signal"


def test_sharp_edges_of_a_box_mesh():
    mesh = MeshShape(*box_mesh())
    assert len(sharp_edges(mesh)) == 12  # the 12 box edges; face diagonals are flat


# ------------------------------------------------------------------ end to end (small project)
@pytest.fixture(scope="module")
def small(small_wiring):
    return small_wiring.project, small_wiring.bundle


def test_small_project_is_planned_supported_and_measured(small):
    project, bundle = small
    routing = bundle["routing"]
    for wire in routing.wires.values():
        bad = [b for b in wire.bends if b.radius < 0.98 * b.required]
        assert not bad, wire.wire_id
    cuts = bundle["cuts"]
    for wire_id, cut in cuts.items():
        route = routing.wires[wire_id]
        assert route.cut_length == pytest.approx(cut.nominal)
        assert cut.nominal > cut.centreline + cut.end_allowance
        assert cut.ends[0]["contact"] and cut.ends[0]["strip"] > 0  # servo crimp contact from the catalog
    routes = bundle["routes"]
    assert set(routes) >= {"wires", "harnesses", "clearances"}
    w = next(iter(routes["wires"].values()))
    assert {"installed_ampacity", "derating", "cut"} <= set(w)
    assert "supports" in next(iter(routes["harnesses"].values()))


def test_release_gate_and_manufacturing_package(small, tmp_path):
    from r3_model.diagnostics import Diagnostic

    project, bundle = small
    ok = release_status(project, [])
    assert ok["status"] == "released" and all(v["valid"] for v in ok["wires"].values())
    blocked = release_status(project, [Diagnostic.error("wire_bend_radius_violation", "x", subjects=["lead", "W1"])])
    assert blocked["status"] == "unreleased" and not blocked["harnesses"]["lead"]["valid"]
    assert not blocked["wires"]["W1"]["valid"]
    manifest = write_package(project, bundle["routing"], bundle["cuts"], bundle["supports"], bundle["bundles"],
                             blocked, tmp_path)
    assert manifest["status"] == "unreleased" and not manifest["errors"]
    for name in ("lead.wireviz.yaml", "lead.svg", "lead.png", "bom.csv", "cut_list.csv", "continuity_test.csv",
                 "assembly_sequence.md", "tooling.csv", "labels.csv", "branch_dimensions.csv", "manifest.json"):
        assert (tmp_path / name).stat().st_size > 0, name
    rows = list(csv.DictReader((tmp_path / "cut_list.csv").open()))
    assert rows and all(r["release"] == "UNRELEASED" for r in rows)
    bom = list(csv.DictReader((tmp_path / "bom.csv").open()))
    assert {"housing", "contact", "wire"} <= {r["section"] for r in bom}
    tests = list(csv.DictReader((tmp_path / "continuity_test.csv").open()))
    assert {r["expect"] for r in tests} == {"continuity", "isolation"}
    assert "UNRELEASED" in (tmp_path / "assembly_sequence.md").read_text()
    assert json.loads((tmp_path / "manifest.json").read_text())["harnesses"]["lead"]["valid"] is False


def test_pinned_support_becomes_ball_and_connect_in_mujoco(small):
    project, bundle = small
    routes = json.loads(json.dumps(bundle["routes"]))
    path = np.asarray(routes["wires"][next(iter(routes["wires"]))]["path"])
    mid = path[len(path) // 2].tolist()
    for h in routes["harnesses"].values():
        h["tie_points"] = [mid]
        h["supports"] = [{"id": "S1", "position": mid, "permitted_motion": "pinned"}]
    sim = CableSimulator(project, routes, bundle["obstacles"])
    kinds = {int(sim.model.eq_type[i]) for i in range(sim.model.neq)
             if (mujoco.mj_id2name(sim.model, mujoco.mjtObj.mjOBJ_EQUALITY, i) or "").endswith("_pin")}
    assert int(mujoco.mjtEq.mjEQ_CONNECT) in kinds and int(mujoco.mjtEq.mjEQ_WELD) in kinds
    for _ in range(300):
        report = sim.step(0.001, {"pos": [0, 0, 0], "quat": [1, 0, 0, 0], "lin_acc": [0, 0, 0]})
    assert report.max_strain < 0.001  # the pinned support holds position
    tension = sim.monitor.end_tension()
    assert tension.shape[1] == 2 and np.all(np.isfinite(tension)) and np.all(tension < 5.0)


def test_bundle_occupancy_detects_interference_and_segregation():
    """A motor pair laid 1.5 mm from a GPS lead: the bundles overlap and share a corridor."""
    catalog = Project.load(BENCH).catalog
    a = np.column_stack([np.linspace(0, 0.1, 50), np.zeros(50), np.zeros(50)])
    b = a + [0.0, 0.0015, 0.0]
    wires = {"w1": SimpleNamespace(path=a, harness="h1"), "w2": SimpleNamespace(path=b, harness="h2")}
    routing = SimpleNamespace(
        harnesses={"h1": SimpleNamespace(segments=[{"points": a, "wires": ["w1"], "od": 0.0033}]),
                   "h2": SimpleNamespace(segments=[{"points": b, "wires": ["w2"], "od": 0.0010}])},
        wires=wires, nodes={})
    project = SimpleNamespace(catalog=catalog, spec=SimpleNamespace(
        components={}, simulation=SimpleNamespace(environment=SimpleNamespace(ambient_temperature_c=25.0)),
        electrical=SimpleNamespace(harnesses=[
            SimpleNamespace(id="h1", wires=[SimpleNamespace(id="w1", signal="MOTOR_A", awg=14)]),
            SimpleNamespace(id="h2", wires=[SimpleNamespace(id="w2", signal="GPS_TX", awg=28)])])))
    report = analyze_bundles(project, SimpleNamespace(obstacles=[], static=[]), routing)
    codes = {d.code for d in report.diagnostics}
    assert "harness_interference" in codes and "emc_segregation_violation" in codes
    overlap = next(d for d in report.diagnostics if d.code == "harness_interference")
    assert overlap.data["overlap"] == pytest.approx(0.0033 / 2 + 0.0005 - 0.0015, abs=2e-4)
    assert len(overlap.data["witness"]) == 2
    assert report.derating["w1"]["installed_ampacity"] == pytest.approx(
        float(catalog.wire(14).ampacity) * ambient_factor(30.0))  # alone (one current-carrying conductor), +5 K
