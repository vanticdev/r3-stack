"""Rigid-body kinematics derived from ``mechanical.joints``.

A *body* is the chassis (``ROOT``) or a component that is the child of a joint.
Every other component belongs to the body of its ``mount`` chain. Joint origins
and axes are authored in the robot frame at the nominal (zero) configuration,
so a body's transform is the composition, root first, of rotations about those
nominal axes: ``x = T_1(q_1) o T_2(q_2) o ... (x_nominal)``.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

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


class Kinematics:
    def __init__(self, spec: RobotSpec) -> None:
        self.spec = spec
        self.joints: dict[str, JointInfo] = {}
        self.joint_of_child: dict[str, JointInfo] = {}
        for joint in spec.mechanical.joints:
            axis = np.asarray(joint.axis, dtype=float)
            info = JointInfo(joint.id, joint.type, joint.parent, joint.child, np.asarray(joint.origin, dtype=float),
                             axis / np.linalg.norm(axis), tuple(joint.limits) if joint.limits else None)
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
        for trans in self.spec.mechanical.transmissions:
            if trans.type != "ackermann_steering":
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
        limits = [min(abs(j.limits[0]), abs(j.limits[1])) for j in self.joints.values()
                  if j.type == "revolute" and j.limits and abs(j.axis[2]) > 0.9]
        return min(limits) if limits else 0.0
