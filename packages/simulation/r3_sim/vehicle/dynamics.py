"""Runtime vehicle: MuJoCo stepping plus transmission, steering, rolling
resistance and aerodynamic drag."""

from __future__ import annotations

import math
from typing import Any

import mujoco
import numpy as np

from r3_model.project import Project

from .mjcf import VehicleModel, _behavior_params
from .overlap import MujocoWorker
from .servo import SteeringServo, ackermann_angles

#: Frontal drag area C_d * A for a 1/10 touring/buggy body (estimate, m^2).
DEFAULT_DRAG_AREA = 0.04
#: Speed scale (rad/s) for smoothing sign(w) in rolling resistance at standstill.
ROLLING_SMOOTHING = 0.5


def air_density(temperature_c: float, pressure_pa: float = 101325.0) -> float:
    """Ideal gas: rho = p / (R_specific T), R_specific(dry air) = 287.05 J/(kg K)."""
    return pressure_pa / (287.05 * (temperature_c + 273.15))


class Vehicle:
    """MuJoCo car with motor-torque and servo-pulse inputs."""

    def __init__(self, project: Project, vm: VehicleModel) -> None:
        self.project = project
        self.vm = vm
        self.model = mujoco.MjModel.from_xml_string(vm.xml)
        self.data = mujoco.MjData(self.model)
        model = self.model
        env = project.spec.simulation.environment
        self.rho = air_density(env.ambient_temperature_c)
        root_params = project.spec.components[vm.root].params
        self.drag_area = float(root_params.get("drag_area", DEFAULT_DRAG_AREA))
        self.root_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, vm.root)
        self.free_qpos = model.jnt_qposadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"{vm.root}_free")]
        self.free_qvel = model.jnt_dofadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"{vm.root}_free")]

        def joint(name: str) -> tuple[int, int]:
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            return int(model.jnt_qposadr[jid]), int(model.jnt_dofadr[jid])

        self.joint_index = {j.id: joint(j.id) for j in project.spec.mechanical.joints if j.type != "fixed"}
        self.wheel_tire_geom = {cid: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"tire_{cid}") for cid in vm.wheels}
        self.tire_geom_to_wheel = {gid: cid for cid, gid in self.wheel_tire_geom.items()}
        self._wheel_order = list(vm.wheels)
        self._tire_index = np.full(model.ngeom, -1, dtype=np.int64)
        for k, cid in enumerate(self._wheel_order):
            self._tire_index[self.wheel_tire_geom[cid]] = k
        self._contact_force = np.zeros(6)
        self.wheel_normal_force = {cid: 0.0 for cid in vm.wheels}
        self.drive_actuators = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"act_{d.id}") for d in vm.drives]
        self.drive_tendons = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_TENDON, d.tendon) for d in vm.drives]
        self.steer_actuators = [[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, a) for a in s.actuators] for s in vm.steering]
        self.servos = [SteeringServo.from_params(_behavior_params(project, s.actuator_component)) for s in vm.steering]
        self.body_ids = {name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) for name in vm.bodies}
        sensor = lambda name: model.sensor_adr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, name)]  # noqa: E731
        self.acc_adr = sensor("imu_acc") if vm.imu_site else None
        self.gyro_adr = sensor("imu_gyro") if vm.imu_site else None
        self.motor_torque = [0.0 for _ in vm.drives]
        self.carrier_torque = [0.0 for _ in vm.drives]
        self.steer_targets = [(0.0, 0.0) for _ in vm.steering]
        mujoco.mj_forward(model, self.data)
        self._worker = MujocoWorker(model, self.data)
        self.cables = None
        if vm.cables is not None:
            from r3_wiring.mujoco_cables import CableMonitor, settle

            # The cables live in this world; let their slack settle with the car at rest before t = 0.
            self.cables = CableMonitor(model, self.data, vm.cables, vm.root)
            settle(model, self.data, self.cables)

    # ------------------------------------------------------------------ inputs
    def set_motor_torque(self, index: int, motor_torque: float, omega: float | None = None) -> None:
        """Motor shaft torque (N*m) -> carrier torque through the gear ratio. Gear losses
        always oppose power flow: tau_c = tau_m N eta when driving, tau_m N / eta when back-driven.
        ``omega``: motor speed read before :meth:`start_step` (required while one is in flight)."""
        drive = self.vm.drives[index]
        omega = self.motor_omega(index) if omega is None else omega
        driving = motor_torque * omega >= 0.0
        factor = drive.ratio * (drive.efficiency if driving else 1.0 / drive.efficiency)
        self.motor_torque[index] = motor_torque
        self.carrier_torque[index] = motor_torque * factor
        self.data.ctrl[self.drive_actuators[index]] = self.carrier_torque[index]

    def step_servo(self, index: int, dt: float, pulse_us: float, voltage: float) -> None:
        steer = self.vm.steering[index]
        servo = self.servos[index]
        # Reaction of the knuckles on the horn by virtual work: sum F_i d(delta_i)/d(theta).
        eps = 1e-4
        plus = ackermann_angles(steer.ratio * (servo.theta + eps), steer.wheelbase, steer.kingpin_track, steer.max_steer)
        minus = ackermann_angles(steer.ratio * (servo.theta - eps), steer.wheelbase, steer.kingpin_track, steer.max_steer)
        load = 0.0
        for k, act in enumerate(self.steer_actuators[index]):
            gain = (plus[k] - minus[k]) / (2 * eps)
            load += -self.data.actuator_force[act] * gain  # actuator force acts on knuckle; horn feels the opposite
        servo.step(dt, pulse_us, voltage, -load)
        angles = ackermann_angles(steer.ratio * servo.theta, steer.wheelbase, steer.kingpin_track, steer.max_steer)
        self.steer_targets[index] = angles
        for act, angle in zip(self.steer_actuators[index], angles):
            self.data.ctrl[act] = angle

    # ------------------------------------------------------------------ step
    def _apply_resistances(self) -> None:
        data = self.data
        data.qfrc_applied[:] = 0.0
        for cid, wheel in self.vm.wheels.items():
            _, dof = self.joint_index[wheel.joint]
            omega = data.qvel[dof]
            # Rolling resistance moment M = C_rr N r opposing wheel spin.
            data.qfrc_applied[dof] -= wheel.rolling_resistance * self.wheel_normal_force[cid] * wheel.radius * math.tanh(omega / ROLLING_SMOOTHING)
        v = data.qvel[self.free_qvel:self.free_qvel + 3]
        speed = math.sqrt(float(v @ v))
        # Aerodynamic drag F = -1/2 rho CdA |v| v at the body COM (still air).
        data.xfrc_applied[self.root_body, :3] = -0.5 * self.rho * self.drag_area * speed * v

    def _update_contacts(self) -> None:
        """Normal force on each tire (for the rolling-resistance moment), from tire contacts only."""
        data = self.data
        for cid in self.wheel_normal_force:
            self.wheel_normal_force[cid] = 0.0
        n = data.ncon
        if not n:
            return
        geoms = data.contact.geom[:n]
        wheel = np.maximum(self._tire_index[geoms[:, 0]], self._tire_index[geoms[:, 1]])
        hits = np.flatnonzero(wheel >= 0)
        if not len(hits):
            return
        if self.model.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC:
            # Elliptic cones: the first constraint row of a contact is its normal force.
            address = data.contact.efc_address[:n][hits]
            normal = np.where(address >= 0, np.abs(data.efc_force[np.maximum(address, 0)]), 0.0)
            for k, f in zip(wheel[hits], normal):
                self.wheel_normal_force[self._wheel_order[k]] += float(f)
            return
        force = self._contact_force
        for i in hits:
            mujoco.mj_contactForce(self.model, data, int(i), force)
            self.wheel_normal_force[self._wheel_order[wheel[i]]] += abs(force[0])

    def step(self) -> None:
        self.start_step()
        self.finish_step()

    @property
    def overlapped(self) -> bool:
        """True when MuJoCo's step halves run in the background worker."""
        return self._worker.enabled

    def start_step(self) -> None:
        """First half of ``mj_step`` (``mj_step1``), in the background when possible. Until
        it completes, only controls, applied forces and Python state may change; state
        written by ``mj_step1`` (kinematics, tendon/actuator velocities, contacts) must not be read."""
        self._worker.start("mj_step1")

    def launch_step2(self) -> None:
        """Applied resistances, then ``mj_step2`` (actuation, constraint solve, integration) in
        the background. ``mj_step1`` does not read applied forces. While it runs, ``qpos``,
        ``qvel``, ``actuator_force`` and ``sensordata`` must not be read."""
        self._apply_resistances()
        self._worker.start("mj_step2")

    def complete_step(self) -> None:
        self._worker.wait()
        self._update_contacts()
        if self.cables is not None:
            self.cables.update()

    def finish_step(self) -> None:
        """``mj_step2`` in this thread; with :meth:`start_step` equal to ``mj_step``."""
        self._apply_resistances()
        self._worker.wait()
        mujoco.mj_step2(self.model, self.data)
        self._update_contacts()
        if self.cables is not None:
            self.cables.update()

    def state(self, moving_parts: bool = False) -> "VehicleState":
        """Copy of the state the kernel reads after a step (valid while MuJoCo runs on)."""
        return VehicleState(self, moving_parts)

    # ------------------------------------------------------------------ state
    @property
    def time(self) -> float:
        return float(self.data.time)

    def motor_omega(self, index: int = 0) -> float:
        return float(self.data.ten_velocity[self.drive_tendons[index]]) * self.vm.drives[index].ratio

    def position(self) -> np.ndarray:
        return self.data.qpos[self.free_qpos:self.free_qpos + 3].copy()

    def quaternion(self) -> np.ndarray:
        return self.data.qpos[self.free_qpos + 3:self.free_qpos + 7].copy()

    def rotation(self) -> np.ndarray:
        return self.data.xmat[self.root_body].reshape(3, 3).copy()

    def yaw(self) -> float:
        r = self.data.xmat[self.root_body]
        return math.atan2(r[3], r[0])

    def velocity(self) -> np.ndarray:
        return self.data.qvel[self.free_qvel:self.free_qvel + 3].copy()

    def forward_speed(self) -> float:
        r = self.data.xmat[self.root_body]
        v = self.data.qvel[self.free_qvel:self.free_qvel + 3]
        return float(r[0] * v[0] + r[3] * v[1] + r[6] * v[2])

    def angular_velocity_world(self) -> np.ndarray:
        local = self.data.qvel[self.free_qvel + 3:self.free_qvel + 6]
        return self.rotation() @ local

    def joint_angle(self, joint_id: str) -> float:
        return float(self.data.qpos[self.joint_index[joint_id][0]])

    def joint_velocity(self, joint_id: str) -> float:
        return float(self.data.qvel[self.joint_index[joint_id][1]])

    def imu(self) -> tuple[np.ndarray, np.ndarray]:
        """(specific force m/s^2, angular rate rad/s) in the IMU site frame."""
        sd = self.data.sensordata
        return sd[self.acc_adr:self.acc_adr + 3].copy(), sd[self.gyro_adr:self.gyro_adr + 3].copy()

    def steer_angle(self, index: int = 0) -> float:
        """Mean measured kingpin angle (rad)."""
        steer = self.vm.steering[index]
        return 0.5 * sum(self.joint_angle(j) for j in steer.joints)

    def qpos(self) -> np.ndarray:
        return self.data.qpos.copy()

    def body_pose(self, body: str) -> tuple[np.ndarray, np.ndarray]:
        bid = self.body_ids[body]
        return self.data.xpos[bid].copy(), self.data.xquat[bid].copy()

    def moving_parts(self) -> dict[str, dict[str, Any]]:
        out = {}
        for body in self.vm.bodies:
            if body == self.vm.root:
                continue
            pos, quat = self.body_pose(body)
            out[body] = {"pos": pos, "quat": quat}
        return out

    def chassis_state(self, previous_velocity: np.ndarray | None, previous_omega: np.ndarray | None, dt: float) -> dict[str, Any]:
        v = self.velocity()
        w = self.angular_velocity_world()
        lin_acc = (v - previous_velocity) / dt if previous_velocity is not None else np.zeros(3)
        ang_acc = (w - previous_omega) / dt if previous_omega is not None else np.zeros(3)
        return {"pos": self.position(), "quat": self.quaternion(), "lin_vel": v, "lin_acc": lin_acc, "ang_vel": w, "ang_acc": ang_acc}


class VehicleState:
    """Snapshot of a :class:`Vehicle` after a step, with the same read accessors."""

    def __init__(self, vehicle: Vehicle, moving_parts: bool = False) -> None:
        data = vehicle.data
        self._vehicle = vehicle
        self._qpos = data.qpos.copy()
        self._qvel = data.qvel.copy()
        self._xmat = data.xmat[vehicle.root_body].copy()
        self._sensordata = data.sensordata.copy()
        self._ten_velocity = data.ten_velocity.copy()
        self._moving = vehicle.moving_parts() if moving_parts else None

    def motor_omega(self, index: int = 0) -> float:
        vehicle = self._vehicle
        return float(self._ten_velocity[vehicle.drive_tendons[index]]) * vehicle.vm.drives[index].ratio

    def position(self) -> np.ndarray:
        f = self._vehicle.free_qpos
        return self._qpos[f:f + 3].copy()

    def quaternion(self) -> np.ndarray:
        f = self._vehicle.free_qpos
        return self._qpos[f + 3:f + 7].copy()

    def rotation(self) -> np.ndarray:
        return self._xmat.reshape(3, 3).copy()

    def yaw(self) -> float:
        r = self._xmat
        return math.atan2(r[3], r[0])

    def velocity(self) -> np.ndarray:
        f = self._vehicle.free_qvel
        return self._qvel[f:f + 3].copy()

    def forward_speed(self) -> float:
        r, f = self._xmat, self._vehicle.free_qvel
        v = self._qvel[f:f + 3]
        return float(r[0] * v[0] + r[3] * v[1] + r[6] * v[2])

    def joint_angle(self, joint_id: str) -> float:
        return float(self._qpos[self._vehicle.joint_index[joint_id][0]])

    def imu(self) -> tuple[np.ndarray, np.ndarray]:
        vehicle = self._vehicle
        sd = self._sensordata
        return sd[vehicle.acc_adr:vehicle.acc_adr + 3].copy(), sd[vehicle.gyro_adr:vehicle.gyro_adr + 3].copy()

    def steer_angle(self, index: int = 0) -> float:
        steer = self._vehicle.vm.steering[index]
        return 0.5 * sum(self.joint_angle(j) for j in steer.joints)

    def qpos(self) -> np.ndarray:
        return self._qpos.copy()

    def moving_parts(self) -> dict[str, dict[str, Any]]:
        return self._moving if self._moving is not None else self._vehicle.moving_parts()
