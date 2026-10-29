"""Steering linkage solver: servo horn -> two tie rods -> steering arms -> knuckles.

Each side is a spatial four-bar: the horn ball ``H`` turns about the servo
axis, the knuckle ball ``K`` turns about the kingpin axis, and the tie rod keeps
``|H - K|`` equal to its length at the modeled (straight-ahead) pose. For every
servo angle the knuckle angle ``phi`` is found by Newton iteration continued from
the previous sample, so the solution stays on the assembled branch.

Diagnostics:
* lock-up: no ``phi`` satisfies the length constraint (the rod cannot reach);
* toggle: the constraint Jacobian ``d|H-K|/dphi`` vanishes, i.e. the tie rod
  lines up with the steering arm (transmission angle -> 0) and the knuckle
  angle stops being a function of the servo angle;
* unreachable limits: over full servo travel a knuckle exceeds its joint limit
  (the servo would drive it into the stop) or never reaches it.

Ackermann percentage at each outer angle d_o: 100 (d_i - d_o) / (d_i* - d_o) with
the ideal inner angle cot(d_i*) = cot(d_o) - track / wheelbase.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


def rotation(axis: Sequence[float], angle: float) -> np.ndarray:
    """Rodrigues rotation matrix about unit ``axis``."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    k = np.array([[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]])
    return np.eye(3) + math.sin(angle) * k + (1.0 - math.cos(angle)) * (k @ k)


@dataclass
class Side:
    """One side of the linkage, all points in the robot frame (m)."""

    name: str
    joint: str
    kingpin_point: np.ndarray
    kingpin_axis: np.ndarray
    knuckle_ball: np.ndarray
    horn_ball: np.ndarray
    limits: tuple[float, float]

    @property
    def rod_length(self) -> float:
        return float(np.linalg.norm(self.horn_ball - self.knuckle_ball))


@dataclass
class Linkage:
    servo_point: np.ndarray
    servo_axis: np.ndarray
    sides: list[Side]
    wheelbase: float
    track: float  # kingpin track


@dataclass
class SideSolution:
    phi: list[float] = field(default_factory=list)
    transmission_deg: list[float] = field(default_factory=list)
    residual: list[float] = field(default_factory=list)
    status: str = "ok"  # ok | lockup | toggle
    failed_at: float | None = None


def horn_ball(linkage: Linkage, side: Side, theta: float) -> np.ndarray:
    return linkage.servo_point + rotation(linkage.servo_axis, theta) @ (side.horn_ball - linkage.servo_point)


def knuckle_ball(side: Side, phi: float) -> np.ndarray:
    return side.kingpin_point + rotation(side.kingpin_axis, phi) @ (side.knuckle_ball - side.kingpin_point)


def _residual(linkage: Linkage, side: Side, theta: float, phi: float) -> tuple[float, float, float]:
    """Constraint g = |H-K| - L, dg/dphi, and the transmission angle (deg)."""
    h = horn_ball(linkage, side, theta)
    k = knuckle_ball(side, phi)
    d = h - k
    dist = float(np.linalg.norm(d))
    a = side.kingpin_axis / np.linalg.norm(side.kingpin_axis)
    arm = k - side.kingpin_point
    dk = np.cross(a, arm)  # dK/dphi
    dg = float(-(d @ dk) / dist)
    # Transmission angle: between the tie rod and the knuckle ball's velocity
    # direction; 90 deg is ideal, 0 deg is toggle.
    cos_t = abs(float(d @ dk)) / (dist * float(np.linalg.norm(dk)))
    return dist - side.rod_length, dg, math.degrees(math.asin(min(1.0, cos_t)))


def solve_side(linkage: Linkage, side: Side, thetas: Sequence[float], toggle_deg: float = 10.0) -> SideSolution:
    """Continuation solve of phi(theta) starting from the modeled pose (theta=phi=0).
    ``thetas`` must be sorted and contain 0."""
    out = SideSolution()
    order = np.argsort(np.abs(np.asarray(thetas)))
    results: dict[int, tuple[float, float, float]] = {}
    for direction in (1, -1):
        phi = 0.0
        for index in [i for i in order if (thetas[i] >= 0 if direction > 0 else thetas[i] < 0)]:
            theta = float(thetas[index])
            converged = False
            for _ in range(60):
                g, dg, trans = _residual(linkage, side, theta, phi)
                if abs(dg) < 1e-9:
                    break
                step = g / dg
                phi -= max(-0.2, min(0.2, step))
                if abs(g) < 1e-10:
                    converged = True
                    break
            g, dg, trans = _residual(linkage, side, theta, phi)
            if not converged and abs(g) > 1e-7:
                out.status, out.failed_at = "lockup", theta
                break
            if trans < toggle_deg:
                out.status, out.failed_at = "toggle", theta
            results[index] = (phi, trans, g)
    for i in range(len(thetas)):
        phi, trans, g = results.get(i, (float("nan"), float("nan"), float("nan")))
        out.phi.append(phi)
        out.transmission_deg.append(trans)
        out.residual.append(g)
    return out


def ideal_inner(outer: float, track: float, wheelbase: float) -> float:
    """Ideal Ackermann inner angle for an outer angle (both > 0)."""
    if outer <= 0:
        return 0.0
    return math.atan(1.0 / (1.0 / math.tan(outer) - track / wheelbase))


def steering_table(linkage: Linkage, servo_travel: tuple[float, float], samples: int = 41) -> dict:
    """Wheel angle vs servo angle per side, Ackermann percentage and status."""
    lo, hi = servo_travel
    thetas = sorted(set(np.linspace(lo, hi, samples).round(12).tolist()) | {0.0})
    solutions = {side.joint: solve_side(linkage, side, thetas) for side in linkage.sides}
    rows = []
    for i, theta in enumerate(thetas):
        row: dict = {"servo_rad": theta}
        for side in linkage.sides:
            row[side.joint] = solutions[side.joint].phi[i]
            row[f"{side.joint}_transmission_deg"] = solutions[side.joint].transmission_deg[i]
        angles = [row[s.joint] for s in linkage.sides]
        if len(angles) == 2 and all(math.isfinite(a) for a in angles) and abs(angles[0]) > 1e-4 and abs(angles[1]) > 1e-4:
            inner, outer = max(abs(angles[0]), abs(angles[1])), min(abs(angles[0]), abs(angles[1]))
            ideal = ideal_inner(outer, linkage.track, linkage.wheelbase)
            row["ackermann_pct"] = 100.0 * (inner - outer) / (ideal - outer) if ideal > outer else float("nan")
        rows.append(row)
    return {"thetas": thetas, "rows": rows, "solutions": solutions}
