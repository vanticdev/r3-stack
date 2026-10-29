"""Rigid-body helpers: rotations, primitive inertias and composite inertia."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from r3_model.graph import rpy_matrix

__all__ = [
    "MassProperties", "box_inertia", "cylinder_inertia", "combine", "rotation_to_quat", "quat_to_rotation",
    "axis_rotation", "rpy_matrix", "yaw_of",
]


@dataclass
class MassProperties:
    """Mass, center of mass and inertia tensor about the COM, all in the robot frame."""

    mass: float
    com: np.ndarray
    inertia: np.ndarray  # 3x3 about com
    source: str = "catalog"  # cad | catalog

    def transformed(self, rotation: np.ndarray, translation: np.ndarray) -> "MassProperties":
        return MassProperties(self.mass, rotation @ self.com + translation, rotation @ self.inertia @ rotation.T, self.source)


def box_inertia(mass: float, size: Iterable[float]) -> np.ndarray:
    """Solid box about its center: I_xx = m (b^2 + c^2) / 12, etc."""
    a, b, c = (float(v) for v in size)
    return np.diag([mass * (b * b + c * c) / 12.0, mass * (a * a + c * c) / 12.0, mass * (a * a + b * b) / 12.0])


def cylinder_inertia(mass: float, diameter: float, length: float, axis: str = "z") -> np.ndarray:
    """Solid cylinder about its center: I_axis = m r^2 / 2, I_perp = m (3 r^2 + h^2) / 12."""
    r = diameter / 2.0
    axial = 0.5 * mass * r * r
    perpendicular = mass * (3.0 * r * r + length * length) / 12.0
    diag = [perpendicular] * 3
    diag["xyz".index(axis)] = axial
    return np.diag(diag)


def combine(parts: list[MassProperties]) -> MassProperties:
    """Composite rigid body by the parallel-axis theorem:
    M = sum m_i, C = sum m_i c_i / M, I = sum (I_i + m_i (|d_i|^2 E - d_i d_i^T)), d_i = c_i - C."""
    mass = sum(p.mass for p in parts)
    if mass <= 0:
        return MassProperties(0.0, np.zeros(3), np.zeros((3, 3)))
    com = sum(p.mass * p.com for p in parts) / mass
    inertia = np.zeros((3, 3))
    for p in parts:
        d = p.com - com
        inertia += p.inertia + p.mass * (float(d @ d) * np.eye(3) - np.outer(d, d))
    source = "cad" if all(p.source == "cad" for p in parts) else ("mixed" if any(p.source == "cad" for p in parts) else "catalog")
    return MassProperties(mass, com, inertia, source)


def rotation_to_quat(rotation: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion (w, x, y, z), MuJoCo convention."""
    import mujoco

    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, np.ascontiguousarray(rotation, dtype=float).reshape(9))
    return quat


def quat_to_rotation(quat: Iterable[float]) -> np.ndarray:
    import mujoco

    mat = np.zeros(9)
    mujoco.mju_quat2Mat(mat, np.asarray(list(quat), dtype=float))
    return mat.reshape(3, 3)


def axis_rotation(axis: str) -> np.ndarray:
    """Rotation that maps the local z axis (MuJoCo cylinder axis) onto ``axis``."""
    if axis == "x":
        return rpy_matrix([0.0, np.pi / 2, 0.0])
    if axis == "y":
        return rpy_matrix([-np.pi / 2, 0.0, 0.0])
    return np.eye(3)


def yaw_of(rotation: np.ndarray) -> float:
    return float(np.arctan2(rotation[1, 0], rotation[0, 0]))
