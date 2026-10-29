"""Routed harnesses as MuJoCo cables inside the vehicle world, clearance in the kernel,
overlapped stepping, the reduced electrical Newton solve and headless GL selection."""

import json
import shutil
from pathlib import Path

import mujoco
import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.gl import choose_backend
from r3_sim.kernel import CoSimKernel
from r3_sim.vehicle import Vehicle, build_vehicle_model
from r3_wiring.stage import route_and_check

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


@pytest.fixture(scope="module")
def routed(tmp_path_factory):
    """The RC car with build/wiring/routes.json (catalog geometry, no CAD export)."""
    root = tmp_path_factory.mktemp("rc-car-sim-cables")
    shutil.copy(RC_CAR / "robot.yaml", root / "robot.yaml")
    project = Project.load(root)
    routes = route_and_check(project)["routes"]
    out = project.build_dir("wiring")
    (out / "routes.json").write_text(json.dumps(routes))
    return Project.load(root), routes


def _scenario(project: Project):
    return next(s for s in project.spec.simulation.scenarios if s.id == "full_throttle")


def test_cables_are_in_the_vehicle_world(routed):
    project, routes = routed
    plain = build_vehicle_model(project)
    vm = build_vehicle_model(project, routes=routes)
    plan = vm.cables
    assert plan is not None and not any(d.code == "cable_model_fallback" for d in vm.diagnostics)
    model = mujoco.MjModel.from_xml_string(vm.xml)
    assert "mujoco.elasticity.cable" in vm.xml and 'type="cable"' in vm.xml
    assert {w for b in plan.bundles for w in b.wires} == set(routes["wires"])
    # Wire mass rides on the vehicle; the part collision geoms add none.
    rigid = sum(model.body_mass[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b)] for b in vm.bodies)
    assert rigid == pytest.approx(sum(mujoco.MjModel.from_xml_string(plain.xml).body_mass), rel=1e-9)
    assert sum(model.body_mass) - rigid == pytest.approx(plan.total_mass, rel=0.02)
    assert vm.total_mass == pytest.approx(plain.total_mass + plan.total_mass)
    # Cables are anchored to the bodies that carry their pins (knuckles, wheels or chassis).
    welds = [i for i in range(model.neq) if model.eq_type[i] == mujoco.mjtEq.mjEQ_WELD]
    assert len(welds) == sum(len(b.spans) for b in plan.bundles)
    parents = {mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.eq_obj2id[i])) for i in welds}
    assert parents <= set(vm.bodies)


def test_kernel_clearance_is_a_physical_surface_distance(routed):
    """Regression for min_wire_clearance = -0.208 m: body poses were applied as component poses
    (20 mm knuckle offset) and the mesh SDF sign flipped beside corners. Now the clearance is
    MuJoCo's signed surface distance, identical to mj_geomDistance."""
    project, _ = routed
    kernel = CoSimKernel(project, _scenario(project))
    assert kernel.vehicle.cables is not None and kernel.cables is kernel.vehicle.cables
    for _ in range(100):
        kernel.step()
    assert kernel.vehicle.data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number == 0
    monitor = kernel.vehicle.cables
    exact = monitor.exact_distances()
    for bundle, current in zip(monitor.bundle_ids, monitor.current):
        assert current == pytest.approx(exact[bundle][0], abs=1e-6), bundle
    distance, wire, part = kernel.min_wire_clearance
    print("kernel min clearance at rest:", round(distance * 1e3, 2), "mm", wire, part)
    assert -0.005 < distance <= monitor.margin


def _drive(kernel: CoSimKernel) -> None:
    for key in kernel.pwm_devices:
        kernel.pwm_state[key] = {"enabled": True, "period_ns": 20_000_000,
                                 "duty_ns": 1_800_000 if kernel.esc_id in kernel.pwm_devices[key] else 1_500_000}


def test_overlapped_stepping_is_bit_identical(routed, monkeypatch):
    project, _ = routed
    runs = []
    for overlap in ("0", "1"):
        monkeypatch.setenv("R3_SIM_OVERLAP", overlap)
        kernel = CoSimKernel(project, _scenario(project))
        _drive(kernel)
        assert kernel.vehicle.overlapped == (overlap == "1")
        for deadline in (0.05, 0.1, 0.25, 0.4):  # deadline advances, as the HAL's sleep_until
            kernel.advance_until(lambda: kernel.t + 1e-9 >= deadline, deadline=deadline)
        runs.append((kernel.vehicle.data.qpos.copy(), kernel.recorder.arrays(), kernel.min_wire_clearance))
    (q0, rec0, clr0), (q1, rec1, clr1) = runs
    assert np.array_equal(q0, q1)
    assert rec0.keys() == rec1.keys() and all(np.array_equal(rec0[k], rec1[k], equal_nan=True) for k in rec0)
    assert clr0 == clr1


def test_vehicle_step_halves_equal_mj_step(routed):
    project, routes = routed
    vm = build_vehicle_model(project, routes=routes)
    a, b = Vehicle(project, vm), Vehicle(project, vm)
    for vehicle in (a, b):
        vehicle.set_motor_torque(0, 0.05)
    for _ in range(200):
        a.start_step()  # mj_step1 in the background worker
        a.launch_step2()  # mj_step2 in the background worker
        a.complete_step()
        b.step()  # mj_step1 in the worker, mj_step2 inline
    assert np.array_equal(a.data.qpos, b.data.qpos) and np.array_equal(a.data.qvel, b.data.qvel)


def test_reduced_newton_matches_full_newton(routed):
    from r3_sim.electrical import ElectricalSystem

    project, routes = routed
    system = ElectricalSystem(project, routes=routes)
    inputs = {"esc": {"pulse_us": 1900.0}, "motor": {"omega": 600.0}, "pi": {"activity": 0.55},
              "servo": {"pulse_us": 1500.0, "horn_torque": 0.1, "horn_speed": 0.0}}
    for _ in range(20):
        system.step(0.001, inputs)
    circuit = system.circuit
    A = circuit.linear_matrix()
    x_chord, info = circuit.solve(system.x)
    assert info.converged
    x_full, _ = circuit._newton(system.x, A, 80, 1e-9, 2.0, chord=False)
    # Plain dense Newton with central-difference Jacobian, no elimination: the textbook reference.
    x = system.x.copy()
    for _ in range(50):
        F, J = A @ x, A.copy()
        for element in circuit.elements:
            rows, deps, rmask, dmask = element._indices()
            v = element._values(x)
            F[rows[rmask]] += element.residual(v)[rmask]
            for j in np.flatnonzero(dmask):
                h = 1e-7 * max(1.0, abs(v[j]))
                vp, vm_ = v.copy(), v.copy()
                vp[j] += h
                vm_[j] -= h
                J[rows[rmask], deps[j]] += ((element.residual(vp) - element.residual(vm_)) / (2 * h))[rmask]
        dx = np.linalg.solve(J, -F)
        x += dx
        if np.abs(dx).max() < 1e-12:
            break
    scale = 1.0 + np.abs(x).max()
    assert np.abs(x_chord - x).max() < 1e-8 * scale
    assert np.abs(x_full - x).max() < 1e-8 * scale


def test_headless_gl_backend_selection():
    assert choose_backend("Linux", {}) in ("egl", "osmesa")  # no display: EGL if libEGL exists, else OSMesa
    assert choose_backend("Linux", {"DISPLAY": ":0"}) is None  # MuJoCo's default (GLFW)
    assert choose_backend("Linux", {"MUJOCO_GL": "osmesa"}) == "osmesa"  # explicit choice wins
    assert choose_backend("Darwin", {}) == "cgl"
