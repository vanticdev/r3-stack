"""Monitored MuJoCo runtime for arbitrary robots and declared physics adapters."""

from __future__ import annotations

import math
from typing import Any

import mujoco
import numpy as np

from r3_model.project import Project
from r3_model.diagnostics import Diagnostic

from .assembly import AssemblyModel, _behavior_params
from .overlap import MujocoWorker


def mujoco_threads() -> int:
    """Worker threads of MuJoCo's per-step thread pool: ``R3_MUJOCO_THREADS`` (0/1 = serial), default 4."""
    import os

    try:
        return max(0, int(os.environ.get("R3_MUJOCO_THREADS", "4")))
    except ValueError:
        return 0


#: Speed scale (rad/s) for smoothing sign(w) in rolling resistance at standstill.
ROLLING_SMOOTHING = 0.5


def air_density(temperature_c: float, pressure_pa: float = 101325.0) -> float:
    """Ideal gas: rho = p / (R_specific T), R_specific(dry air) = 287.05 J/(kg K)."""
    return pressure_pa / (287.05 * (temperature_c + 273.15))


class SimulationInstability(RuntimeError):
    """MuJoCo reset a numerically unstable state; the run cannot qualify."""


class RobotDynamics:
    """Robot assembly with joint monitoring and optional declared actuators."""

    def __init__(self, project: Project, vm: AssemblyModel) -> None:
        self.substeps = project.spec.simulation.physics_substeps
        self.diagnostics: list[Diagnostic] = []
        self.project = project
        self.vm = vm
        self.wheels = getattr(vm, "wheels", {})
        self.steering = getattr(vm, "steering", [])
        if self.steering:
            from .vehicle.servo import SteeringServo
        from .compiled_models import compile_model
        self.model = compile_model(project, vm.xml)
        self.data = mujoco.MjData(self.model)
        # Compile fixture planes with their final masks so MuJoCo's aggregate
        # world-body mask includes them. Hold them inactive only during setup;
        # LoadCase enables each plane after its native distance-based placement.
        for name in ("joint_test_ground", "joint_impact_wall"):
            geom = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
            if geom >= 0:
                self.model.geom_conaffinity[geom] = 0
        self.mujoco_threads = mujoco_threads()
        if self.mujoco_threads > 1:
            # MuJoCo's own thread pool parallelises collision inside a step; the results are bit-identical to the
            # serial step (checked on the robot with cables: max |dq| = 0 after 600 steps).
            mujoco.mju_threadpool(self.data, self.mujoco_threads)
        model = self.model
        env = project.spec.simulation.environment
        self.rho = air_density(env.ambient_temperature_c)
        root_params = project.spec.components[vm.root].params
        self.drag_area = float(root_params.get("drag_area", getattr(vm,"default_drag_area",0.0)))
        self.root_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, vm.root)
        self.free_qpos = model.jnt_qposadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"{vm.root}_free")]
        self.free_qvel = model.jnt_dofadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, f"{vm.root}_free")]

        def joint(name: str) -> tuple[int, int]:
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            return int(model.jnt_qposadr[jid]), int(model.jnt_dofadr[jid])

        self.joint_index = {j.id: joint(j.id) for j in project.spec.mechanical.joints if j.type != "fixed"}
        self.wheel_tire_geom = {cid: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"tire_{cid}") for cid in self.wheels}
        self.tire_geom_to_wheel = {gid: cid for cid, gid in self.wheel_tire_geom.items()}
        self._wheel_order = list(self.wheels)
        self._tire_index = np.full(model.ngeom, -1, dtype=np.int64)
        self._flex_tire_index = np.full(model.nflex, -1, dtype=np.int64)
        for k, cid in enumerate(self._wheel_order):
            if self.wheel_tire_geom[cid] >= 0:
                self._tire_index[self.wheel_tire_geom[cid]] = k
            body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, cid)
            for f in range(model.nflex):
                start, count = int(model.flex_vertadr[f]), int(model.flex_vertnum[f])
                if (model.flex_rigid[f] and model.flex_contype[f] & 1 and count
                        and np.all(model.flex_vertbodyid[start:start + count] == body)):
                    # Internal surfaces replaced by an environment contact proxy
                    # must not count shaft/fastener loads as rolling support.
                    self._flex_tire_index[f] = k
        self._contact_force = np.zeros(6)
        self._object_velocity = np.zeros(6)
        self._resistance_wrenches = np.zeros((model.nbody,6))
        self._wheel_axes = {cid:project.graph.component_pose(cid)[1] @ np.eye(3)["xyz".index(project.catalog.component(project.spec.components[cid].type).mechanical.axis)] for cid in self.wheels}
        self._link_groups = {c.id:c.group or c.id for c in vm.connections.connections}
        self._transmission_paths = {t.id:set(t.path) for t in project.spec.mechanical.transmissions}
        self.wheel_normal_force = {cid: 0.0 for cid in self.wheels}
        self.drive_actuators = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"act_{d.id}") for d in vm.drives]
        self.drive_tendons = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_TENDON, d.tendon) for d in vm.drives]
        self.steer_actuators = [[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, a) for a in s.actuators] for s in self.steering]
        self.horn_actuators = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, s.horn_actuator) if s.horn_actuator else -1
                               for s in self.steering]
        self.servos = [SteeringServo.from_params(_behavior_params(project, s.actuator_component)) for s in self.steering]
        self.body_ids = {name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) for name in vm.bodies}
        sensor = lambda name: model.sensor_adr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, name)]  # noqa: E731
        self.acc_adr = sensor("imu_acc") if vm.imu_site else None
        self.gyro_adr = sensor("imu_gyro") if vm.imu_site else None
        self.motor_torque = [0.0 for _ in vm.drives]
        self.carrier_torque = [0.0 for _ in vm.drives]
        self.steer_targets = [(0.0, 0.0) for _ in self.steering]
        from .surface_collisions import ContactExclusions
        contact_filter = ContactExclusions(model, vm.joint_bindings)
        contact_filter.refresh(self.data.eq_active)
        mujoco.mj_forward(model, self.data)
        from .joints import JointMonitor

        self.joints = JointMonitor(
            model, self.data, vm.connections, vm.joint_bindings, vm.root, contact_filter=contact_filter
        )
        self.joints.origins = {
            **vm.body_origin,
            **{f"mount:{k}": v for k, v in vm.body_origin.items()},
        }
        from r3_model.pulled_view import cad_view
        cad = cad_view(project, mass_required=True)
        self._resting_poses = {}
        hardware_parents = {}
        for fastener in project.spec.mechanical.fasteners:
            for name in (fastener.part, fastener.nut_part):
                if name:
                    hardware_parents[name] = next((part for part in fastener.clamps if part != name), fastener.thread_into or vm.root)
        for name in vm.bodies:
            if name == vm.root:
                continue
            comp = project.spec.components.get(name)
            joint = vm.moving_joints.get(name)
            parent = (joint.parent if joint else comp.mount) if comp else hardware_parents.get(name) or (cad.get("parts",{}).get(name) or {}).get("owner") or vm.root
            if parent == name or parent not in self.body_ids:
                parent = vm.root
            parent = vm.root if parent == "world" else parent
            carrier = f"mount:{name}"
            a = model.body(carrier).id if joint else self.body_ids[name]
            b = self.body_ids[parent]
            rotation = self.data.xmat[b].reshape(3,3)
            rel = rotation.T @ (self.data.xpos[a] - self.data.xpos[b])
            rel_rot = rotation.T @ self.data.xmat[a].reshape(3,3)
            self._resting_poses[name] = (a,b,rel,rel_rot)
        self._part_axes = {name:vm.moving_joints.get(name) for name in self._resting_poses}
        self._part_tolerances = {name:min((c.tolerance for c in vm.connections.connections if name in (c.a,c.b)),
                                         default=float(project.catalog.joint_defaults["fit_tolerance"].value))
                                 for name in self._resting_poses}
        self._part_radii = {}
        for name in self._resting_poses:
            body=self.body_ids[name];start=int(model.body_geomadr[body]);count=int(model.body_geomnum[body])
            radius=max((float(model.geom_rbound[g])+float(np.linalg.norm(model.geom_pos[g])) for g in range(start,start+count)),default=0.)
            self._part_radii[name]=radius
        self._mount_constraints = {}
        for binding in vm.joint_bindings:
            for body in (binding["body1"], binding["body2"]):
                if body.startswith("mount:"):
                    self._mount_constraints.setdefault(body[6:], []).append(model.equality(binding["equality"]).id)
        self.disconnected_drives: set[int] = set()
        self._released_mounts: set[str] = set()
        self._detached_joint_ids: set[str] = set()
        self._update_released_mounts()
        self.cables = None
        if vm.cables is not None:
            from r3_wiring.mujoco_cables import CableMonitor, settle

            # The cables live in this world; let their slack settle with the robot at rest before t = 0.
            self.cables = CableMonitor(model, self.data, vm.cables, vm.root)
            # Pre-roll settles cables only. Robot parts start from the assembly,
            # so missing retainers cannot fall out before recording begins.
            rigid = np.setdiff1d(np.arange(model.nv), self.cables.cable_dofs)
            qpos0 = self.data.qpos.copy()
            # Preserve cable qpos while holding only non-cable joint positions.
            cable_qpos = np.concatenate(
                [
                    np.arange(
                        model.jnt_qposadr[j],
                        model.jnt_qposadr[j]
                        + (
                            7
                            if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE
                            else 4
                            if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_BALL
                            else 1
                        ),
                    )
                    for j in range(model.njnt)
                    if model.jnt_dofadr[j] in self.cables.cable_dofs
                ]
            )
            rigid_qpos = np.setdiff1d(np.arange(model.nq), cable_qpos)

            def hold_assembly() -> None:
                self.data.qpos[rigid_qpos] = qpos0[rigid_qpos]
                self.data.qvel[rigid] = 0

            settle(model, self.data, self.cables, before_step=hold_assembly)
            hold_assembly()
            mujoco.mj_forward(model, self.data)
        self._worker = MujocoWorker(model, self.data)

    def close(self) -> None:
        worker = getattr(self, '_worker', None)
        if worker is not None:
            worker.close()

    def __del__(self) -> None:
        # The runner closes explicitly; standalone project experiments also own workers.
        try:
            self.close()
        except Exception:
            pass

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
        steer = self.steering[index]
        servo = self.servos[index]
        # Reaction of the knuckles on the horn by virtual work: sum F_i d(delta_i)/d(theta).
        eps = 1e-4
        plus = steer.wheel_angles(servo.theta + eps)
        minus = steer.wheel_angles(servo.theta - eps)
        load = 0.0
        for k, act in enumerate(self.steer_actuators[index]):
            gain = (plus[k] - minus[k]) / (2 * eps)
            load += -self.data.actuator_force[act] * gain  # actuator force acts on knuckle; horn feels the opposite
        servo.step(dt, pulse_us, voltage, -load)
        angles = steer.wheel_angles(servo.theta)
        self.steer_targets[index] = angles
        for act, angle in zip(self.steer_actuators[index], angles):
            self.data.ctrl[act] = angle
        if self.horn_actuators[index] >= 0:
            self.data.ctrl[self.horn_actuators[index]] = servo.theta

    # ------------------------------------------------------------------ step
    def _update_released_mounts(self) -> None:
        """Fix only a redundant coordinate after release, leaving the part free.

        A free carrier plus its hinge has one unobservable spin coordinate.
        Freezing that internal coordinate after the real mount releases makes
        the pair one freely moving rigid body; it never anchors it to a parent.
        """
        m,d=self.model,self.data
        for child,joint in self.vm.moving_joints.items():
            if child in self._released_mounts or any(d.eq_active[e] for e in self._mount_constraints.get(child,[])):
                continue
            eq=m.equality(f"released:{child}").id
            jid=m.joint(joint.id).id
            if joint.type=="ball":
                a=m.body(child).id;b=m.body(f"mount:{child}").id
                m.eq_data[eq,3:6]=d.xmat[a].reshape(3,3).T@(d.xpos[b]-d.xpos[a])
                inverse=np.zeros(4);mujoco.mju_negQuat(inverse,d.xquat[a])
                mujoco.mju_mulQuat(m.eq_data[eq,6:10],inverse,d.xquat[b])
            else:
                m.eq_data[eq,0]=d.qpos[m.jnt_qposadr[jid]]
            d.eq_active[eq]=True
            self._released_mounts.add(child)
            self._detached_joint_ids.add(joint.id)

    def _apply_resistances(self) -> None:
        data = self.data
        # Once a mounted output is mechanically disconnected, its former
        # actuator cannot keep applying torque across the open connection.
        detached_joints = self._detached_joint_ids
        failed_links = {self._link_groups[name] for name,state in self.joints.states.items() if state.failed and name in self._link_groups}
        for k, drive in enumerate(self.vm.drives):
            if detached_joints.intersection(drive.joints) or failed_links.intersection(self._transmission_paths.get(drive.id,set())):
                self.disconnected_drives.add(k)
            if k in self.disconnected_drives:
                data.ctrl[self.drive_actuators[k]] = 0
        for steer in self.steering:
            if steer.horn_joint in detached_joints and steer.horn_actuator:
                act=self.model.actuator(steer.horn_actuator).id
                self.model.actuator_gainprm[act]=0;self.model.actuator_biasprm[act]=0
            for name, act_name in zip(steer.joints, steer.actuators):
                if name in detached_joints or failed_links.intersection(self._transmission_paths.get(steer.id,set())):
                    act = self.model.actuator(act_name).id
                    self.model.actuator_gainprm[act] = 0
                    self.model.actuator_biasprm[act] = 0
        # Apply road resistance to the actual tire body. A detached tire must
        # not keep braking the axle it used to be attached to.
        data.xfrc_applied[:] -= self._resistance_wrenches
        self._resistance_wrenches.fill(0)
        for cid,wheel in self.wheels.items():
            body=self.body_ids[cid]
            axis=data.xmat[body].reshape(3,3) @ self._wheel_axes[cid]
            mujoco.mj_objectVelocity(self.model,data,mujoco.mjtObj.mjOBJ_BODY,body,self._object_velocity,0)
            omega=float(self._object_velocity[:3] @ axis)
            self._resistance_wrenches[body,3:] -= wheel.rolling_resistance*self.wheel_normal_force[cid]*wheel.radius*math.tanh(omega/ROLLING_SMOOTHING)*axis
        v=data.qvel[self.free_qvel:self.free_qvel+3]
        self._resistance_wrenches[self.root_body,:3] -= .5*self.rho*self.drag_area*math.sqrt(float(v@v))*v
        data.xfrc_applied[:] += self._resistance_wrenches

    def _update_contacts(self) -> None:
        """Normal force on each tire (for the rolling-resistance moment), from tire contacts only."""
        data = self.data
        for cid in self.wheel_normal_force:
            self.wheel_normal_force[cid] = 0.0
        n = data.ncon
        if not n:
            return
        geoms = data.contact.geom[:n]
        indexes = np.full(geoms.shape, -1, dtype=np.int64)
        valid = geoms >= 0
        indexes[valid] = self._tire_index[geoms[valid]]
        if self.model.nflex:
            flexes = data.contact.flex[:n]
            valid = flexes >= 0
            indexes[valid] = np.maximum(indexes[valid], self._flex_tire_index[flexes[valid]])
        wheel = indexes.max(axis=1)
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
        self._update_released_mounts()
        if hasattr(self, "load_case"):
            self.load_case.apply()
        self._worker.start("mj_step1")

    def launch_step2(self) -> None:
        """Applied resistances, then ``mj_step2`` (actuation, constraint solve, integration) in
        the background. ``mj_step1`` does not read applied forces. While it runs, ``qpos``,
        ``qvel``, ``actuator_force`` and ``sensordata`` must not be read."""
        self._worker.wait()
        self._apply_resistances()
        self._worker.start("mj_step2")

    def complete_step(self) -> None:
        """Wait for ``mj_step2``. The cable clearance of this step is captured, not measured: the kernel measures it
        (:meth:`CableMonitor.update`, same result) while MuJoCo runs the next ``mj_step1`` in the worker."""
        self._worker.wait()
        self._update_contacts()
        self._check_joints()
        if self.cables is not None:
            if self._worker.enabled and self.substeps == 1:
                self.cables.capture()
            else:
                self.cables.update()

        self._remaining_substeps()

    def finish_step(self) -> None:
        """``mj_step2`` in this thread; with :meth:`start_step` equal to ``mj_step``."""
        self._worker.wait()
        self._apply_resistances()
        mujoco.mj_step2(self.model, self.data)
        self._update_contacts()
        self._check_joints()
        if self.cables is not None:
            self.cables.update()
        self._remaining_substeps()

    def _remaining_substeps(self) -> None:
        # Only MuJoCo integrates. Keep the electrical/software master period,
        # with capacity/contact checks on every native physics step.
        for _ in range(1, self.substeps):
            self._update_released_mounts()
            if hasattr(self, "load_case"):
                self.load_case.apply()
            self._apply_resistances()
            mujoco.mj_step(self.model, self.data)
            self._update_contacts()
            self._check_joints()
            if self.cables is not None:
                self.cables.update()

    def reset_assembly_reference(self) -> None:
        """Refresh the initial parent-relative reference after a load-case rotation."""
        self._resting_poses = {name:(a,b,self.data.xmat[b].reshape(3,3).T @ (self.data.xpos[a]-self.data.xpos[b]),
                                    self.data.xmat[b].reshape(3,3).T @ self.data.xmat[a].reshape(3,3))
                               for name,(a,b,_,_) in self._resting_poses.items()}
        self.joints.reset_reference()

    def _check_joints(self) -> None:
        for warning in (
            mujoco.mjtWarning.mjWARN_BADQACC,
            mujoco.mjtWarning.mjWARN_BADQPOS,
            mujoco.mjtWarning.mjWARN_BADQVEL,
        ):
            if self.data.warning[warning].number:
                raise SimulationInstability(
                    f"MuJoCo {warning.name}: unstable state at t={self.data.time:g} s; load results are incomplete"
                )
        if hasattr(self, "load_case"):
            self.load_case.observe()
        self.joints.step()
        for name, (a, b, rest, rest_rot) in self._resting_poses.items():
            if name in self.joints.detached:
                continue
            error = float(
                np.linalg.norm(
                    self.data.xmat[b].reshape(3, 3).T
                    @ (self.data.xpos[a] - self.data.xpos[b])
                    - rest
                )
            )
            rotation = self.data.xmat[b].reshape(3,3).T @ self.data.xmat[a].reshape(3,3)
            joint = self._part_axes[name]
            if joint and joint.type in ("continuous", "revolute"):
                axis = np.asarray(joint.axis, float); axis /= np.linalg.norm(axis)
                # Carrier spin about the allowed hinge axis is an unobservable
                # coordinate, not separation of a fitted surface.
                angle_error = float(np.linalg.norm(rotation @ axis - rest_rot @ axis))
            elif joint and joint.type == "ball":
                angle_error = 0.0
            else:
                angle_error = float(np.linalg.norm(rotation-rest_rot))/math.sqrt(2)
            error = float(max(error, angle_error*self._part_radii[name]))
            tolerance = self._part_tolerances[name]
            if error > tolerance:
                self.joints.detached.add(name)
                message = f"{name}: moved {error:.6g} m relative to its parent (tolerance {tolerance:g} m)"
                self.joints.events.append(dict(t=float(self.data.time), event="part_detached", joint=f"resting:{name}",
                                              parts=[name], mode="displacement", load=error, capacity=tolerance,
                                              cause="fit displacement", message=message))
                self.joints.diagnostics.append(
                    Diagnostic.error(
                        "part_detached",
                        f"{name}: moved {error:.6g} m relative to its parent (tolerance {tolerance:g} m)",
                        subjects=[name],
                        data={
                            "t": float(self.data.time),
                            "displacement_m": error,
                            "tolerance_m": tolerance,
                        },
                    )
                )

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
        steer = self.steering[index]
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
        steer = self._vehicle.steering[index]
        return 0.5 * sum(self.joint_angle(j) for j in steer.joints)

    def qpos(self) -> np.ndarray:
        return self._qpos.copy()

    def moving_parts(self) -> dict[str, dict[str, Any]]:
        return self._moving if self._moving is not None else self._vehicle.moving_parts()


# Backwards-compatible adapter name; assembly users use RobotDynamics directly.
Vehicle = RobotDynamics
