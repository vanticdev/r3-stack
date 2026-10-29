"""Elastic-band relaxation of routed bundle segments.

A string-pulled path hugs obstacle corners, which no cable can follow. Each
bundle segment is relaxed as a discrete elastic rod: explicit gradient steps
on the bending energy ``E = sum |x[i-1] - 2 x[i] + x[i+1]|^2`` (a bi-Laplacian
flow, which pushes the neighbours of a kink *outward* so the cable stands off
a corner), a small tension term, and a weak spring to the routed path so the
route keeps its surface-following character. After every step points are
projected out of the clearance field ``F(x) >= r`` (static keepouts, plates
away from drilled holes, and moving envelopes plus the clearance requirement).
Connector leads are clamped (fixed points), giving a tangent boundary
condition; junction ends use a free (zero-curvature) boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .geometry import resample_polyline

FieldFn = Callable[[np.ndarray], np.ndarray]


@dataclass
class RelaxSegment:
    head: np.ndarray  # fixed leading points (connector lead), >= 1 point
    free: np.ndarray  # free interior points
    tail: np.ndarray  # fixed trailing points, >= 1 point
    radius: float  # bundle radius
    bend_radius: float = 0.01  # target minimum bend radius of the bundle
    exempt: np.ndarray | None = None  # free points never projected (inside termination zones)

    def points(self) -> np.ndarray:
        return np.vstack([self.head, self.free, self.tail])


def _uniform(points: np.ndarray, count: int) -> np.ndarray:
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    if cum[-1] <= 0:
        return np.repeat(points[:1], count, axis=0)
    targets = np.linspace(0.0, cum[-1], count)
    return np.column_stack([np.interp(targets, cum, points[:, a]) for a in range(3)])


def make_segment(polyline: np.ndarray, head_fixed: int, tail_fixed: int, radius: float, spacing: float,
                 bend_radius: float = 0.01) -> RelaxSegment:
    """Split a polyline into clamped head/tail runs and a uniformly resampled free part."""
    polyline = np.asarray(polyline, dtype=float)
    head_pts = polyline[: max(1, head_fixed)]
    tail_pts = polyline[len(polyline) - max(1, tail_fixed):]
    middle = polyline[max(1, head_fixed) - 1: len(polyline) - max(1, tail_fixed) + 1]
    head, _ = resample_polyline(head_pts, spacing) if len(head_pts) > 1 else (head_pts, None)
    tail, _ = resample_polyline(tail_pts, spacing) if len(tail_pts) > 1 else (tail_pts, None)
    length = float(np.linalg.norm(np.diff(middle, axis=0), axis=1).sum()) if len(middle) > 1 else 0.0
    count = max(0, int(math.ceil(length / spacing)) - 1)
    free = _uniform(middle, count + 2)[1:-1] if count > 0 else np.zeros((0, 3))
    return RelaxSegment(head, free, tail, radius, bend_radius)


def _gradient(field: FieldFn, points: np.ndarray, eps: float) -> np.ndarray:
    grad = np.zeros_like(points)
    for axis in range(3):
        offset = np.zeros(3)
        offset[axis] = eps
        grad[:, axis] = field(points + offset) - field(points - offset)
    norm = np.linalg.norm(grad, axis=1, keepdims=True)
    norm[norm < 1e-12] = 1.0
    return grad / norm


def _first_difference(m: int):
    from scipy.sparse import diags

    return diags([-np.ones(m - 1), np.ones(m - 1)], [0, 1], shape=(m - 1, m))


def _second_difference(m: int):
    from scipy.sparse import diags

    return diags([np.ones(m - 2), -2 * np.ones(m - 2), np.ones(m - 2)], [0, 1, 2], shape=(m - 2, m))


def relax(segments: list[RelaxSegment], field: FieldFn, iterations: int = 40, spacing: float = 0.002,
          anchor: float = 0.02, eps: float = 0.001, margin: float = 0.0003, penalty: float = 1.0e6,
          stiffness_scale: float = 2.0, max_step: float = 0.0015) -> None:
    """In-place relaxation of all segments (each independently).

    Each iteration solves the proximal, constrained smoothing step
    ``min |x - y|^2 + a |x - x0|^2 + k |D2 x|^2 + T |D1 x|^2 + w sum_active (n_i . (x_i - q_i))^2``
    with the clamped points fixed. ``k = (2 R / h)^4`` smooths over the
    target bend radius ``R``; points that violate the clearance field become
    active half-space constraints (normal ``n_i`` from the field gradient,
    ``q_i`` on the required clearance surface; released when their multiplier
    turns negative), so the rod bends *around* obstacles with its stiffness
    instead of being kinked by projection."""
    from scipy.sparse import eye, kron
    from scipy.sparse.linalg import spsolve

    for segment in segments:
        n = len(segment.free)
        if n == 0:
            continue
        head, tail = segment.head, segment.tail
        m = len(head) + n + len(tail)
        d2 = _second_difference(m).tocsc()
        free_cols = np.arange(len(head), len(head) + n)
        fixed_cols = np.concatenate([np.arange(len(head)), np.arange(len(head) + n, m)])
        d2f, d2c = d2[:, free_cols], d2[:, fixed_cols]
        fixed = np.vstack([head, tail])
        k = (stiffness_scale * segment.bend_radius / spacing) ** 4
        # Weak tension (cable pulled taut between ties) with bending/tension
        # length scale sqrt(EI/T) = 4 R, so the rod neither balloons nor hugs corners.
        tension = k / (4.0 * segment.bend_radius / spacing) ** 2
        d1 = _first_difference(m).tocsc()
        d1f, d1c = d1[:, free_cols], d1[:, fixed_cols]
        bending = kron(k * (d2f.T @ d2f) + tension * (d1f.T @ d1f), eye(3)).tocsc()
        coupling = k * (d2f.T @ (d2c @ fixed)) + tension * (d1f.T @ (d1c @ fixed))  # (n, 3)
        # Proximal weight: one step smooths over ~1 R, so the rod moves
        # gradually and cannot jump through obstacles between constraint updates.
        prox = k / (segment.bend_radius / spacing) ** 4
        original = segment.free.copy()
        # Terminations may sit inside a keepout (a connector 1.5 mm above the
        # deck). The required clearance ramps from the end's own depth to the
        # full radius over ``ramp`` so the cable leaves it smoothly.
        ramp = max(2.0 * segment.bend_radius, 0.006)
        depth_head = max(0.0, segment.radius - float(field(head[-1:])[0]))
        depth_tail = max(0.0, segment.radius - float(field(tail[:1])[0]))
        index = np.arange(n)
        d_head, d_tail = (index + 1) * spacing, (n - index) * spacing
        target = segment.radius - depth_head * np.clip(1 - d_head / ramp, 0, 1) - depth_tail * np.clip(1 - d_tail / ramp, 0, 1)
        segment.exempt = np.zeros(n, dtype=bool)
        normals = np.zeros((n, 3))
        offsets = np.zeros(n)
        active = np.zeros(n, dtype=bool)
        x = original.copy()
        buffer = 0.0005  # activate constraints slightly before contact
        extra = 0
        it = 0
        while True:
            values = field(x)
            violating = values < target + buffer
            if it >= iterations and (not np.any(values < target - 1e-4) or extra >= 15):
                break
            if it >= iterations:
                extra += 1
            if violating.any():
                grad = _gradient(field, x[violating], eps)
                normals[violating] = grad
                goal = x[violating] + grad * (target[violating] + buffer - values[violating])[:, None]
                offsets[violating] = np.einsum("ij,ij->i", grad, goal)
                active |= violating
            release = active & (values > target + 0.003)
            active &= ~release
            constraint = _block_diag(normals, np.where(active, penalty, 0.0))
            system = (prox * (1 + anchor)) * eye(3 * n) + bending + constraint
            rhs = (prox * (x + anchor * original) - coupling).ravel()
            rhs += (normals * (offsets * np.where(active, penalty, 0.0))[:, None]).ravel()
            proposal = spsolve(system.tocsc(), rhs).reshape(n, 3)
            step = proposal - x
            length = np.linalg.norm(step, axis=1, keepdims=True)
            x = x + step * np.minimum(1.0, max_step / np.maximum(length, 1e-12))
            # Half-space, not equality: a constraint that pulls its point toward
            # the obstacle (point on the free side of its plane) is released.
            pulling = active & (np.einsum("ij,ij->i", normals, x) > offsets + 1e-7)
            active &= ~pulling
            if it % 5 == 4 and it < iterations:
                chain = np.vstack([head[-1:], x, tail[:1]])
                x = _uniform(chain, n + 2)[1:-1]
            it += 1
        # Final safety projection (small residuals of the penalty method only).
        for _ in range(2):
            values = field(x)
            inside = values < target - margin
            if not inside.any():
                break
            grad = _gradient(field, x[inside], eps)
            x[inside] += grad * (target[inside] - values[inside] + margin)[:, None]
        segment.free = x


def _block_diag(normals: np.ndarray, weights: np.ndarray):
    """Sparse block-diagonal matrix of ``w_i n_i n_i^T`` (3x3 blocks)."""
    from scipy.sparse import csc_matrix

    n = len(normals)
    blocks = weights[:, None, None] * np.einsum("ij,ik->ijk", normals, normals)
    base = 3 * np.arange(n)
    rows = (base[:, None, None] + np.arange(3)[None, :, None]).repeat(3, axis=2)
    cols = (base[:, None, None] + np.arange(3)[None, None, :]).repeat(3, axis=1)
    return csc_matrix((blocks.ravel(), (rows.ravel(), cols.ravel())), shape=(3 * n, 3 * n))


def curvature_radius(points: np.ndarray) -> np.ndarray:
    """Discrete radius of curvature at interior vertices: R = l / (2 sin(phi/2)),
    l = mean adjacent edge length (exact for points sampled on a circle)."""
    points = np.asarray(points, dtype=float)
    if len(points) < 3:
        return np.zeros(0)
    a, b = points[1:-1] - points[:-2], points[2:] - points[1:-1]
    la, lb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    cos = np.einsum("ij,ij->i", a, b) / np.maximum(la * lb, 1e-15)
    phi = np.arccos(np.clip(cos, -1.0, 1.0))
    with np.errstate(divide="ignore"):
        return np.where(phi > 1e-6, 0.5 * (la + lb) / (2 * np.sin(phi / 2)), np.inf)


def radius_at_scale(points: np.ndarray, delta: float = 0.003, step: float = 0.0005) -> np.ndarray:
    """Circumradius of (p(s - delta), p(s), p(s + delta)) along arc length:
    a bend-radius measurement insensitive to vertex spacing artefacts."""
    points = np.asarray(points, dtype=float)
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    if cum[-1] < 2 * delta:
        return np.zeros(0)
    s = np.arange(delta, cum[-1] - delta, step)

    def at(values: np.ndarray) -> np.ndarray:
        return np.column_stack([np.interp(values, cum, points[:, a]) for a in range(3)])

    a, b, c = at(s - delta), at(s), at(s + delta)
    ab, bc, ca = np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)
    area2 = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(area2 > 1e-15, ab * bc * ca / (2 * area2), np.inf)
