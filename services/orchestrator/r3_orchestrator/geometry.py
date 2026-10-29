"""Convex solids in the robot frame and exact pairwise overlap volumes.

Catalog shapes become convex polyhedra (boxes exactly, cylinders as regular
prisms). Two convex solids intersect in a convex polyhedron that is the
intersection of both half-space sets; its volume is computed with qhull.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import linprog
from scipy.spatial import ConvexHull, HalfspaceIntersection, QhullError

from r3_model.graph import rpy_matrix

# A 48-gon prism under-estimates a cylinder's volume by (1 - sin(2*pi/N) * N / (2*pi)) = 0.29 %.
CYLINDER_SEGMENTS = 48
# Solids whose intersection cannot contain a ball of this radius only touch (shared faces).
TOUCH_TOLERANCE = 5e-5  # m (0.1 mm thick overlap)

_AXIS_ROTATION = {
    # Local prism frame (axis along +z) -> component frame for axis x / y / z.
    "x": np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]),
    "y": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]),
    "z": np.eye(3),
}


@dataclass
class Solid:
    """A component body as a convex polyhedron in the robot frame."""

    id: str
    shape: str
    vertices: np.ndarray  # (n, 3)
    halfspaces: np.ndarray  # (m, 4): normal . x + offset <= 0
    volume: float

    @property
    def aabb(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)


def local_vertices(shape: str, size: list[float], axis: str = "z") -> np.ndarray:
    """Vertices in the component frame, following the catalog convention: the
    origin is the center of the bottom (mounting) face for boxes and for
    z-axis cylinders; x/y-axis cylinders are centered on their axis at
    mid-length (wheels, motor cans)."""
    if shape in ("box", "custom"):
        sx, sy, sz = size
        corners = np.array([[x, y, z] for x in (-sx / 2, sx / 2) for y in (-sy / 2, sy / 2) for z in (0.0, sz)])
        return corners
    if shape == "cylinder":
        diameter, length = size[0], size[1]
        angles = np.linspace(0.0, 2 * np.pi, CYLINDER_SEGMENTS, endpoint=False)
        ring = np.stack([diameter / 2 * np.cos(angles), diameter / 2 * np.sin(angles)], axis=1)
        z0, z1 = (0.0, length) if axis == "z" else (-length / 2, length / 2)
        prism = np.vstack([np.column_stack([ring, np.full(len(ring), z0)]), np.column_stack([ring, np.full(len(ring), z1)])])
        return prism @ _AXIS_ROTATION[axis].T
    raise ValueError(f"unsupported shape {shape!r}")


def make_solid(solid_id: str, shape: str, size: list[float], axis: str, xyz: list[float], rpy: list[float]) -> Solid:
    vertices = local_vertices(shape, size, axis) @ rpy_matrix(rpy).T + np.asarray(xyz, dtype=float)
    hull = ConvexHull(vertices)
    return Solid(solid_id, shape, vertices, hull.equations, float(hull.volume))


def _chebyshev_center(halfspaces: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Center and radius of the largest ball inside {x : A x + b <= 0}."""
    normals, offsets = halfspaces[:, :3], halfspaces[:, 3]
    norms = np.linalg.norm(normals, axis=1)
    # maximize r  s.t.  A x + r |a_i| <= -b
    c = np.array([0.0, 0.0, 0.0, -1.0])
    a_ub = np.column_stack([normals, norms])
    result = linprog(c, A_ub=a_ub, b_ub=-offsets, bounds=[(None, None)] * 3 + [(0, None)], method="highs")
    if not result.success:
        return None
    return result.x[:3], float(result.x[3])


def overlap(a: Solid, b: Solid) -> dict[str, Any] | None:
    """Intersection of two solids, or None if they are disjoint or only touch.

    Returns ``volume`` (m^3), ``extent`` (AABB size of the intersection, m),
    ``depth`` (smallest extent, m) and ``center`` (m)."""
    amin, amax = a.aabb
    bmin, bmax = b.aabb
    if np.any(amax <= bmin) or np.any(bmax <= amin):
        return None
    halfspaces = np.vstack([a.halfspaces, b.halfspaces])
    found = _chebyshev_center(halfspaces)
    if found is None or found[1] < TOUCH_TOLERANCE:
        return None
    try:
        points = HalfspaceIntersection(halfspaces, found[0]).intersections
        hull = ConvexHull(points)
    except QhullError:
        return None
    low, high = points.min(axis=0), points.max(axis=0)
    extent = high - low
    return {
        "volume": float(hull.volume),
        "extent": extent.tolist(),
        "depth": float(extent.min()),
        "center": ((low + high) / 2).tolist(),
        "inscribed_radius": found[1],
    }
