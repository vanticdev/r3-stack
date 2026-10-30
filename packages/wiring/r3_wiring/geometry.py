"""Rigid geometric primitives with vectorized signed distance.

All coordinates are in metres in the robot frame (+x forward, +y left, +z up)
unless a docstring says otherwise. Every shape implements:

* ``sdf(points)``: signed distance (negative inside) for an ``(N, 3)`` array.
* ``aabb()``: axis-aligned bounds ``(lo, hi)``.
* ``transformed(rotation, translation)``: the shape after ``x' = R x + t``.
* ``surface_points(spacing)``: points on the boundary, used for reach checks.
* ``edges()``: polylines for wireframe rendering.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np


def rotation_about(axis: Iterable[float], angle: float) -> np.ndarray:
    """Rodrigues rotation matrix for a rotation of ``angle`` rad about ``axis``."""
    a = np.asarray(list(axis), dtype=float)
    a = a / np.linalg.norm(a)
    x, y, z = a
    c, s = math.cos(angle), math.sin(angle)
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + s * k + (1 - c) * (k @ k)


def quat_to_matrix(quat: Iterable[float]) -> np.ndarray:
    """Rotation matrix of a unit quaternion ``[w, x, y, z]`` (MuJoCo convention)."""
    w, x, y, z = (float(v) for v in quat)
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def matrix_to_quat(rotation: np.ndarray) -> np.ndarray:
    """Unit quaternion ``[w, x, y, z]`` of a rotation matrix (Shepperd's method)."""
    m = np.asarray(rotation, dtype=float)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2.0 * math.sqrt(trace + 1.0)
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = 2.0 * math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    q = np.asarray(q)
    return q / np.linalg.norm(q)


def axis_matrix(axis: str) -> np.ndarray:
    """Rotation that maps local +z onto the named catalog axis."""
    if axis == "x":
        return rotation_about([0, 1, 0], math.pi / 2)
    if axis == "y":
        return rotation_about([1, 0, 0], -math.pi / 2)
    return np.eye(3)


class Shape:
    """Base class; subclasses implement the vectorized interface."""

    def sdf(self, points: np.ndarray) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    def aabb(self) -> tuple[np.ndarray, np.ndarray]:  # pragma: no cover - interface
        raise NotImplementedError

    def transformed(self, rotation: np.ndarray, translation: np.ndarray) -> "Shape":  # pragma: no cover
        raise NotImplementedError

    def surface_points(self, spacing: float) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    def edges(self) -> list[np.ndarray]:  # pragma: no cover - interface
        raise NotImplementedError

    def gradient(self, points: np.ndarray, eps: float = 1e-5) -> np.ndarray:
        """Unit outward normal from central differences of the SDF."""
        points = np.atleast_2d(points)
        grad = np.empty_like(points)
        for axis in range(3):
            offset = np.zeros(3)
            offset[axis] = eps
            grad[:, axis] = self.sdf(points + offset) - self.sdf(points - offset)
        norm = np.linalg.norm(grad, axis=1, keepdims=True)
        norm[norm < 1e-12] = 1.0
        return grad / norm

    def sdf_bounded(self, points: np.ndarray, cutoff: float) -> np.ndarray:
        """SDF evaluated only near the shape; far points get ``cutoff``."""
        lo, hi = self.aabb()
        near = np.all((points >= lo - cutoff) & (points <= hi + cutoff), axis=1)
        out = np.full(len(points), cutoff, dtype=float)
        if near.any():
            out[near] = np.minimum(self.sdf(points[near]), cutoff)
        return out


@dataclass
class Box(Shape):
    """Oriented box: ``rotation`` columns are the local axes."""

    center: np.ndarray
    rotation: np.ndarray
    half: np.ndarray

    def sdf(self, points: np.ndarray) -> np.ndarray:
        q = np.abs((np.atleast_2d(points) - self.center) @ self.rotation) - self.half
        outside = np.linalg.norm(np.maximum(q, 0.0), axis=1)
        inside = np.minimum(q.max(axis=1), 0.0)
        return outside + inside

    def aabb(self) -> tuple[np.ndarray, np.ndarray]:
        extent = np.abs(self.rotation) @ self.half
        return self.center - extent, self.center + extent

    def transformed(self, rotation: np.ndarray, translation: np.ndarray) -> "Box":
        return Box(rotation @ self.center + translation, rotation @ self.rotation, self.half.copy())

    def corners(self) -> np.ndarray:
        signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float)
        return self.center + (signs * self.half) @ self.rotation.T

    def surface_points(self, spacing: float) -> np.ndarray:
        points = []
        for axis in range(3):
            u, v = [a for a in range(3) if a != axis]
            nu = max(2, int(math.ceil(2 * self.half[u] / spacing)) + 1)
            nv = max(2, int(math.ceil(2 * self.half[v] / spacing)) + 1)
            gu, gv = np.meshgrid(np.linspace(-self.half[u], self.half[u], nu), np.linspace(-self.half[v], self.half[v], nv))
            for sign in (-1.0, 1.0):
                local = np.zeros((gu.size, 3))
                local[:, u], local[:, v], local[:, axis] = gu.ravel(), gv.ravel(), sign * self.half[axis]
                points.append(local)
        local = np.vstack(points)
        return self.center + local @ self.rotation.T

    def edges(self) -> list[np.ndarray]:
        c = self.corners()
        pairs = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
        return [np.vstack([c[a], c[b]]) for a, b in pairs]

    def faces(self) -> list[np.ndarray]:
        c = self.corners()
        quads = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]
        return [c[list(q)] for q in quads]


@dataclass
class Cylinder(Shape):
    """Capped cylinder whose axis is the local +z column of ``rotation``."""

    center: np.ndarray
    rotation: np.ndarray
    radius: float
    half_length: float

    @property
    def axis(self) -> np.ndarray:
        return self.rotation[:, 2]

    def sdf(self, points: np.ndarray) -> np.ndarray:
        q = (np.atleast_2d(points) - self.center) @ self.rotation
        radial = np.hypot(q[:, 0], q[:, 1]) - self.radius
        axial = np.abs(q[:, 2]) - self.half_length
        outside = np.hypot(np.maximum(radial, 0.0), np.maximum(axial, 0.0))
        inside = np.minimum(np.maximum(radial, axial), 0.0)
        return outside + inside

    def aabb(self) -> tuple[np.ndarray, np.ndarray]:
        a = self.axis
        extent = np.abs(a) * self.half_length + self.radius * np.sqrt(np.clip(1.0 - a * a, 0.0, 1.0))
        return self.center - extent, self.center + extent

    def transformed(self, rotation: np.ndarray, translation: np.ndarray) -> "Cylinder":
        return Cylinder(rotation @ self.center + translation, rotation @ self.rotation, self.radius, self.half_length)

    def _ring(self, z: float, radius: float, count: int) -> np.ndarray:
        angles = np.linspace(0.0, 2 * math.pi, count, endpoint=False)
        local = np.column_stack([radius * np.cos(angles), radius * np.sin(angles), np.full(count, z)])
        return self.center + local @ self.rotation.T

    def surface_points(self, spacing: float) -> np.ndarray:
        count = max(8, int(math.ceil(2 * math.pi * self.radius / spacing)))
        rings = max(2, int(math.ceil(2 * self.half_length / spacing)) + 1)
        points = [self._ring(z, self.radius, count) for z in np.linspace(-self.half_length, self.half_length, rings)]
        for radius in np.arange(spacing, self.radius, spacing):
            n = max(6, int(math.ceil(2 * math.pi * radius / spacing)))
            points += [self._ring(-self.half_length, radius, n), self._ring(self.half_length, radius, n)]
        points.append(np.vstack([self.center + self.axis * self.half_length, self.center - self.axis * self.half_length]))
        return np.vstack(points)

    def edges(self) -> list[np.ndarray]:
        top = self._ring(self.half_length, self.radius, 36)
        bottom = self._ring(-self.half_length, self.radius, 36)
        lines = [np.vstack([top, top[:1]]), np.vstack([bottom, bottom[:1]])]
        for k in range(0, 36, 9):
            lines.append(np.vstack([top[k], bottom[k]]))
        return lines


def cluster_decimate(vertices: np.ndarray, faces: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Vertex-clustering decimation: merge vertices on a ``cell`` grid and drop the
    triangles that collapse. A closed mesh stays closed (its winding number stays
    1 inside); a vertex moves by at most cell * sqrt(3) to its cell's mean. Used for routing
    obstacles, whose voxels are millimetres, not for the CAD fit checks."""
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=int)
    keys = np.floor(vertices / cell).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    merged = np.zeros((counts.size, 3))
    np.add.at(merged, inverse, vertices)
    merged /= counts[:, None]
    tri = inverse[faces]
    tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 2] != tri[:, 0])]
    # Push forward the oriented triangle chain, including multiplicity. Opposite
    # faces cancel, but two equally wound faces must both remain: deduplicating
    # only that face would leave its neighbouring edges unbalanced.
    inversions = ((tri[:, 0] > tri[:, 1]).astype(int) +
                  (tri[:, 0] > tri[:, 2]) + (tri[:, 1] > tri[:, 2]))
    keys, inverse = np.unique(np.sort(tri, axis=1), axis=0, return_inverse=True)
    net = np.zeros(len(keys), dtype=int)
    np.add.at(net, inverse.reshape(-1), np.where(inversions % 2, -1, 1))
    keep = net != 0
    oriented, counts = keys[keep].copy(), net[keep]
    oriented[counts < 0] = oriented[counts < 0][:, [0, 2, 1]]
    return merged, np.repeat(oriented, np.abs(counts), axis=0)


_POOL: Any = None


def _pool() -> Any:
    """Shared thread pool for chunked array work (winding numbers)."""
    global _POOL
    if _POOL is None:
        import os
        from concurrent.futures import ThreadPoolExecutor

        _POOL = ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1))
    return _POOL


def _workers(count: int) -> int:
    """KD-tree query threads: all cores for large batches (voxel fields), one for small ones
    (thread start-up would dominate)."""
    return -1 if count >= 4096 else 1


class MeshShape(Shape):
    """Closed triangle mesh (e.g. a CAD STL export). Distance is measured to a
    dense set of surface samples. The sign is exact for closed meshes: points
    outside the bounding box are outside, others use the generalized winding
    number (sum of the triangles' solid angles / 4 pi, Van Oosterom & Strackee
    1983). The sign of the nearest sample's face normal is wrong next to edges
    and corners, where the nearest sample can lie on a face whose plane the
    point is behind."""

    def __init__(self, vertices: np.ndarray, faces: np.ndarray, spacing: float = 0.0015,
                 samples: tuple[np.ndarray, np.ndarray] | None = None,
                 base: "MeshShape | None" = None, pose: tuple[np.ndarray, np.ndarray] | None = None) -> None:
        self.vertices = np.asarray(vertices, dtype=float)
        self.faces = np.asarray(faces, dtype=int)
        self.spacing = spacing
        # A rigidly moved copy (``transformed``) keeps its base mesh and pose (x = R x_base + t):
        # distances and winding numbers are evaluated on the base in its own frame, so the
        # hundreds of posed copies of a swept envelope share one sample set and KD-tree.
        self._base = base
        self._pose = pose
        self._local: tuple[np.ndarray, np.ndarray, Any] | None = None
        if base is None:
            self._local = (*(samples if samples is not None else self._sample(spacing)), None)

    def _own(self) -> tuple[np.ndarray, np.ndarray, Any]:
        """World-frame samples, normals and KD-tree (built on first use for posed copies)."""
        from scipy.spatial import cKDTree

        if self._local is None:
            rotation, translation = self._pose
            base_samples, base_normals = self._base._samples, self._base._normals
            self._local = (base_samples @ rotation.T + translation, base_normals @ rotation.T, None)
        if self._local[2] is None:
            self._local = (self._local[0], self._local[1], cKDTree(self._local[0]))
        return self._local

    @property
    def _samples(self) -> np.ndarray:
        if self._local is None:
            self._own()
        return self._local[0]

    @property
    def _normals(self) -> np.ndarray:
        if self._local is None:
            self._own()
        return self._local[1]

    @property
    def _tree(self) -> Any:
        return self._own()[2]

    def _sample(self, spacing: float) -> tuple[np.ndarray, np.ndarray]:
        """Barycentric grid of each triangle with n = ceil(longest edge / spacing)
        subdivisions (same points as a per-triangle loop, vectorized per n)."""
        tri = self.vertices[self.faces]
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        normal = np.cross(b - a, c - a)
        area2 = np.linalg.norm(normal, axis=1)
        keep = area2 >= 1e-14
        a, b, c, normal, area2 = a[keep], b[keep], c[keep], normal[keep], area2[keep]
        normal = normal / area2[:, None]
        longest = np.max(np.stack([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - a, axis=1),
                                   np.linalg.norm(c - b, axis=1)]), axis=0)
        counts = np.maximum(1, np.ceil(longest / spacing).astype(int))
        chunks: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        for n in np.unique(counts):
            idx = np.flatnonzero(counts == n)
            uv = np.array([(i / n, j / n) for i in range(n + 1) for j in range(n + 1 - i)])
            pts = a[idx, None, :] + uv[None, :, :1] * (b - a)[idx, None, :] + uv[None, :, 1:] * (c - a)[idx, None, :]
            chunks.append((idx, pts.reshape(-1, 3), np.repeat(normal[idx], len(uv), axis=0)))
        # Restore triangle order (only affects KD-tree tie-breaking).
        order = np.argsort(np.concatenate([np.repeat(idx, len(p) // len(idx)) for idx, p, _ in chunks]), kind="stable")
        points = np.vstack([p for _, p, _ in chunks])[order]
        normals = np.vstack([nrm for _, _, nrm in chunks])[order]
        return points, normals

    def winding_number(self, points: np.ndarray) -> np.ndarray:
        """Generalized winding number of each point (1 inside a closed, outward mesh, 0 outside)."""
        points = np.atleast_2d(points)
        tri = self.vertices[self.faces]
        out = np.zeros(len(points))

        def chunk(start: int) -> None:
            rel = tri[None, :, :, :] - points[start:start + 256, None, None, :]  # (p, f, 3, 3)
            length = np.linalg.norm(rel, axis=3)
            ra, rb, rc = rel[:, :, 0], rel[:, :, 1], rel[:, :, 2]
            la, lb, lc = length[:, :, 0], length[:, :, 1], length[:, :, 2]
            numerator = np.einsum("pfi,pfi->pf", ra, np.cross(rb, rc))
            denominator = (la * lb * lc + np.einsum("pfi,pfi->pf", ra, rb) * lc
                           + np.einsum("pfi,pfi->pf", rb, rc) * la + np.einsum("pfi,pfi->pf", rc, ra) * lb)
            out[start:start + 256] = np.arctan2(numerator, denominator).sum(axis=1) / (2.0 * math.pi)

        starts = range(0, len(points), 256)
        if len(starts) > 1:  # independent chunks; numpy releases the GIL in these array kernels
            list(_pool().map(chunk, starts))
        else:
            for start in starts:
                chunk(start)
        return out

    def _query(self, points: np.ndarray, bound: float = np.inf) -> tuple[np.ndarray, np.ndarray]:
        """(nearest-sample distance, sign) of world points; distances beyond ``bound`` are inf."""
        lo, hi = self.aabb()
        candidate = np.all((points >= lo) & (points <= hi), axis=1)
        if self._base is not None:  # evaluate on the base mesh, in its frame (rigid: same distances)
            rotation, translation = self._pose
            local = (points - translation) @ rotation
            distance, _ = self._base._tree.query(local, distance_upper_bound=bound, workers=_workers(len(local)))
            mesh, query = self._base, local
        else:
            distance, _ = self._tree.query(points, distance_upper_bound=bound, workers=_workers(len(points)))
            mesh, query = self, points
        sign = np.ones(len(points))
        if candidate.any():
            sign[candidate] = np.where(mesh.winding_number(query[candidate]) > 0.5, -1.0, 1.0)
        return distance, sign

    def sdf(self, points: np.ndarray) -> np.ndarray:
        distance, sign = self._query(np.atleast_2d(points))
        return distance * sign

    def sdf_bounded(self, points: np.ndarray, cutoff: float) -> np.ndarray:
        """``min(sdf, cutoff)`` near the mesh: the KD-tree stops searching at ``cutoff`` (exact,
        points deeper than ``cutoff`` inside are re-measured without the bound)."""
        lo, hi = self.aabb()
        near = np.all((points >= lo - cutoff) & (points <= hi + cutoff), axis=1)
        out = np.full(len(points), cutoff, dtype=float)
        if near.any():
            sub = points[near]
            distance, sign = self._query(sub, bound=cutoff)
            deep = ~np.isfinite(distance) & (sign < 0)
            if deep.any():
                distance[deep], _ = self._query(sub[deep])
            values = np.where(np.isfinite(distance), distance * sign, cutoff)
            out[near] = np.minimum(values, cutoff)
        return out

    def aabb(self) -> tuple[np.ndarray, np.ndarray]:
        if not hasattr(self, "_aabb"):
            self._aabb = (self.vertices.min(axis=0), self.vertices.max(axis=0))
        return self._aabb

    def transformed(self, rotation: np.ndarray, translation: np.ndarray) -> "MeshShape":
        # Rigid motion: keep the base mesh's surface samples and KD-tree instead of re-sampling.
        rotation, translation = np.asarray(rotation, float), np.asarray(translation, float)
        if self._base is not None:
            r0, t0 = self._pose
            base, pose = self._base, (rotation @ r0, rotation @ t0 + translation)
        else:
            base, pose = self, (rotation, translation)
        return MeshShape(self.vertices @ rotation.T + translation, self.faces, self.spacing, base=base, pose=pose)

    def surface_points(self, spacing: float) -> np.ndarray:
        step = max(1, int(spacing / self.spacing))
        return self._samples[::step]

    def edges(self) -> list[np.ndarray]:
        lo, hi = self.aabb()
        return Box((lo + hi) / 2, np.eye(3), (hi - lo) / 2).edges()


class SweptShape(Shape):
    """Union of posed copies of a shape: the envelope a moving part sweeps
    through its joint travel. Distances are the minimum over samples; with a
    1 deg step the envelope is underestimated by at most r*(1 - cos 0.5 deg)
    radially and ~r*0.5 deg tangentially (0.5 mm for a 55 mm wheel)."""

    def __init__(self, shapes: list[Shape], label: str = "") -> None:
        self.shapes = shapes
        self.label = label

    def sdf(self, points: np.ndarray) -> np.ndarray:
        points = np.atleast_2d(points)
        out = np.full(len(points), np.inf)
        for shape in self.shapes:
            np.minimum(out, shape.sdf(points), out=out)
        return out

    def sdf_bounded(self, points: np.ndarray, cutoff: float) -> np.ndarray:
        lo, hi = self.aabb()
        near = np.all((points >= lo - cutoff) & (points <= hi + cutoff), axis=1)
        out = np.full(len(points), cutoff, dtype=float)
        if near.any():
            sub = points[near]
            values = np.full(len(sub), cutoff)
            for shape in self.shapes:
                np.minimum(values, shape.sdf_bounded(sub, cutoff), out=values)
            out[near] = values
        return out

    def aabb(self) -> tuple[np.ndarray, np.ndarray]:
        bounds = [shape.aabb() for shape in self.shapes]
        return np.min([b[0] for b in bounds], axis=0), np.max([b[1] for b in bounds], axis=0)

    def transformed(self, rotation: np.ndarray, translation: np.ndarray) -> "SweptShape":
        return SweptShape([s.transformed(rotation, translation) for s in self.shapes], self.label)

    def surface_points(self, spacing: float) -> np.ndarray:
        return np.vstack([shape.surface_points(spacing) for shape in self.shapes])

    def edges(self) -> list[np.ndarray]:
        stride = max(1, len(self.shapes) // 6)
        picks = self.shapes[::stride]
        if self.shapes[-1] is not picks[-1]:
            picks.append(self.shapes[-1])
        return [edge for shape in picks for edge in shape.edges()]


def load_stl(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a binary or ASCII STL; returns (vertices, faces) with shared vertices merged."""
    data = Path(path).read_bytes()
    triangles: np.ndarray
    if data[:5] == b"solid" and b"facet" in data[:400]:
        values = [float(tok) for line in data.decode(errors="ignore").splitlines()
                  if line.strip().startswith("vertex") for tok in line.split()[1:4]]
        triangles = np.asarray(values, dtype=float).reshape(-1, 3, 3)
    else:
        count = struct.unpack_from("<I", data, 80)[0]
        record = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
        triangles = np.frombuffer(data, dtype=record, count=count, offset=84)["v"].astype(float)
    flat = triangles.reshape(-1, 3)
    vertices, inverse = np.unique(np.round(flat, 9), axis=0, return_inverse=True)
    return vertices, inverse.reshape(-1, 3)


def polyline_length(points: np.ndarray) -> float:
    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def resample_polyline(points: np.ndarray, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Points every <= ``spacing`` along a polyline (vertices kept) and their arc length."""
    points = np.asarray(points, dtype=float)
    out, arc = [points[0]], [0.0]
    total = 0.0
    for a, b in zip(points[:-1], points[1:]):
        length = float(np.linalg.norm(b - a))
        n = max(1, int(math.ceil(length / spacing)))
        for k in range(1, n + 1):
            out.append(a + (b - a) * (k / n))
            arc.append(total + length * k / n)
        total += length
    return np.asarray(out), np.asarray(arc)


def point_at_arc(points: np.ndarray, s: float) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    s = float(np.clip(s, 0.0, cum[-1]))
    k = int(np.searchsorted(cum, s, side="right") - 1)
    k = min(k, len(seg) - 1)
    t = 0.0 if seg[k] == 0 else (s - cum[k]) / seg[k]
    return points[k] + (points[k + 1] - points[k]) * t


def project_to_polyline(points: np.ndarray, query: np.ndarray) -> tuple[float, float]:
    """(distance, arc length) of the closest point on a polyline to ``query``."""
    points = np.asarray(points, dtype=float)
    a, b = points[:-1], points[1:]
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom[denom == 0] = 1.0
    t = np.clip(np.einsum("ij,ij->i", query - a, ab) / denom, 0.0, 1.0)
    closest = a + ab * t[:, None]
    dist = np.linalg.norm(closest - query, axis=1)
    k = int(np.argmin(dist))
    seg = np.linalg.norm(ab, axis=1)
    return float(dist[k]), float(seg[:k].sum() + seg[k] * t[k])


# Diameter ratio D/d of the smallest circle enclosing n equal circles
# (Graham & Lubachevsky packings; n = 1..12). Used for loose bundle OD.
_PACKING_RATIO = [1.0, 2.0, 2.1547, 2.4142, 2.7013, 3.0, 3.0, 3.3048, 3.6131, 3.8130, 3.9238, 4.0296]


def bundle_diameter(diameters: Iterable[float]) -> float:
    """Outer diameter of a round bundle of conductors with the given ODs:
    D = k(n) * d_rms, with k(n) from optimal circle packings; for n > 12 the
    area-based estimate D = sqrt(sum d^2 / 0.75)."""
    d = np.asarray(list(diameters), dtype=float)
    if d.size == 0:
        return 0.0
    if d.size == 1:
        return float(d[0])
    if d.size <= len(_PACKING_RATIO):
        return float(_PACKING_RATIO[d.size - 1] * math.sqrt(float(np.mean(d * d))))
    return float(math.sqrt(float(np.sum(d * d)) / 0.75))
