"""Cable slack simulation: catenary sag, long-run stability, pins on moving bodies."""

import math

import numpy as np
from scipy.optimize import brentq

from r3_model.project import Project
from r3_wiring.cable_sim import GRAVITY, CableSimulator, RodSolver
from r3_wiring.checks import run_checks
from r3_wiring.export import routes_json
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import route_project
from test_wiring_support import write_project


def test_hanging_cable_matches_catenary():
    span, length, n = 0.2, 0.26, 52
    x = np.column_stack([np.linspace(0, span, n + 1), np.zeros(n + 1), np.zeros(n + 1)])
    x[1:-1, 2] -= 1e-4 * np.sin(np.linspace(0, math.pi, n + 1)[1:-1])
    rest = np.full(n, length / n)
    mass = np.zeros(n + 1)
    mass[:-1] += 0.5 * rest * 0.01
    mass[1:] += 0.5 * rest * 0.01
    pinned = np.zeros(n + 1, bool)
    pinned[[0, -1]] = True
    solver = RodSolver(iterations=4, damping=8.0)
    solver.add_chain(x, rest, mass, 1e-9, 1e3, 0.001, pinned)  # nearly perfectly flexible
    solver.finalize()
    for _ in range(int(3.0 / 0.0005)):
        solver.substep(0.0005, lambda p, v: np.broadcast_to(GRAVITY, p.shape))
    # Catenary through both pins: s = 2 a sinh(D / 2a), z(x) = a cosh((x - D/2)/a) - a cosh(D/2a).
    a = brentq(lambda a: 2 * a * math.sinh(span / (2 * a)) - length, 1e-3, 10.0)
    xs, zs = solver.x[:, 0], solver.x[:, 2]
    analytic = a * np.cosh((xs - span / 2) / a) - a * math.cosh(span / (2 * a))
    sag = a * (math.cosh(span / (2 * a)) - 1)
    assert abs(-zs.min() - sag) / sag < 0.02
    assert np.sqrt(np.mean((zs - analytic) ** 2)) < 0.001
    assert np.abs(solver.v).max() < 1e-3  # at rest


def test_simulator_stable_10s(tmp_path):
    project = Project.load(write_project(tmp_path, block=True, slack="40 mm"))
    obstacles, _ = build_obstacles(project)
    routing = route_project(project, obstacles)
    routes = routes_json(routing, run_checks(project, obstacles, routing).clearances)
    sim = CableSimulator(project, routes, obstacles)
    start = sim.solver.x.copy()
    max_speed = 0.0
    for k in range(10000):  # 10 s at 1 ms with 0.5 g 25 Hz vibration and a 3 g bump at 5 s
        t = (k + 1) * 0.001
        a_z = 0.5 * 9.80665 * math.sin(2 * math.pi * 25 * t) + (3 * 9.80665 * math.sin(math.pi * (t - 5) / 0.02) if 5 <= t <= 5.02 else 0)
        report = sim.step(0.001, {"pos": [0, 0, 0], "quat": [1, 0, 0, 0], "lin_acc": [0, 0, a_z],
                                  "ang_vel": [0, 0, 0.5], "ang_acc": [0, 0, 0]})
        max_speed = max(max_speed, report.max_speed)
    assert np.all(np.isfinite(sim.solver.x))
    assert max_speed < 3.0
    assert report.max_strain < 0.02
    assert np.abs(sim.solver.x - start).max() < 0.05  # bounded: pinned cable cannot wander off


def test_pins_follow_moving_body(tmp_path):
    project = Project.load(write_project(tmp_path, block=False, moving_end=True, slack="60 mm"))
    obstacles, _ = build_obstacles(project)
    routing = route_project(project, obstacles)
    routes = routes_json(routing, run_checks(project, obstacles, routing).clearances)
    sim = CableSimulator(project, routes, obstacles, settle=0.1)
    wire = sim.wires[0]
    end_index, body, nominal = wire.pins[-1]
    assert body == "arm"
    rotation = np.array([[math.cos(0.3), -math.sin(0.3), 0], [math.sin(0.3), math.cos(0.3), 0], [0, 0, 1]])
    origin = np.array([0.2, -0.06, 0.013])
    p0, _ = project.graph.component_pose("arm")
    pose = {"pos": (rotation @ (p0 - origin) + origin).tolist(),
            "quat": [math.cos(0.15), 0, 0, math.sin(0.15)]}
    for _ in range(50):
        sim.step(0.001, {}, {"arm": pose, "dst": {"pos": (rotation @ (project.graph.component_pose("dst")[0] - origin) + origin).tolist(),
                                                  "quat": pose["quat"]}})
    expected = rotation @ (nominal - origin) + origin
    assert np.linalg.norm(sim.solver.x[end_index] - expected) < 1e-6
