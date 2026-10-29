"""Rigid frames: rotation (columns = axes in the parent frame) plus origin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from r3_catalog.schema import Placement


def _unit(v: Iterable[float]) -> np.ndarray:
    a = np.asarray(list(v), dtype=float)
    n = np.linalg.norm(a)
    if n < 1e-12:
        raise ValueError("zero-length axis")
    return a / n


def any_perpendicular(z: np.ndarray, hint: Sequence[float] = (0.0, 0.0, 1.0)) -> np.ndarray:
    """A unit vector perpendicular to ``z``, as close to ``hint`` as possible
    (falls back to world x, then y, when ``hint`` is parallel to ``z``)."""
    z = _unit(z)
    for candidate in (hint, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        c = np.asarray(candidate, dtype=float)
        p = c - (c @ z) * z
        if np.linalg.norm(p) > 1e-6:
            return p / np.linalg.norm(p)
    raise ValueError("no perpendicular found")


@dataclass(frozen=True)
class Frame:
    rotation: np.ndarray  # 3x3, columns are the frame's x, y, z axes in the parent frame
    origin: np.ndarray  # 3

    @classmethod
    def identity(cls) -> "Frame":
        return cls(np.eye(3), np.zeros(3))

    @classmethod
    def from_axes(cls, origin: Iterable[float], x: Iterable[float], z: Iterable[float]) -> "Frame":
        z_axis = _unit(z)
        x_axis = np.asarray(list(x), dtype=float)
        x_axis = _unit(x_axis - (x_axis @ z_axis) * z_axis)
        y_axis = np.cross(z_axis, x_axis)
        return cls(np.column_stack([x_axis, y_axis, z_axis]), np.asarray(list(origin), dtype=float))

    @classmethod
    def from_placement(cls, placement: Placement, scale: float = 1.0) -> "Frame":
        return cls.from_axes(np.asarray(placement.origin) * scale, placement.x, placement.z)

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> "Frame":
        return cls(np.asarray(matrix[:3, :3], dtype=float), np.asarray(matrix[:3, 3], dtype=float))

    @property
    def x(self) -> np.ndarray:
        return self.rotation[:, 0]

    @property
    def y(self) -> np.ndarray:
        return self.rotation[:, 1]

    @property
    def z(self) -> np.ndarray:
        return self.rotation[:, 2]

    def matrix(self) -> np.ndarray:
        out = np.eye(4)
        out[:3, :3] = self.rotation
        out[:3, 3] = self.origin
        return out

    def __matmul__(self, other: "Frame") -> "Frame":
        """``self @ other``: ``other`` (expressed in self) expressed in self's parent."""
        return Frame(self.rotation @ other.rotation, self.rotation @ other.origin + self.origin)

    def inverse(self) -> "Frame":
        rt = self.rotation.T
        return Frame(rt, -rt @ self.origin)

    def point(self, p: Iterable[float]) -> np.ndarray:
        return self.rotation @ np.asarray(list(p), dtype=float) + self.origin

    def points(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(pts, dtype=float) @ self.rotation.T + self.origin

    def vector(self, v: Iterable[float]) -> np.ndarray:
        return self.rotation @ np.asarray(list(v), dtype=float)

    def mated(self) -> "Frame":
        """The mating half's connector frame: same origin and x, z reversed
        (a half turn about x), so pin n of both halves coincide."""
        return Frame(self.rotation @ np.diag([1.0, -1.0, -1.0]), self.origin.copy())

    def to_json(self) -> dict[str, list[float]]:
        return {"origin": [round(float(v), 7) for v in self.origin], "x": [round(float(v), 7) for v in self.x],
                "y": [round(float(v), 7) for v in self.y], "z": [round(float(v), 7) for v in self.z]}


__all__ = ["Frame", "any_perpendicular"]
