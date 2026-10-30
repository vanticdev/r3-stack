"""Standalone cable shaker: the routed harnesses as MuJoCo elastic cables on the
robot's bodies, with the chassis held by a shaker table.

R3 does not integrate cable dynamics itself: :mod:`r3_wiring.mujoco_cables`
describes the cables (``composite type="cable"`` with MuJoCo's
``mujoco.elasticity.cable`` plugin) and MuJoCo steps them. In the full digital
twin the same cables live in the vehicle's MuJoCo world (``r3_sim.vehicle``);
this module builds a chassis-only world for the wiring stage's
vibration/bump/steering drive and keeps the ``CableSimulator`` interface:

* the chassis body is fixed to the world; the world frame *is* the chassis frame;
* translational chassis acceleration enters through gravity (equivalence
  principle): in the chassis frame every body feels ``R^T (g - a_o)``, applied as
  ``opt.gravity`` each step, which is exact for any translation;
* chassis rotation adds the fictitious forces
  ``-m (alpha x r + omega x (omega x r) + 2 omega x v)`` on each cable segment
  (``xfrc_applied``; segment rotational inertia is negligible);
* jointed bodies (knuckles, wheels) follow the commanded joint values through
  stiff joint equality constraints, so cables anchored on them move with them
  and moving parts push cables they hit.

Clearance to moving parts is read from MuJoCo contact data every step
(:class:`r3_wiring.mujoco_cables.CableMonitor`).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import numpy as np

from r3_model.project import Project

from .bodies import ROOT, Kinematics
from .geometry import quat_to_matrix
from .mujoco_cables import ANCHOR_SOLREF, SETTLE_TIME, CableMonitor, CablePlan, emit_mjcf, plan_cables, settle
from .obstacles import ObstacleSet, build_obstacles

GRAVITY = np.array([0.0, 0.0, -9.80665])
CHASSIS = "chassis"


@dataclass
class CableReport:
    time: float
    min_clearance: dict[str, tuple[float, str]]  # wire -> (clearance this step, closest moving part)
    penetrating: list[str]
    max_speed: float  # fastest cable segment, m/s (chassis frame)
    max_strain: float  # largest pin give (m): cable end to its welded pin; segments are inextensible


def _fmt(values: Any) -> str:
    return " ".join(f"{float(v):.9g}" for v in np.ravel(values))


def _component_mass(project: Project, component: str) -> float:
    from r3_model.pulled import load_pulled, PullError
    pulled = load_pulled(project.root)
    part = pulled.parts.get(component)
    if part is None or part.mass is None:
        raise PullError("cad_pull_mass_missing", f"{component}: pulled mass is required for cable simulation")
    return float(part.mass)


def build_shaker_mjcf(project: Project, plan: CablePlan, kin: Kinematics, timestep: float) -> str:
    """Chassis fixed to the world, jointed bodies driven by joint equalities, cables."""
    mj = ET.Element("mujoco", model=f"{project.spec.robot.name}-cable-shaker")
    ET.SubElement(mj, "compiler", angle="radian", autolimits="true")
    ET.SubElement(mj, "option", timestep=f"{timestep:.9g}", integrator="implicitfast", cone="elliptic",
                  gravity=_fmt(GRAVITY))
    world = ET.SubElement(mj, "worldbody")
    bodies: dict[str, ET.Element] = {ROOT: ET.SubElement(world, "body", name=CHASSIS)}
    origins: dict[str, np.ndarray] = {ROOT: np.zeros(3)}
    equality = ET.SubElement(mj, "equality")
    pending = dict(kin.joints)
    while pending:  # parents before children
        progressed = False
        for joint_id, joint in list(pending.items()):
            parent = kin.body_of(joint.parent) if joint.parent != "world" else ROOT
            if parent not in bodies:
                continue
            element = ET.SubElement(bodies[parent], "body", name=joint.child, pos=_fmt(joint.origin - origins[parent]))
            jtype = {"revolute": "hinge", "continuous": "hinge", "prismatic": "slide"}.get(joint.type)
            if jtype:
                ET.SubElement(element, "joint", name=joint_id, type=jtype, axis=_fmt(joint.axis))
                ET.SubElement(equality, "joint", name=f"drive_{joint_id}", joint1=joint_id, polycoef="0 0 0 0 0",
                              solref=ANCHOR_SOLREF)
            bodies[joint.child], origins[joint.child] = element, np.asarray(joint.origin, dtype=float)
            del pending[joint_id]
            progressed = True
        if not progressed:
            raise ValueError(f"joints without a reachable parent body: {sorted(pending)}")
    emit_mjcf(mj, plan, lambda b: bodies[b], lambda b: origins[b], lambda b: CHASSIS if b == ROOT else b,
              part_mass=lambda component: _component_mass(project, component))
    for body, element in bodies.items():
        if body != ROOT and element.find("geom") is None:  # jointed bodies need mass
            ET.SubElement(element, "geom", type="sphere", size="0.001", mass="0.001", contype="0", conaffinity="0",
                          group="4")
    return ET.tostring(mj, encoding="unicode")


class CableSimulator:
    """``CableSimulator(project, routes).step(dt, chassis, moving) -> CableReport``.

    ``chassis``: ``pos``, ``quat`` ([w, x, y, z]) of the robot frame in the world
    and world-frame ``lin_acc`` (of the robot-frame origin), ``ang_vel``,
    ``ang_acc``. ``moving``: world poses (``pos``, ``quat``) of joint-child
    components (the frame of ``RobotGraph.component_pose``), converted to joint
    values; or pass ``joints={joint_id: value}`` directly. Joints not given
    stay where they are.
    """

    def __init__(self, project: Project, routes: dict[str, Any], obstacles: ObstacleSet | None = None, *,
                 timestep: float | None = None, settle_time: float = SETTLE_TIME, plan: CablePlan | None = None) -> None:
        import mujoco

        self.project = project
        self.routes = routes
        self.obstacles = obstacles or build_obstacles(project)[0]
        self.kin = self.obstacles.kinematics
        self.timestep = float(timestep if timestep is not None else project.spec.simulation.timestep)
        self.plan = plan or plan_cables(project, routes, self.obstacles, timestep=self.timestep)
        self.xml = build_shaker_mjcf(project, self.plan, self.kin, self.timestep)
        self.model = mujoco.MjModel.from_xml_string(self.xml)
        self.data = mujoco.MjData(self.model)
        self.monitor = CableMonitor(self.model, self.data, self.plan, CHASSIS)
        self._drive = {joint_id: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_EQUALITY, f"drive_{joint_id}")
                       for joint_id, joint in self.kin.joints.items() if joint.type != "fixed"}
        self._cable_bodies = self.monitor.cable_bodies
        self._mass = self.model.body_mass[self._cable_bodies]
        self.joint_values = {joint_id: 0.0 for joint_id in self._drive}
        self.wire_info = {wire: {"bundle": b.id, "harness": b.harness, "od": b.od, "spans": len(b.spans),
                                 "segments": sum(s.segments for s in b.spans), "free_length": b.free_length,
                                 "routed_length": b.routed_length}
                          for b in self.plan.bundles for wire in b.wires}
        mujoco.mj_forward(self.model, self.data)
        if settle_time > 0:
            settle(self.model, self.data, self.monitor, settle_time)
        self.time = 0.0

    # ------------------------------------------------------------- inputs
    def set_joints(self, joints: dict[str, float]) -> None:
        for joint_id, value in joints.items():
            if joint_id in self._drive:
                self.joint_values[joint_id] = float(value)
                self.model.eq_data[self._drive[joint_id], 0] = float(value)

    def joints_from_poses(self, chassis: dict[str, Any], moving: dict[str, dict[str, Any]]) -> dict[str, float]:
        """Joint values whose child components sit at the given world poses."""
        rc = quat_to_matrix(chassis.get("quat", [1, 0, 0, 0]))
        pc = np.asarray(chassis.get("pos", [0, 0, 0]), dtype=float)
        total: dict[str, np.ndarray] = {}  # body -> rotation relative to its nominal pose, chassis frame
        out: dict[str, float] = {}
        for joint in sorted(self.kin.joints.values(), key=lambda j: len(self.kin.chain(j.child))):
            pose = moving.get(joint.child)
            if pose is None or joint.type == "fixed":
                continue
            _, r0 = self.project.graph.component_pose(joint.child)
            rotation = rc.T @ quat_to_matrix(pose.get("quat", [1, 0, 0, 0])) @ r0.T
            total[joint.child] = rotation
            parent = self.kin.body_of(joint.parent) if joint.parent != "world" else ROOT
            relative = total.get(parent, np.eye(3)).T @ rotation
            if joint.type == "prismatic":
                p0, _ = self.project.graph.component_pose(joint.child)
                position = rc.T @ (np.asarray(pose["pos"], dtype=float) - pc)
                out[joint.id] = float((position - p0) @ joint.axis)
            else:
                # Rotation angle about the joint axis: sin from the skew part, cos from the trace.
                skew = np.array([relative[2, 1] - relative[1, 2], relative[0, 2] - relative[2, 0],
                                 relative[1, 0] - relative[0, 1]]) / 2.0
                out[joint.id] = math.atan2(float(skew @ joint.axis), (float(np.trace(relative)) - 1.0) / 2.0)
        return out

    # ---------------------------------------------------------------- step
    def _frame_forces(self, rc: np.ndarray, omega_w: np.ndarray, alpha_w: np.ndarray) -> None:
        """Fictitious forces of a rotating chassis frame on the cable segments."""
        data = self.data
        data.xfrc_applied[self._cable_bodies] = 0.0
        omega, alpha = rc.T @ omega_w, rc.T @ alpha_w
        if not (omega.any() or alpha.any()):
            return
        bodies = self._cable_bodies
        r = data.xipos[bodies]
        root = self.model.body_rootid[bodies]
        cvel = data.cvel[bodies]
        v = cvel[:, 3:] + np.cross(cvel[:, :3], r - data.subtree_com[root])
        accel = np.cross(alpha, r) + np.cross(omega, np.cross(omega, r)) + 2.0 * np.cross(omega, v)
        data.xfrc_applied[bodies, :3] = -self._mass[:, None] * accel

    def step(self, dt: float, chassis: dict[str, Any] | None = None, moving: dict[str, dict[str, Any]] | None = None,
             joints: dict[str, float] | None = None) -> CableReport:
        import mujoco

        chassis = chassis or {}
        rc = quat_to_matrix(chassis.get("quat", [1, 0, 0, 0]))
        if moving:
            self.set_joints(self.joints_from_poses(chassis, moving))
        if joints:
            self.set_joints(joints)
        # Equivalence principle: in the chassis frame gravity becomes R^T (g - a_origin).
        self.model.opt.gravity[:] = rc.T @ (GRAVITY - np.asarray(chassis.get("lin_acc", [0, 0, 0]), dtype=float))
        omega = np.asarray(chassis.get("ang_vel", [0, 0, 0]), dtype=float)
        alpha = np.asarray(chassis.get("ang_acc", [0, 0, 0]), dtype=float)
        steps = max(1, int(round(dt / self.model.opt.timestep)))
        for _ in range(steps):
            self._frame_forces(rc, omega, alpha)
            mujoco.mj_step(self.model, self.data)
            self.monitor.update()
        self.time += steps * self.model.opt.timestep
        clearance = self.monitor.wire_clearance()
        return CableReport(self.time, clearance, [w for w, (d, _) in clearance.items() if d < 0],
                           self.max_speed(), self.monitor.pin_error())

    # --------------------------------------------------------------- state
    def max_speed(self) -> float:
        """Fastest cable segment origin, m/s, from MuJoCo's com-based velocities."""
        bodies = self._cable_bodies
        if not len(bodies):
            return 0.0
        cvel = self.data.cvel[bodies]
        v = cvel[:, 3:] + np.cross(cvel[:, :3], self.data.xpos[bodies] - self.data.subtree_com[self.model.body_rootid[bodies]])
        return float(np.max(np.linalg.norm(v, axis=1)))

    @property
    def min_history(self) -> dict[str, tuple[float, str, float]]:
        """{wire: (minimum clearance so far, part, time)}."""
        return self.monitor.wire_minimum()

    def positions(self) -> dict[str, np.ndarray]:
        """Cable centreline vertices per wire, chassis frame."""
        return self.monitor.wire_positions("robot")

    def exact_clearance(self) -> dict[str, tuple[float, str]]:
        """Per-wire clearance from ``mj_geomDistance`` (cross-check of the contact data)."""
        bundles = self.monitor.exact_distances()
        return {wire: bundles[b] for wire, b in self.plan.wire_bundle.items()}


__all__ = ["CableReport", "CableSimulator", "build_shaker_mjcf"]
