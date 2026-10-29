import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from r3_model.project import Project
from r3_sim.geometry import MassProperties, box_inertia, combine
from r3_sim.kernel import CoSimKernel
from r3_sim.vehicle import Vehicle, build_vehicle_model
from r3_sim.vehicle.servo import ackermann_angles

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


@pytest.fixture(scope="module")
def project() -> Project:
    return Project.load(RC_CAR)


def test_mjcf_compiles_with_expected_structure(project):
    vm = build_vehicle_model(project)
    model = mujoco.MjModel.from_xml_string(vm.xml)
    catalog_mass = sum(project.catalog.component(c.type).mechanical.mass for c in project.spec.components.values())
    assert sum(model.body_mass) == pytest.approx(catalog_mass, rel=1e-6)
    assert set(vm.bodies) == {"chassis", "knuckle_fl", "knuckle_fr", "wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"}
    assert "battery" in vm.bodies["chassis"] and "pi" in vm.bodies["chassis"]
    # Rotor inertia reflected through the ratio onto the differential tendon: J_m N^2 + J_out.
    assert model.tendon_armature[0] == pytest.approx(1.2e-5 * 9.6 ** 2 + 4.0e-5)
    assert any(d.code == "cad_mass_estimated" for d in vm.diagnostics)
    assert model.geom_type[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "tire_wheel_rl")] == mujoco.mjtGeom.mjGEOM_CYLINDER


def test_composite_inertia_parallel_axis():
    a = MassProperties(1.0, np.array([0.1, 0.0, 0.0]), box_inertia(1.0, [0.1, 0.1, 0.1]))
    b = MassProperties(1.0, np.array([-0.1, 0.0, 0.0]), box_inertia(1.0, [0.1, 0.1, 0.1]))
    total = combine([a, b])
    assert total.mass == 2.0 and np.allclose(total.com, 0.0)
    i_box = 1.0 * (0.01 + 0.01) / 12.0
    assert total.inertia[0, 0] == pytest.approx(2 * i_box)
    assert total.inertia[1, 1] == pytest.approx(2 * (i_box + 0.01))


def test_ackermann_inner_wheel_turns_more():
    left, right = ackermann_angles(0.3, 0.26, 0.17, 0.45)
    assert left > 0.3 > right > 0
    radius = 0.26 / math.tan(0.3)
    assert math.tan(left) == pytest.approx(0.26 / (radius - 0.085))


def test_car_rolls_straight_under_constant_torque(project):
    vehicle = Vehicle(project, build_vehicle_model(project))
    for _ in range(300):
        vehicle.step()
    for _ in range(3000):
        vehicle.set_motor_torque(0, 0.02)
        vehicle.step_servo(0, 0.001, 1500.0, 6.0)
        vehicle.step()
    assert vehicle.forward_speed() > 2.0
    assert abs(vehicle.position()[1]) < 0.02
    assert abs(vehicle.yaw()) < 0.01
    # No longitudinal slip at moderate torque: ground speed = motor speed * r / N.
    assert vehicle.forward_speed() == pytest.approx(vehicle.motor_omega() * 0.055 / 9.6, rel=0.02)


def test_top_speed_consistent_with_motor_limit(project):
    scenario = next(s for s in project.spec.simulation.scenarios if s.id == "full_throttle")
    kernel = CoSimKernel(project, scenario)
    key = ("pwmchip0", 2)
    for step in range(9000):
        t = step * 0.001
        pulse = 1500.0 + 500.0 * min(1.0, t / 2.5)  # ramp to full throttle without spinning the rear tires
        kernel.pwm_state[key] = {"period_ns": 20_000_000, "duty_ns": int(pulse * 1000), "enabled": True}
        kernel.step()
    speed = kernel.vehicle.forward_speed()
    motor = kernel.electrical
    v_batt = kernel.last_outputs[kernel.battery_id]["v_terminal"]
    omega_max = (v_batt - motor.brush_drop) / motor.ke  # no-load motor speed at this supply
    bound = omega_max * 0.055 / 9.6
    assert 0.8 * bound < speed < bound
    assert speed == pytest.approx(kernel.vehicle.motor_omega() * 0.055 / 9.6, rel=0.03)
