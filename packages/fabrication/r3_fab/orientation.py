"""Print orientation: candidate rotations, overhang/support area, bed contact, height.

A face needs support when it faces down more steeply than ``max_overhang_deg``
from vertical: with outward unit normal n in the print frame (z up) the surface
makes the angle theta from vertical with sin(theta) = -n_z, so support is needed
when -n_z > sin(max_overhang) (45 deg: the common FDM rule, Hubs "How to design
parts for FDM 3D printing"; Prusa KB gives 45-60 deg). Faces lying on the bed
(z = 0, facing down) need none.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import trimesh

from .geometry import euler_deg_from_rotation, rotation_from_euler_deg

BED_TOLERANCE_MM = 0.05


@dataclass
class Orientation:
    rotation: np.ndarray  # component frame -> print frame (3x3)
    label: str
    support_area_mm2: float = 0.0
    max_overhang_deg: float = 0.0  # steepest supported overhang (from vertical)
    bed_contact_mm2: float = 0.0
    height_mm: float = 0.0
    footprint_mm: list[float] = field(default_factory=list)  # oriented bbox x, y
    build_direction: list[float] = field(default_factory=list)  # print +z in the component frame
    load_across_layers: bool | None = None
    score: float = 0.0

    @property
    def euler_deg(self) -> list[float]:
        return euler_deg_from_rotation(self.rotation)

    def to_json(self) -> dict[str, Any]:
        return {"label": self.label, "rotation_deg": self.euler_deg, "support_area_mm2": round(self.support_area_mm2, 3),
                "max_overhang_deg": round(self.max_overhang_deg, 2), "bed_contact_mm2": round(self.bed_contact_mm2, 3),
                "height_mm": round(self.height_mm, 3), "footprint_mm": [round(v, 3) for v in self.footprint_mm],
                "build_direction": [round(v, 6) for v in self.build_direction], "load_across_layers": self.load_across_layers,
                "score": round(self.score, 4)}


def axis_candidates() -> list[tuple[str, np.ndarray]]:
    """The six rotations that put each component axis (+-x, +-y, +-z) down on the bed."""
    out = []
    for label, angles in (("-z down", [0, 0, 0]), ("+z down", [180, 0, 0]), ("-y down", [90, 0, 0]),
                          ("+y down", [-90, 0, 0]), ("-x down", [0, -90, 0]), ("+x down", [0, 90, 0])):
        out.append((label, rotation_from_euler_deg(angles)))
    return out


def stable_candidates(mesh: trimesh.Trimesh, count: int = 6) -> list[tuple[str, np.ndarray]]:
    """Resting poses on the convex hull with the highest static probability (trimesh.poses)."""
    try:
        transforms, probabilities = mesh.compute_stable_poses(n_samples=1, threshold=0.01)
    except Exception:  # noqa: BLE001 - degenerate hulls: fall back to the axis-aligned candidates
        return []
    return [(f"stable pose {i + 1} (p={p:.2f})", t[:3, :3]) for i, (t, p) in enumerate(zip(transforms[:count], probabilities[:count]))]


def oriented(mesh: trimesh.Trimesh, rotation: np.ndarray) -> trimesh.Trimesh:
    """Mesh rotated into the print frame and dropped onto z = 0."""
    transform = np.eye(4)
    transform[:3, :3] = rotation
    out = mesh.copy()
    out.apply_transform(transform)
    out.apply_translation([0.0, 0.0, -out.bounds[0, 2]])
    return out


def evaluate(mesh: trimesh.Trimesh, rotation: np.ndarray, label: str, max_overhang_deg: float) -> Orientation:
    part = oriented(mesh, rotation)
    normals = part.face_normals
    areas = part.area_faces
    z_low = part.triangles[:, :, 2].max(axis=1)
    down = -normals[:, 2]
    on_bed = (down > math.cos(math.radians(1.0))) & (z_low < BED_TOLERANCE_MM)
    needs_support = (down > math.sin(math.radians(max_overhang_deg))) & ~on_bed
    steepest = float(np.degrees(np.arcsin(np.clip(down[needs_support], -1, 1))).max()) if needs_support.any() else 0.0
    extents = part.extents
    return Orientation(rotation=rotation, label=label, support_area_mm2=float(areas[needs_support].sum()), max_overhang_deg=steepest,
                       bed_contact_mm2=float(areas[on_bed].sum()), height_mm=float(extents[2]),
                       footprint_mm=[float(extents[0]), float(extents[1])], build_direction=(rotation.T @ [0.0, 0.0, 1.0]).tolist())


def _dedupe(candidates: list[tuple[str, np.ndarray]]) -> list[tuple[str, np.ndarray]]:
    """Drop rotations that put the same component direction up (rotations about z do not change printability)."""
    out: list[tuple[str, np.ndarray]] = []
    for label, rotation in candidates:
        up = rotation.T @ [0.0, 0.0, 1.0]
        if all(float(up @ (r.T @ [0.0, 0.0, 1.0])) < 1.0 - 1e-6 for _, r in out):
            out.append((label, rotation))
    return out


def score(o: Orientation, surface_area_mm2: float, max_dim_mm: float, strength_penalty: float = 0.0) -> float:
    """Lower is better: support area (fraction of the surface), height (fraction of the largest
    dimension), minus bed contact (adhesion and first-layer stability), plus a strength penalty
    when the main load runs across layers (r3_fab.strength). Weights are R3 heuristics."""
    support = 100.0 * o.support_area_mm2 / max(surface_area_mm2, 1e-9)
    height = 10.0 * o.height_mm / max(max_dim_mm, 1e-9)
    contact = 20.0 * o.bed_contact_mm2 / max(surface_area_mm2, 1e-9)
    return support + height - contact + strength_penalty


def choose(mesh: trimesh.Trimesh, max_overhang_deg: float, override: list[float] | None = None,
           stress_direction: np.ndarray | None = None, strength_ok: Any = None) -> tuple[Orientation, list[Orientation]]:
    """Best orientation and all evaluated candidates. ``override`` ([rx, ry, rz] deg) wins.
    ``stress_direction`` (component frame) is the critical principal-stress direction;
    ``strength_ok(build_direction) -> bool`` says whether the part keeps its required
    safety factor with that build direction (a failing direction gets +100)."""
    candidates = _dedupe(axis_candidates() + stable_candidates(mesh))
    surface, max_dim = float(mesh.area), float(np.max(mesh.extents))
    evaluated = []
    for label, rotation in candidates:
        o = evaluate(mesh, rotation, label, max_overhang_deg)
        penalty = 0.0
        if stress_direction is not None:
            alignment = abs(float(np.dot(stress_direction, o.build_direction)))
            o.load_across_layers = alignment >= math.cos(math.radians(45.0))
            if strength_ok is not None and not strength_ok(np.asarray(o.build_direction)):
                penalty = 100.0
            elif o.load_across_layers:
                penalty = 5.0 * alignment  # prefer in-layer load paths when printability ties
        o.score = score(o, surface, max_dim, penalty)
        evaluated.append(o)
    evaluated.sort(key=lambda o: o.score)
    if override is not None:
        chosen = evaluate(mesh, rotation_from_euler_deg(override), "override", max_overhang_deg)
        if stress_direction is not None:
            chosen.load_across_layers = abs(float(np.dot(stress_direction, chosen.build_direction))) >= math.cos(math.radians(45.0))
        chosen.score = score(chosen, surface, max_dim)
        return chosen, evaluated
    return evaluated[0], evaluated


def fit_on_bed(part: trimesh.Trimesh, volume_mm: list[float], steps: int = 36) -> tuple[bool, float, list[float]]:
    """Whether the oriented part fits the build volume, trying rotations about z (every 5 deg).
    Returns (fits, best z rotation in deg, oriented extents at that rotation)."""
    hull = part.convex_hull.vertices if len(part.vertices) > 8 else part.vertices
    best: tuple[float, float, list[float]] | None = None
    for k in range(steps):
        angle = math.radians(k * 180.0 / steps)
        c, s = math.cos(angle), math.sin(angle)
        xy = hull[:, :2] @ np.array([[c, s], [-s, c]])
        extents = [float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1])), float(np.ptp(hull[:, 2]))]
        excess = max(extents[0] - volume_mm[0], extents[1] - volume_mm[1])  # over the bed in the worse axis
        if best is None or excess < best[0]:
            best = (excess, math.degrees(angle), extents)
    assert best is not None
    fits = best[0] <= 0.0 and best[2][2] <= volume_mm[2]
    return fits, best[1], best[2]
