"""Display paths: conductors of one bundle drawn side by side instead of on one centreline.

``routes.json`` paths of wires that share a bundle core coincide along it (the
router routes the bundle centreline). For viewing, each conductor of such a
group is offset to its slot in the bundle cross-section (a ring of wire
diameters) carried along the core by parallel transport; over the fan-out the
offset ramps from zero at the pin, so the drawn wire still ends exactly on its
pin. Slots are assigned to match the pins' order at the first end (no crossing
out of the connector). Display only: lengths and checks use ``routes.json``.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np


def _ring(count: int, od: float) -> np.ndarray:
    if count == 1:
        return np.zeros((1, 2))
    radius = od / (2 * math.sin(math.pi / count))
    angles = np.arange(count) * 2 * math.pi / count
    return radius * np.column_stack([np.cos(angles), np.sin(angles)])


def _transport(core: np.ndarray, normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tangents = np.gradient(core, axis=0)
    tangents /= np.maximum(np.linalg.norm(tangents, axis=1, keepdims=True), 1e-12)
    normals = np.zeros_like(core)
    n = normal - (normal @ tangents[0]) * tangents[0]
    if np.linalg.norm(n) < 1e-9:
        n = np.cross(tangents[0], [0.0, 0.0, 1.0] if abs(tangents[0][2]) < 0.9 else [1.0, 0.0, 0.0])
    n /= np.linalg.norm(n)
    for i, t in enumerate(tangents):
        n = n - (n @ t) * t
        n /= max(np.linalg.norm(n), 1e-12)
        normals[i] = n
    return normals, np.cross(tangents, normals)


def _split(wire: dict[str, Any]) -> tuple[np.ndarray, int, int]:
    path = np.asarray(wire["path"], dtype=float)
    head, tail = (wire.get("fanout") or [0, 0])[:2]
    return path, int(head), int(tail)


def display_paths(routes: dict[str, Any], diameters: dict[str, float]) -> dict[str, np.ndarray]:
    """Per-wire display polylines (robot frame, m). ``diameters``: wire id -> outer diameter."""
    groups: dict[tuple, list[str]] = {}
    for wire_id, wire in routes.get("wires", {}).items():
        path, head, tail = _split(wire)
        core = path[head: len(path) - tail]
        if len(core) < 2:
            continue
        key = (wire["harness"], len(core), tuple(np.round(core[[0, -1]].ravel(), 6)), round(float(core[len(core) // 2][0]), 6))
        groups.setdefault(key, []).append(wire_id)
    out: dict[str, np.ndarray] = {}
    for members in groups.values():
        if len(members) == 1:
            out[members[0]] = np.asarray(routes["wires"][members[0]]["path"], dtype=float)
            continue
        first, head0, tail0 = _split(routes["wires"][members[0]])
        core = first[head0: len(first) - tail0]
        pins = np.array([np.asarray(routes["wires"][m]["path"][0], dtype=float) for m in members])
        spread = pins - pins.mean(axis=0)
        normal = spread[int(np.argmax(np.linalg.norm(spread, axis=1)))] if len(spread) else np.array([1.0, 0.0, 0.0])
        normals, binormals = _transport(core, normal)
        od = max(diameters.get(m, 0.0) for m in members)
        slots = _ring(len(members), od)
        # Pin offsets at the core start, in the (normal, binormal) plane.
        local = np.column_stack([spread @ normals[0], spread @ binormals[0]])
        best, best_cost = None, math.inf
        for order in itertools.permutations(range(len(members))) if len(members) <= 6 else [tuple(range(len(members)))]:
            cost = float(sum(np.linalg.norm(local[i] - slots[order[i]] * 2.0) for i in range(len(members))))
            if cost < best_cost:
                best, best_cost = order, cost
        for index, wire_id in enumerate(members):
            path, head, tail = _split(routes["wires"][wire_id])
            slot = slots[best[index]]
            offsets = slot[0] * normals + slot[1] * binormals
            display = path.copy()
            display[head: len(path) - tail] += offsets
            for start, stop, at in ((0, head, 0), (len(path) - tail, len(path), len(core) - 1)):
                count = stop - start
                if count <= 0:
                    continue
                ramp = np.linspace(0.0, 1.0, count + 1)[:-1] if at == 0 else np.linspace(1.0, 0.0, count + 1)[1:]
                ramp = ramp * ramp * (3 - 2 * ramp)  # smoothstep: tangent continuity at both ends
                display[start:stop] += ramp[:, None] * offsets[at]
            out[wire_id] = display
    return out


__all__ = ["display_paths"]
