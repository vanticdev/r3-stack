"""Routed harnesses as MuJoCo cables inside the vehicle world, clearance in the kernel,
overlapped stepping and the explicit headless GL backend."""

import sys

import mujoco
import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.kernel import CoSimKernel
from r3_sim.vehicle import Vehicle, build_vehicle_model



@pytest.fixture(scope="module")
def routed(rc_car_wiring):
    """The RC car with build/wiring/routes.json (catalog geometry, no CAD export; shared stage run)."""
    assert (rc_car_wiring.root / "build" / "wiring" / "routes.json").exists()
    return Project.load(rc_car_wiring.root), rc_car_wiring.bundle["routes"]


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
    # Cables are anchored to the bodies that carry their pins (knuckles, wheels or chassis): a weld
    # per clamped pin, a connect per pinned support (r3_wiring.supports permitted motion).
    welds = [i for i in range(model.neq)
             if int(model.eq_type[i]) in (int(mujoco.mjtEq.mjEQ_WELD), int(mujoco.mjtEq.mjEQ_CONNECT))
             and (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_EQUALITY, i) or "").endswith("_pin")]
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
        # Separated pairs: identical signed distance. Penetrating pairs: MuJoCo's contact narrow phase
        # and mj_geomDistance estimate the depth with different algorithms (tens of micrometres).
        tolerance = 1e-6 if exact[bundle][0] >= 0 else 5e-5
        assert current == pytest.approx(exact[bundle][0], abs=tolerance), bundle
    distance, wire, part = kernel.min_wire_clearance
    print("kernel min clearance at rest:", round(distance * 1e3, 2), "mm", wire, part)
    assert -0.005 < distance <= monitor.margin


_RUN = """
import hashlib, sys
from r3_model.project import Project
from r3_sim.kernel import CoSimKernel
project = Project.load(sys.argv[1])
kernel = CoSimKernel(project, next(s for s in project.spec.simulation.scenarios if s.id == "full_throttle"))
for key in kernel.pwm_devices:
    kernel.pwm_state[key] = {"enabled": True, "period_ns": 20_000_000,
                             "duty_ns": 1_800_000 if kernel.esc_id in kernel.pwm_devices[key] else 1_500_000}
for deadline in (0.05, 0.1, 0.25, 0.4):  # deadline advances, as the HAL's sleep_until
    kernel.advance_until(lambda: kernel.t + 1e-9 >= deadline, deadline=deadline)
rec = kernel.recorder.arrays()
digest = hashlib.sha1(kernel.vehicle.data.qpos.tobytes() + b"".join(rec[k].tobytes() for k in sorted(rec)))
print(kernel.vehicle.overlapped, digest.hexdigest(), repr(kernel.min_wire_clearance))
"""


def test_overlapped_stepping_is_bit_identical(routed):
    """Serial (R3_SIM_OVERLAP=0) and overlapped stepping give identical recordings. Each run is
    its own process: libngspice is process-global, so a second circuit in one process starts
    from different library state."""
    import os
    import subprocess

    project, _ = routed
    results = []
    for overlap in ("0", "1"):
        env = {**os.environ, "R3_SIM_OVERLAP": overlap, "PYTHONPATH": os.pathsep.join(sys.path)}
        run = subprocess.run([sys.executable, "-c", _RUN, str(project.root)], env=env, capture_output=True, text=True,
                             timeout=600)
        assert run.returncode == 0, run.stderr[-2000:]
        results.append(run.stdout.strip().splitlines()[-1].split(" ", 2))
    (serial_overlap, serial, serial_clearance), (overlapped, digest, clearance) = results
    assert (serial_overlap, overlapped) == ("False", "True")
    assert digest == serial and clearance == serial_clearance


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


def test_renderer_backend_is_explicit():
    """In a fresh interpreter, importing r3_sim selects the backend before mujoco loads."""
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ("MUJOCO_GL", "PYOPENGL_PLATFORM")}
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    code = "from r3_sim import render; import mujoco; print(render.GL_BACKEND)"
    backend = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert backend.returncode == 0, backend.stderr[-1000:]
    if sys.platform == "darwin":
        assert backend.stdout.strip() == "cgl"  # offscreen CGL, no display needed
    explicit = subprocess.run([sys.executable, "-c", code], env={**env, "MUJOCO_GL": "osmesa" if sys.platform == "linux"
                                                                  else "glfw"}, capture_output=True, text=True, timeout=120)
    assert explicit.stdout.strip() in ("osmesa", "glfw")  # an explicit MUJOCO_GL always wins


def _every_pair_minimum(cables) -> tuple[np.ndarray, np.ndarray]:
    """Reference for the clearance monitor: mj_geomDistance on every cable/moving-part pair in the contact list."""
    data, model = cables.data, cables.model
    current = np.full(len(cables.current), cables.margin)
    part = np.full(len(cables.current), -1)
    n = data.ncon
    if not n:
        return current, part
    geoms = data.contact.geom[:n]
    g1, g2 = geoms[:, 0], geoms[:, 1]
    first = (cables.geom_bundle[g1] >= 0) & cables.geom_moving[g2]
    second = (cables.geom_bundle[g2] >= 0) & cables.geom_moving[g1]
    relevant = first | second
    pairs = np.unique(np.stack([np.where(first, g1, g2)[relevant], np.where(first, g2, g1)[relevant]], axis=1), axis=0)
    fromto = np.zeros(6)
    for gc, gp in pairs:
        d = min(mujoco.mj_geomDistance(model, data, int(gc), int(gp), cables.margin, fromto), cables.margin)
        b = cables.geom_bundle[gc]
        if d < current[b]:
            current[b], part[b] = d, cables.geom_part[gp]
    return current, part


def test_clearance_branch_and_bound_equals_every_pair(routed):
    """The bounding-sphere branch and bound in the clearance monitor returns the same per-bundle
    minimum (and closest part) as measuring every listed pair exactly."""
    project, _ = routed
    scenario = next(s for s in project.spec.simulation.scenarios if s.id == "steering_sweep")
    kernel = CoSimKernel(project, scenario)
    cables = kernel.vehicle.cables
    compared = near = 0
    for step in range(600):
        kernel.step()
        if step % 3:
            continue
        reference, part = _every_pair_minimum(cables)
        assert np.allclose(cables.current, reference, rtol=0.0, atol=1e-12)
        close = reference < cables.margin
        assert np.array_equal(cables.current_part[close], part[close])
        compared += 1
        near += int(close.sum())
    assert compared == 200 and near > 0
