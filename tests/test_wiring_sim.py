"""Cables as MuJoCo elastic cables: catenary, bending stiffness, 10 s stability, pins on
moving bodies, contact-based clearance vs mj_geomDistance, and the RC-car steering sweep."""

import math

import mujoco
import numpy as np
import pytest
from scipy.optimize import brentq

from r3_model.project import Project
from r3_wiring.bodies import ROOT
from r3_wiring.cable_sim import CableSimulator
from r3_wiring.checks import run_checks
from r3_wiring.driver import drive_joints
from r3_wiring.export import routes_json
from r3_wiring.geometry import Box, MeshShape
from r3_wiring.mujoco_cables import ANCHOR_SOLIMP, ANCHOR_SOLREF, CABLE_PLUGIN, CableBundle, CableSpan, _damping
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import route_project
from test_wiring_support import write_project

G = 9.80665


def _bundle(vertices: np.ndarray, od: float, ei: float, mass_per_m: float, dt: float = 0.001) -> CableBundle:
    length = float(np.mean(np.linalg.norm(np.diff(vertices, axis=0), axis=1)))
    span = CableSpan("c", vertices, ROOT, ROOT, _damping(ei, mass_per_m, od / 2, length, dt))
    total = length * span.segments
    return CableBundle("c", "h", ["w"], ["red"], od, ei, mass_per_m, total, total, [span], (ROOT, ROOT))


def _single_cable(vertices: np.ndarray, bundle: CableBundle, initial: str, end: str, gravity: float = -G) -> str:
    """One MuJoCo cable composite (R3's plugin parameters) between two fixed anchors."""
    span = bundle.spans[0]
    tip = np.linalg.norm(vertices[-1] - vertices[-2])
    vertex = " ".join(f"{v:.9g}" for v in vertices.ravel())
    stiff = f'solref="{ANCHOR_SOLREF}" solimp="{ANCHOR_SOLIMP}"'
    end_constraint = (f'<connect body1="cB_last" body2="b" anchor="{tip:.9g} 0 0" {stiff}/>' if end == "pin"
                      else f'<weld body1="cB_last" body2="b" {stiff}/>')
    return f"""<mujoco><option timestep="0.001" integrator="implicitfast" gravity="0 0 {gravity}"/>
<extension><plugin plugin="{CABLE_PLUGIN}"/></extension>
<worldbody><body name="a"><composite type="cable" prefix="c" vertex="{vertex}" initial="{initial}">
<plugin plugin="{CABLE_PLUGIN}"><config key="twist" value="{bundle.shear_modulus:.9g}"/>
<config key="bend" value="{bundle.young_modulus:.9g}"/><config key="flat" value="true"/><config key="vmax" value="0"/></plugin>
<joint kind="main" damping="{span.damping:.9g}"/>
<geom type="capsule" size="{bundle.radius:.9g}" density="{bundle.density:.9g}" contype="0" conaffinity="0"/>
</composite></body><body name="b" pos="{vertices[-1][0]:.9g} {vertices[-1][1]:.9g} {vertices[-1][2]:.9g}"/></worldbody>
<equality>{end_constraint}</equality></mujoco>"""


def _vertices(model: mujoco.MjModel, data: mujoco.MjData, segment: float) -> np.ndarray:
    ids = [i for i in range(model.nbody) if (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, i) or "").startswith("cB_")]
    tip = data.xpos[ids[-1]] + data.xmat[ids[-1]].reshape(3, 3)[:, 0] * segment
    return np.vstack([data.xpos[ids], tip])


def test_unequal_spans_preserve_cut_wire_mass():
    from dataclasses import replace

    short = np.column_stack([np.linspace(0, .03, 7), np.zeros((7, 2))])
    long = np.column_stack([np.linspace(0, .4, 9), np.zeros((9, 2))])
    bundle = _bundle(short, od=.003, ei=1e-5, mass_per_m=.02)
    other = _bundle(long, od=.003, ei=1e-5, mass_per_m=.02).spans[0]
    bundle.spans.append(other)
    bundle.free_length = .44  # discretized slack curve is slightly shorter than the cut wire
    mass = 0.0
    for span in bundle.spans:
        ordered = replace(bundle, spans=[span, *[s for s in bundle.spans if s is not span]])
        model = mujoco.MjModel.from_xml_string(_single_cable(span.vertices, ordered, initial="none", end="clamp"))
        mass += sum(model.body_mass)
    assert mass == pytest.approx(bundle.mass_per_m * bundle.free_length, rel=1e-7)  # MJCF uses 9 significant digits


def test_hanging_cable_matches_catenary():
    # A nearly perfectly flexible cable pinned at both ends hangs as a catenary:
    # s = 2 a sinh(D / 2a), z(x) = a cosh((x - D/2)/a) - a cosh(D/2a).
    span, length, n = 0.2, 0.26, 52
    # Start from an upward arc of the right length (not the answer).
    angle = brentq(lambda t: span / (2 * math.sin(t)) * 2 * t - length, 1e-6, 3.1)
    radius = span / (2 * math.sin(angle))
    theta = np.linspace(-angle, angle, n + 1)
    vertices = np.column_stack([span / 2 + radius * np.sin(theta), np.zeros(n + 1),
                                radius * np.cos(theta) - radius * math.cos(angle)])
    bundle = _bundle(vertices, od=0.001, ei=1e-9, mass_per_m=0.01)
    model = mujoco.MjModel.from_xml_string(_single_cable(vertices, bundle, initial="ball", end="pin"))
    data = mujoco.MjData(model)
    damping = model.dof_damping.copy()
    model.dof_damping[:] = damping * 50  # damped pre-roll, as in r3_wiring.mujoco_cables.settle
    for _ in range(4000):
        mujoco.mj_step(model, data)
    model.dof_damping[:] = damping
    for _ in range(2000):
        mujoco.mj_step(model, data)
    assert data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number == 0
    assert sum(model.body_mass) == pytest.approx(0.01 * length, rel=0.01)  # capsule density carries m' l per segment
    points = _vertices(model, data, length / n)
    a = brentq(lambda a: 2 * a * math.sinh(span / (2 * a)) - length, 1e-3, 10.0)
    analytic = a * np.cosh((points[:, 0] - span / 2) / a) - a * math.cosh(span / (2 * a))
    sag = a * (math.cosh(span / (2 * a)) - 1)
    print(f"catenary sag {sag * 1e3:.2f} mm, MuJoCo {-points[:, 2].min() * 1e3:.2f} mm, "
          f"rms {np.sqrt(np.mean((points[:, 2] - analytic) ** 2)) * 1e3:.3f} mm")
    assert abs(-points[:, 2].min() - sag) / sag < 0.02
    assert np.sqrt(np.mean((points[:, 2] - analytic) ** 2)) < 0.001
    assert np.abs(data.qvel).max() < 0.05  # at rest


def test_cantilever_matches_catalog_bending_stiffness():
    # Small-deflection cantilever under its own weight: delta = m' g L^4 / (8 EI). The rigid clamped
    # first segment shortens the beam, so the discrete answer converges as ~(1 - 2 l / L).
    length, n, od, ei, mass_per_m = 0.1, 40, 0.002, 2e-4, 0.0045
    vertices = np.column_stack([np.linspace(0, length, n + 1), np.zeros(n + 1), np.zeros(n + 1)])
    bundle = _bundle(vertices, od, ei, mass_per_m)
    xml = _single_cable(vertices, bundle, initial="none", end="free")
    xml = xml[:xml.index("<equality>")] + "</mujoco>"
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    for _ in range(6000):
        mujoco.mj_step(model, data)
    tip = -_vertices(model, data, length / n)[-1, 2]
    theory = mass_per_m * G * length ** 4 / (8 * ei)
    print(f"cantilever tip {tip * 1e6:.1f} um vs Euler-Bernoulli {theory * 1e6:.1f} um")
    assert 1 - 2.5 / n < tip / theory < 1.02


def _routes(project: Project):
    obstacles, _ = build_obstacles(project)
    routing = route_project(project, obstacles)
    return obstacles, routes_json(routing, run_checks(project, obstacles, routing).clearances)


def test_simulator_stable_10s(tmp_path):
    project = Project.load(write_project(tmp_path, block=True, slack="40 mm"))
    obstacles, routes = _routes(project)
    sim = CableSimulator(project, routes, obstacles)
    start = sim.positions()
    max_speed = max_pin = 0.0
    for k in range(10000):  # 10 s at 1 ms: 0.5 g 25 Hz vibration, a 3 g bump at 5 s, yawing at 0.5 rad/s
        t = (k + 1) * 0.001
        a_z = 0.5 * G * math.sin(2 * math.pi * 25 * t) + (3 * G * math.sin(math.pi * (t - 5) / 0.02) if 5 <= t <= 5.02 else 0)
        report = sim.step(0.001, {"pos": [0, 0, 0], "quat": [1, 0, 0, 0], "lin_acc": [0, 0, a_z],
                                  "ang_vel": [0, 0, 0.5], "ang_acc": [0, 0, 0]})
        max_speed, max_pin = max(max_speed, report.max_speed), max(max_pin, report.max_strain)
    end = sim.positions()
    assert sim.data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number == 0
    assert all(np.all(np.isfinite(p)) for p in end.values())
    assert max_speed < 3.0
    assert max_pin < 0.001  # pins (connector clamps) give less than 1 mm
    assert max(np.abs(end[w] - start[w]).max() for w in end) < 0.05  # bounded: pinned cables cannot wander off


def test_pins_follow_moving_body(tmp_path):
    project = Project.load(write_project(tmp_path, block=False, moving_end=True, slack="60 mm"))
    obstacles, routes = _routes(project)
    sim = CableSimulator(project, routes, obstacles, settle_time=0.1)
    nominal = sim.positions()["W1"][-1].copy()  # the dst end sits on the steered arm
    origin = np.array([0.2, -0.06, 0.013])
    for _ in range(300):
        sim.step(0.001, joints={"steer": 0.3})
    rotation = np.array([[math.cos(0.3), -math.sin(0.3), 0], [math.sin(0.3), math.cos(0.3), 0], [0, 0, 1]])
    expected = rotation @ (nominal - origin) + origin
    assert np.linalg.norm(sim.positions()["W1"][-1] - expected) < 0.001
    # The same joint value recovered from component poses (the CableSimulator.step interface).
    p0, r0 = project.graph.component_pose("arm")
    from r3_wiring.geometry import matrix_to_quat
    pose = {"pos": (rotation @ (p0 - origin) + origin).tolist(), "quat": matrix_to_quat(rotation @ r0).tolist()}
    assert sim.joints_from_poses({}, {"arm": pose})["steer"] == pytest.approx(0.3, abs=1e-9)


@pytest.fixture(scope="module")
def rc_car(rc_car_wiring):
    return rc_car_wiring.project, rc_car_wiring.bundle["obstacles"], rc_car_wiring.bundle["routes"]


def test_rc_car_steering_sweep(rc_car):
    """Lock-to-lock steering with spinning wheels on the shaker: stable, and every clearance is a
    physical surface distance (the PBD-era kernel reported -0.2 m from a frame/sign bug)."""
    project, obstacles, routes = rc_car
    sim = CableSimulator(project, routes, obstacles)
    limit = sim.kin.steering_limit()
    worst = math.inf
    for k in range(1000):
        t = (k + 1) * 0.001
        steer = limit * math.sin(2 * math.pi * t)
        report = sim.step(0.001, joints=drive_joints(sim.kin, steer, 36.0 * t))
        worst = min(worst, min(d for d, _ in report.min_clearance.values()))
    history = sim.min_history
    print("RC car sweep worst clearance:", round(worst * 1e3, 2), "mm;",
          {w: (round(d * 1e3, 2), c) for w, (d, c, _) in sorted(history.items(), key=lambda kv: kv[1][0])[:4]})
    assert sim.data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number == 0
    assert all(np.isfinite(d) and -0.01 < d <= sim.plan.margin for d, _, _ in history.values())
    # MuJoCo's own distance query agrees with the contact-derived clearance.
    exact = sim.exact_clearance()
    for wire, (distance, _) in sim.monitor.wire_clearance().items():
        # Penetrating pairs: the contact narrow phase and mj_geomDistance estimate depth differently.
        # Separated capsule-cylinder pairs: both are iterative convex solvers (micrometre convergence).
        assert distance == pytest.approx(exact[wire][0], abs=1e-5 if exact[wire][0] >= 0 else 5e-5), wire


def test_mesh_sdf_sign_is_exact_near_corners():
    box = Box(np.array([0.26, 0.068, 0.055]), np.eye(3), np.array([0.015, 0.01, 0.02]))
    corners = box.corners()
    hull = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6),
            (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    faces = np.array([f if np.dot(np.cross(corners[f[1]] - corners[f[0]], corners[f[2]] - corners[f[0]]),
                                  corners[list(f)].mean(axis=0) - box.center) > 0 else f[::-1] for f in hull])
    mesh = MeshShape(corners, faces, spacing=0.001)
    rng = np.random.default_rng(0)
    points = box.center + rng.uniform(-0.25, 0.25, size=(400, 3))
    points[:50] = box.center + rng.uniform(-0.016, 0.016, size=(50, 3)) * [1, 1, 1.3]
    # The point that the nearest-sample sign called -0.196 m (behind the top face, beside the box).
    points = np.vstack([points, [[0.0546, 0.0112, 0.0749]]])
    assert np.all(np.sign(mesh.sdf(points)) == np.sign(box.sdf(points)))
    assert np.allclose(mesh.sdf(points), box.sdf(points), atol=0.0015)
