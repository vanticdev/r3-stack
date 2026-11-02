"""Voxel search volume and the per-voxel fields the router uses."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .cache import NumericCache, shape_identity
from .obstacles import ObstacleSet

FIELD_CUTOFF = 0.05  # fields are clamped here; no cost term reaches further


@dataclass
class VoxelGrid:
    origin: np.ndarray  # centre of voxel (0, 0, 0)
    shape: tuple[int, int, int]
    voxel: float

    @classmethod
    def around(cls, obstacles: ObstacleSet, voxel: float, margin_xy: float = 0.015,
               top_margin: float = 0.055, *, points: np.ndarray | None = None) -> "VoxelGrid":
        """Include native obstacle extents and every required routing destination.

        A structure's AABB top is not a floor: it may be a hollow enclosure
        with terminals below its walls. Solids remain blocked by their SDFs;
        search bounds never substitute for actual floor or wall geometry.
        """
        bounds = [o.swept.aabb() for o in obstacles.obstacles]
        if not bounds:
            raise ValueError("Routing requires native obstacle geometry")
        lo = np.min([b[0] for b in bounds], axis=0)
        hi = np.max([b[1] for b in bounds], axis=0)
        low = lo - [margin_xy, margin_xy, voxel]
        high = hi + [margin_xy, margin_xy, top_margin]
        if points is not None and len(points):
            points = np.asarray(points, dtype=float).reshape(-1, 3)
            if not np.isfinite(points).all():
                raise ValueError("Routing destinations must be finite")
            padding = max(margin_xy, 2 * voxel)
            low = np.minimum(low, points.min(axis=0) - padding)
            high = np.maximum(high, points.max(axis=0) + padding)
        shape = tuple(int(math.ceil((high[a] - low[a]) / voxel)) + 1 for a in range(3))
        return cls(low, shape, voxel)  # type: ignore[arg-type]

    @property
    def size(self) -> int:
        return self.shape[0] * self.shape[1] * self.shape[2]

    @property
    def strides(self) -> tuple[int, int, int]:
        return self.shape[1] * self.shape[2], self.shape[2], 1

    def centers(self) -> np.ndarray:
        i, j, k = np.meshgrid(*(np.arange(n) for n in self.shape), indexing="ij")
        return self.origin + np.column_stack([i.ravel(), j.ravel(), k.ravel()]) * self.voxel

    def ijk(self, point: np.ndarray) -> tuple[int, int, int]:
        idx = np.round((np.asarray(point) - self.origin) / self.voxel).astype(int)
        if np.any(idx < 1) or np.any(idx > np.asarray(self.shape) - 2):
            raise ValueError("Routing point lies outside the search volume")
        return int(idx[0]), int(idx[1]), int(idx[2])

    def flat(self, point: np.ndarray) -> int:
        i, j, k = self.ijk(point)
        return (i * self.shape[1] + j) * self.shape[2] + k

    def center(self, flat: int) -> np.ndarray:
        i, rest = divmod(flat, self.shape[1] * self.shape[2])
        j, k = divmod(rest, self.shape[2])
        return self.origin + np.array([i, j, k], dtype=float) * self.voxel

    def border(self) -> np.ndarray:
        mask = np.zeros(self.shape, dtype=bool)
        mask[0, :, :] = mask[-1, :, :] = True
        mask[:, 0, :] = mask[:, -1, :] = True
        mask[:, :, 0] = mask[:, :, -1] = True
        return mask.ravel()

    def sample(self, field: np.ndarray, points: np.ndarray) -> np.ndarray:
        """Trilinear interpolation of a flat field at points."""
        f = field.reshape(self.shape)
        rel = (np.atleast_2d(points) - self.origin) / self.voxel
        rel = np.clip(rel, 0, np.asarray(self.shape) - 1.000001)
        i0 = np.floor(rel).astype(int)
        t = rel - i0
        out = np.zeros(len(rel))
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    w = (t[:, 0] if dx else 1 - t[:, 0]) * (t[:, 1] if dy else 1 - t[:, 1]) * (t[:, 2] if dz else 1 - t[:, 2])
                    out += w * f[i0[:, 0] + dx, i0[:, 1] + dy, i0[:, 2] + dz]
        return out


@dataclass
class Fields:
    """Signed distances (m) at voxel centres, clamped at ``FIELD_CUTOFF``."""

    static_clear: np.ndarray  # min(sdf - keepout) over static, non-drillable obstacles
    static_raw: np.ndarray  # min sdf over static, non-drillable obstacles
    moving_keep: np.ndarray  # min(sdf - keepout) over moving swept envelopes
    moving_raw: np.ndarray  # min sdf over moving swept envelopes
    struct_dist: np.ndarray  # min sdf over static structure (tie/surface preference)
    plates: list[tuple[str, int, np.ndarray, float]]  # (component, normal axis, sdf - keepout, thickness)
    plates_raw: list[np.ndarray]  # raw sdf of each plate (same order)
    by_component: dict[str, np.ndarray] = field(default_factory=dict)  # raw swept sdf of ``keep`` components


def compute_fields(grid: VoxelGrid, obstacles: ObstacleSet, keep: set[str] | frozenset[str] = frozenset(),
                   *, cache: NumericCache | None = None) -> Fields:
    """All fields in one pass over the obstacles; the raw (swept) field of every component in
    ``keep`` is kept as well, so per-harness keep-away fields need no second SDF evaluation."""
    points = grid.centers()
    n = len(points)
    static_clear = np.full(n, FIELD_CUTOFF)
    static_raw = np.full(n, FIELD_CUTOFF)
    plates_raw = []
    moving_keep = np.full(n, FIELD_CUTOFF)
    moving_raw = np.full(n, FIELD_CUTOFF)
    struct_dist = np.full(n, FIELD_CUTOFF)
    plates = []
    by_component: dict[str, np.ndarray] = {}
    for obstacle in obstacles.obstacles:
        identity = shape_identity(obstacle.swept) if cache is not None and cache.enabled else None
        key = cache.key('field', identity, grid.origin, grid.shape, grid.voxel, FIELD_CUTOFF) if identity else None
        raw = cache.get('field', key, size=n, dtype=np.float64) if key is not None else None
        if raw is None:
            raw = obstacle.swept.sdf_bounded(points, FIELD_CUTOFF)
            if key is not None:
                cache.put(key, raw)
        if obstacle.component in keep and obstacle.component not in by_component:
            by_component[obstacle.component] = raw
        if obstacle.moving:
            np.minimum(moving_raw, raw, out=moving_raw)
            np.minimum(moving_keep, raw - obstacle.keepout, out=moving_keep)
            continue
        if obstacle.structure:
            np.minimum(struct_dist, raw, out=struct_dist)
        if obstacle.plate_axis is not None:
            lo, hi = obstacle.shape.aabb()
            plates.append((obstacle.component, obstacle.plate_axis, raw - obstacle.keepout,
                           float(hi[obstacle.plate_axis] - lo[obstacle.plate_axis])))
            plates_raw.append(raw)
        else:
            np.minimum(static_clear, raw - obstacle.keepout, out=static_clear)
            np.minimum(static_raw, raw, out=static_raw)
    return Fields(static_clear, static_raw, moving_keep, moving_raw, struct_dist, plates, plates_raw, by_component)
