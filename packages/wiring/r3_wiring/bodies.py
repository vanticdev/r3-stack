"""Rigid-body kinematics derived from ``mechanical.joints``.

A *body* is the chassis (``ROOT``) or a component that is the child of a joint.
Every other component belongs to the body of its ``mount`` chain. Joint origins
and axes are authored in the robot frame at the nominal (zero) configuration,
so a body's transform is the composition, root first, of rotations about those
nominal axes: ``x = T_1(q_1) o T_2(q_2) o ... (x_nominal)``.

Steering: when the cad stage solved the tie-rod linkage
(``build/cad/cad_model.json`` ``kinematics.steering``), kingpin angles follow
that table instead of ideal Ackermann geometry, and each kingpin sweeps only the
travel the linkage can reach within the servo's horn travel.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from r3_model.spec import RobotSpec

from .geometry import rotation_about

ROOT = "robot"
Transform = tuple[np.ndarray, np.ndarray]  # (rotation, translation): x' = R x + t
IDENTITY: Transform = (np.eye(3), np.zeros(3))


@dataclass(frozen=True)
class JointInfo:
    id: str
    type: str
    parent: str
    child: str
    origin: np.ndarray
    axis: np.ndarray
    limits: tuple[float, float] | None

    def transform(self, value: float) -> Transform:
        if self.type in ("revolute", "continuous"):
            rotation = rotation_about(self.axis, value)
            return rotation, self.origin - rotation @ self.origin
        if self.type == "prismatic":
            return np.eye(3), self.axis * value
        return IDENTITY

    def samples(self, step_angle: float, step_length: float) -> list[float]:
        if self.type == "fixed":
            return [0.0]
        if self.type == "continuous":
            n = max(4, int(math.ceil(2 * math.pi / step_angle)))
            return list(np.linspace(0.0, 2 * math.pi, n, endpoint=False))
        lower, upper = self.limits if self.limits else (0.0, 0.0)
        step = step_angle if self.type == "revolute" else step_length
        n = max(2, int(math.ceil((upper - lower) / step)) + 1)
        return list(np.linspace(lower, upper, n))


def compose(a: Transform, b: Transform) -> Transform:
    """a o b."""
    return a[0] @ b[0], a[0] @ b[1] + a[1]


def invert(t: Transform) -> Transform:
    return t[0].T, -t[0].T @ t[1]


def cad_steering_table(cad: dict[str, Any] | None) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, tuple[float, float]]] | None:
    """CAD linkage solution: (servo angles ascending, joint -> kingpin angles, joint -> reachable range), or None
    when absent or when any side failed inside the horn travel."""
    steering = ((cad or {}).get("kinematics") or {}).get("steering") if isinstance(cad, dict) else None
    rows = (steering or {}).get("table") or []
    sides = (steering or {}).get("sides") or {}
    if len(rows) < 2 or not sides or any(side.get("status") != "ok" for side in sides.values()):
        return None
    if any(row.get(joint) is None for row in rows for joint in sides):
        return None
    rows = sorted(rows, key=lambda row: float(row["servo_rad"]))
    servo = np.array([float(row["servo_rad"]) for row in rows])
    angles = {joint: np.array([float(row[joint]) for row in rows]) for joint in sides}
    ranges = {joint: (float(a.min()), float(a.max())) for joint, a in angles.items()}
    return servo, angles, ranges


class Kinematics:
    def __init__(self, spec: RobotSpec, cad: dict[str, Any] | None = None) -> None:
        self.spec = spec
        self.joints: dict[str, JointInfo] = {}
        self.joint_of_child: dict[str, JointInfo] = {}
        self.steering_table = cad_steering_table(cad)
        reach = self.steering_table[2] if self.steering_table else {}
        for joint in spec.mechanical.joints:
            axis = np.asarray(joint.axis, dtype=float)
            info = JointInfo(joint.id, joint.type, joint.parent, joint.child, np.asarray(joint.origin, dtype=float),
                             axis / np.linalg.norm(axis), tuple(joint.limits) if joint.limits else None)
            if joint.id in reach:
                # The linkage, not the joint stop, bounds the kingpin travel.
                lower, upper = reach[joint.id]
                if info.limits:
                    lower, upper = max(lower, info.limits[0]), min(upper, info.limits[1])
                info = replace(info, limits=(lower, upper))
            self.joints[joint.id] = info
            self.joint_of_child[joint.child] = info

    def body_of(self, component: str) -> str:
        seen: set[str] = set()
        current = component
        while current not in ("world", "") and current not in seen:
            seen.add(current)
            if current in self.joint_of_child:
                return current
            comp = self.spec.components.get(current)
            if comp is None:
                break
            current = comp.mount
        return ROOT

    def chain(self, body: str) -> list[JointInfo]:
        """Joints from the root down to ``body``."""
        chain: list[JointInfo] = []
        current = body
        while current in self.joint_of_child:
            joint = self.joint_of_child[current]
            chain.append(joint)
            current = self.body_of(joint.parent) if joint.parent != "world" else ROOT
            if current == ROOT:
                break
        return list(reversed(chain))

    def mount_chain(self, component: str) -> list[str]:
        """The component and every component it is (transitively) mounted on."""
        out, current = [], component
        while current in self.spec.components and current not in out:
            out.append(current)
            current = self.spec.components[current].mount
        return out

    def body_transform(self, body: str, q: dict[str, float]) -> Transform:
        result = IDENTITY
        for joint in self.chain(body):
            result = compose(result, joint.transform(q.get(joint.id, 0.0)))
        return result

    def is_moving(self, body: str) -> bool:
        return any(j.type != "fixed" for j in self.chain(body))

    def configurations(self, body: str, step_angle: float = math.radians(1.0), step_length: float = 0.001,
                       skip: set[str] | None = None) -> list[dict[str, float]]:
        """Cartesian product of joint samples along the chain (``skip`` joints held at 0)."""
        chain = [j for j in self.chain(body) if not skip or j.id not in skip]
        if not chain:
            return [{}]
        grids = [j.samples(step_angle, step_length) for j in chain]
        return [dict(zip([j.id for j in chain], values)) for values in itertools.product(*grids)]

    def relative_joints(self, body_a: str, body_b: str) -> list[JointInfo]:
        """Joints whose motion changes the relative pose of two bodies."""
        a, b = self.chain(body_a), self.chain(body_b)
        common = 0
        while common < min(len(a), len(b)) and a[common].id == b[common].id:
            common += 1
        return a[common:] + b[common:]

    # -------------------------------------------------------------- steering
    def ackermann(self, steer: float) -> dict[str, float]:
        """Joint angles of every ``ackermann_steering`` transmission for a
        commanded centre steer angle (rad, +left). Inner wheel:
        tan(d_i) = L / (L / tan d - T/2); outer: tan(d_o) = L / (L / tan d + T/2)."""
        out: dict[str, float] = {}
        if self.steering_table:
            # CAD linkage: find the horn angle whose mean kingpin angle is ``steer`` (monotonic), then read each side.
            servo, angles, _ = self.steering_table
            centre = np.mean([a for a in angles.values()], axis=0)
            horn = float(np.interp(steer, centre, servo))
            out.update({joint: float(np.interp(horn, servo, a)) for joint, a in angles.items() if joint in self.joints})
        for trans in self.spec.mechanical.transmissions:
            if trans.type != "ackermann_steering" or all(j in out for j in trans.joints):
                continue
            from r3_model.units import to_si

            wheelbase = to_si(trans.params.get("wheelbase", 0.0))
            track = to_si(trans.params.get("kingpin_track", 0.0))
            for joint_id in trans.joints:
                joint = self.joints.get(joint_id)
                if joint is None:
                    continue
                left = joint.origin[1] > 0
                if abs(steer) < 1e-9 or wheelbase <= 0:
                    angle = steer
                else:
                    turning_left = steer > 0
                    inner = left == turning_left
                    radius = wheelbase / math.tan(abs(steer))
                    angle = math.atan(wheelbase / (radius - track / 2 if inner else radius + track / 2))
                    angle = math.copysign(angle, steer)
                if joint.limits:
                    angle = float(np.clip(angle, *joint.limits))
                out[joint_id] = angle
        return out

    def steering_limit(self) -> float:
        """Largest centre steer angle reachable on both sides (rad)."""
        if self.steering_table:
            servo, angles, _ = self.steering_table
            centre = np.mean([a for a in angles.values()], axis=0)
            return float(min(abs(centre[0]), abs(centre[-1])))
        limits = [min(abs(j.limits[0]), abs(j.limits[1])) for j in self.joints.values()
                  if j.type == "revolute" and j.limits and abs(j.axis[2]) > 0.9]
        return min(limits) if limits else 0.0
