import math

import mujoco
import numpy as np
import pytest

from r3_model.project import Project
from r3_model.units import to_si
from r3_sim.geometry import MassProperties, box_inertia, combine
from r3_sim.kernel import CoSimKernel
from r3_sim.vehicle import Vehicle, build_vehicle_model
from r3_sim.vehicle.servo import ackermann_angles



@pytest.fixture(scope="module")
def project(rc_car_fresh) -> Project:
    return Project.load(rc_car_fresh)


def test_mjcf_compiles_with_expected_structure(project):
    vm = build_vehicle_model(project)
    model = mujoco.MjModel.from_xml_string(vm.xml)
    cad = project.read_build_json("cad/cad_model.json")
    cad_parts = (cad or {}).get("parts", {})
    # CAD mass properties take precedence over catalog estimates, per part (a stale cad_model may lack new parts).
    expected_mass = sum(cad_parts[cid]["mass"] if vm.mass_source[cid] != "catalog"
                        else project.spec.components[cid].params.get("mass", project.catalog.component(c.type).mechanical.mass)
                        for cid, c in project.spec.components.items())
    # Every component and load-bearing fastener/nut has its own massive body.
    # CAD hardware uses exported mass; otherwise its catalog solid primitives
    # contribute mass (checked independently by the synthetic hardware fixture).
    expected_mass += sum(vm.mass_properties[name].mass for names in vm.cad_extras.values() for name in names)
    assert sum(model.body_mass) == pytest.approx(expected_mass, rel=1e-6)
    assert set(project.spec.components) <= set(vm.bodies)
    assert all(members == [cid] for cid,members in vm.bodies.items() if cid in project.spec.components)
    assert model.neq >= len(project.spec.mechanical.interfaces)
    assert {model.joint(i).name for i in range(model.njnt)} >= {"ball_tie_rod", "ball_tie_rod_r", "steer_servo"}
    for cid in project.spec.components:
        assert model.body(cid).mass > 0
        assert model.body(cid).geomnum > 0
    for body, extras in vm.cad_extras.items():
        assert extras in ([], [body])
    # Rotor inertia reflected through the ratio onto the differential tendon: J_m N^2 + J_out (gearbox via the path).
    drive = next(t for t in project.spec.mechanical.transmissions if t.type == "differential")
    params = lambda cid: {**project.catalog.component(project.spec.components[cid].type).behavior.params,  # noqa: E731
                          **project.spec.components[cid].params}
    expected_armature = to_si(params(drive.actuator)["rotor_inertia"]) * drive.ratio ** 2 + to_si(params("gearbox")["output_inertia"])
    assert expected_armature > 0
    assert model.tendon_armature[0] == pytest.approx(expected_armature)
    estimated = [cid for cid, source in vm.mass_source.items() if source == "catalog"]
    assert any(d.code == "cad_mass_estimated" for d in vm.diagnostics) == bool(estimated)
    assert model.geom_type[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "tire_wheel_rl")] == mujoco.mjtGeom.mjGEOM_CYLINDER


def test_steering_follows_cad_linkage_table(project):
    vm = build_vehicle_model(project)
    (steer,) = vm.steering
    table = ((project.read_build_json("cad/cad_model.json") or {}).get("kinematics") or {}).get("steering")
    if not table:
        assert steer.source == "ackermann_ratio"
        assert steer.wheel_angles(0.2) == ackermann_angles(steer.ratio * 0.2, steer.wheelbase, steer.kingpin_track,
                                                            steer.max_steer)
        return
    assert steer.source == "cad_linkage"
    for row in table["table"]:
        assert steer.wheel_angles(row["servo_rad"]) == pytest.approx([row[j] for j in steer.joints], abs=1e-9)
    # Horn travel stops hold the end rows; the inner wheel turns more than the outer one.
    end = max(row["servo_rad"] for row in table["table"])
    assert steer.wheel_angles(end + 0.2) == pytest.approx(steer.wheel_angles(end))
    left, right = steer.wheel_angles(0.3)
    assert left > right > 0


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


def test_car_rolls_straight_under_constant_torque(tmp_path):
    from test_joint_physics import drivetrain_project
    project = drivetrain_project(tmp_path)
    vehicle = Vehicle(project, build_vehicle_model(project))
    for _ in range(300):
        vehicle.step()
    for _ in range(3000):
        vehicle.set_motor_torque(0, 0.02)
        vehicle.step()
    assert vehicle.forward_speed() > 2.0
    assert abs(vehicle.position()[1]) < 0.02
    assert abs(vehicle.yaw()) < 0.01
    # No longitudinal slip at moderate torque: ground speed = motor speed * r / N.
    assert vehicle.forward_speed() == pytest.approx(vehicle.motor_omega() * 0.055 / 9.066, rel=0.02)


def test_top_speed_consistent_with_motor_limit(tmp_path):
    from test_joint_physics import drivetrain_project
    project = drivetrain_project(tmp_path)
    scenario = next(s for s in project.spec.simulation.scenarios if s.id == "full_throttle")
    kernel = CoSimKernel(project, scenario)
    key = ("pwmchip0", 2)
    for step in range(9000):
        t = step * 0.001
        if step % 10 == 0:  # GPIO heartbeat at the 100 Hz control rate (no arbiter in the synthetic bench)
            kernel.gpio_written(("gpiochip0", 27), (step // 10) % 2)
        pulse = 1500.0 + 500.0 * max(0.0, min(1.0, (t - 0.05) / 2.5))  # neutral while the arbiter re-arms, then ramp
        kernel.pwm_state[key] = {"period_ns": 20_000_000, "duty_ns": int(pulse * 1000), "enabled": True}
        kernel.step()
    speed = kernel.vehicle.forward_speed()
    # Motor constants from the catalog, independent of which electrical model the kernel uses.
    params = project.catalog.component(project.spec.components["motor"].type).behavior.params
    ke = 60.0 / (2 * math.pi * float(params["kv_rpm_per_v"]))
    v_batt = kernel.last_outputs[kernel.battery_id]["v_terminal"]
    omega_max = (v_batt - float(params.get("brush_drop", 0.2))) / ke  # no-load motor speed at this supply
    bound = omega_max * 0.055 / 9.066
    # The 9.066:1 drive spins the motor nearer its no-load speed only up to the load line: drag and rolling resistance
    # hold it near 80 % of the no-load wheel speed (8.5 of 10.7 m/s at this supply).
    assert 0.75 * bound < speed < bound
    assert speed == pytest.approx(kernel.vehicle.motor_omega() * 0.055 / 9.066, rel=0.03)
