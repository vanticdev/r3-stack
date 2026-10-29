"""Cross-section of a power pour, measured on KiCad's own filled copper (read back by pcbnew).

A pour that carries current between terminals (pads or board points) is only as good as its
narrowest cross-section along the current path. The filled polygons are rasterized
(``resolution`` mm grid) together with the terminal pads; the Euclidean distance transform gives,
at every copper pixel, the distance to the nearest copper edge (half the local width). The
bottleneck is the *widest path* between the terminal groups: the largest ``r`` for which the
pixels with distance >= ``r`` still connect every group, found by bisection over the distance
values. A path may start within ``r`` of a terminal (a channel of width 2 r touching the pad: the
current enters from the pad, not past the copper edge behind it). ``width = 2 r`` is then rated with
the IPC-2152 fit like a track.
Pure measurement: R3 does not change the copper.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
from matplotlib.path import Path as MplPath
from scipy import ndimage

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1 (mm)
Ring = Sequence[Point]
POINT_TERMINAL_RADIUS = 0.3  # mm, a free-point terminal (track end landing in the pour)


def _raster(polygons: Iterable[Sequence[Ring]], xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    gx, gy = np.meshgrid(xs, ys)
    points = np.column_stack([gx.ravel(), gy.ravel()])
    mask = np.zeros(len(points), dtype=bool)
    for rings in polygons:
        if not rings or len(rings[0]) < 3:
            continue
        inside = MplPath(np.asarray(rings[0])).contains_points(points)
        for hole in rings[1:]:
            if len(hole) >= 3:
                inside &= ~MplPath(np.asarray(hole)).contains_points(points)
        mask |= inside
    return mask.reshape(gy.shape)


def _terminal_mask(terminal: Box | Point, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    gx, gy = np.meshgrid(xs, ys)
    if len(terminal) == 4:
        x0, y0, x1, y1 = terminal  # type: ignore[misc]
        return (gx >= min(x0, x1)) & (gx <= max(x0, x1)) & (gy >= min(y0, y1)) & (gy <= max(y0, y1))
    x, y = terminal  # type: ignore[misc]
    return np.hypot(gx - x, gy - y) <= POINT_TERMINAL_RADIUS


def bottleneck_width(polygons: Sequence[Sequence[Ring]], groups: Sequence[Sequence[Box | Point]],
                     resolution: float = 0.02) -> float:
    """Narrowest cross-section (mm) the current must pass between the terminal ``groups`` through the
    copper ``polygons`` (each: outline ring then hole rings). 0 when the groups are not connected."""
    if len(groups) < 2:
        raise ValueError("a pour check needs at least two terminal groups")
    coords = [p for rings in polygons for ring in rings for p in ring]
    for group in groups:
        for t in group:
            coords += [(t[0], t[1]), (t[2], t[3])] if len(t) == 4 else [(t[0] - 0.5, t[1] - 0.5), (t[0] + 0.5, t[1] + 0.5)]
    if not coords:
        return 0.0
    arr = np.asarray(coords)
    margin = 4 * resolution
    xs = np.arange(arr[:, 0].min() - margin, arr[:, 0].max() + margin, resolution)
    ys = np.arange(arr[:, 1].min() - margin, arr[:, 1].max() + margin, resolution)
    copper = _raster(polygons, xs, ys)
    group_masks = []
    for group in groups:
        m = np.zeros_like(copper)
        for t in group:
            m |= _terminal_mask(t, xs, ys)
        group_masks.append(m)
        copper |= m  # terminal pads are copper of the same net
    # distance from a pixel centre to the nearest non-copper pixel centre, ~ the distance to the edge + res/2
    edt = ndimage.distance_transform_edt(copper) * resolution - 0.5 * resolution
    terminals = np.logical_or.reduce(group_masks)
    to_terminal = [ndimage.distance_transform_edt(~m) * resolution for m in group_masks]

    def connected(r: float) -> bool:
        reach = [d <= r + resolution for d in to_terminal]
        labels, _ = ndimage.label((edt >= r) | terminals, structure=np.ones((3, 3)))
        common = None
        for m, near in zip(group_masks, reach):
            ids = set(np.unique(labels[m | (near & (edt >= r))])) - {0}
            common = ids if common is None else common & ids
            if not common:
                return False
        return bool(common)

    if not connected(0.0):
        return 0.0
    values = np.unique(edt[copper & ~terminals])
    lo, hi = 0, len(values) - 1
    if hi < 0 or connected(float(values[hi])):
        return 2.0 * float(values[hi]) if hi >= 0 else 0.0
    while lo < hi:  # largest index with connected(values[index])
        mid = (lo + hi + 1) // 2
        if connected(float(values[mid])):
            lo = mid
        else:
            hi = mid - 1
    return 2.0 * float(values[lo])
