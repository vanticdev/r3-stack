"""Display paths: conductors of one bundle drawn side by side instead of on one centreline.

``routes.json`` paths of wires that share a bundle core coincide along it (the
router routes the bundle centreline; checks use the bundle radius). For
viewing, every run where conductors coincide is spread into a cross-section:
a flat ribbon for leads that leave one connector together (servo, JST), a
ring otherwise. The offsets are carried along the shared run by parallel
transport. Over each fan-out the offset grows with the conductor's
convergence towards the run, so the drawn wire still ends exactly on its pin
and pins and slots interpolate without crossing.

Coincidence is detected geometrically (point to polyline distance), not by
vertex counts: conductors of one core can carry different fan-out vertex
counts. Passes repeat on the displaced paths, so a Y harness whose branches
share different sub-runs is resolved run by run. Display only: lengths and
checks use ``routes.json``.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

MAX_PASSES = 4
INTERIOR_RAMP = 0.008  # m: ramp length where conductors leave a shared run mid-path
FAN_MAX = 0.04  # m: longest pin-to-run stretch treated as a fan-out (longer: an interior ramp)
SPLICE_REACH = 0.012  # m: around a shared splice point conductors are crimped together
CROSSING_COS = 0.9  # coincident run whose conductors meet at more than ~25 deg: a crossing, not a shared core


def _ring(count: int, od: float) -> np.ndarray:
    if count == 1:
        return np.zeros((1, 2))
    radius = od / (2 * math.sin(math.pi / count))
    angles = np.arange(count) * 2 * math.pi / count
    return radius * np.column_stack([np.cos(angles), np.sin(angles)])


def _flat(diameters: list[float]) -> np.ndarray:
    d = np.asarray(diameters, dtype=float)
    x = np.r_[0.0, np.cumsum((d[:-1] + d[1:]) / 2)]
    return np.column_stack([x - (x[0] + x[-1]) / 2, np.zeros(len(d))])


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


def _project(points: np.ndarray, polyline: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Distance from each point to ``polyline`` and its fractional vertex parameter there."""
    a, b = polyline[:-1], polyline[1:]
    ab = b - a
    denom = np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-18)
    t = np.clip(np.einsum("kij,ij->ki", points[:, None, :] - a[None], ab) / denom, 0.0, 1.0)
    foot = a[None] + t[..., None] * ab[None]
    dist = np.linalg.norm(points[:, None, :] - foot, axis=2)
    seg = np.argmin(dist, axis=1)
    rows = np.arange(len(points))
    return dist[rows, seg], seg + t[rows, seg]


def _frame_at(normals: np.ndarray, binormals: np.ndarray, param: float) -> tuple[np.ndarray, np.ndarray]:
    i = min(int(math.floor(param)), len(normals) - 2)
    f = param - i
    n = (1 - f) * normals[i] + f * normals[i + 1]
    b = (1 - f) * binormals[i] + f * binormals[i + 1]
    return n / max(np.linalg.norm(n), 1e-12), b / max(np.linalg.norm(b), 1e-12)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, stop) index runs where ``mask`` is true."""
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
    return [(int(s), int(e)) for s, e in edges.reshape(-1, 2)]


def _weights(path: np.ndarray, shared: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex offset weight (1 on the shared run, 0 at the pins) and the index of
    the shared vertex whose offset each vertex inherits."""
    n = len(path)
    weight = np.zeros(n)
    source = np.zeros(n, dtype=int)
    runs = _runs(shared)
    if not runs:
        return weight, source
    for start, stop in runs:
        weight[start:stop] = 1.0
        source[start:stop] = np.arange(start, stop)
    arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
    gaps = [(0, runs[0][0], None, runs[0][0])]
    gaps += [(a[1], b[0], a[1] - 1, b[0]) for a, b in zip(runs[:-1], runs[1:])]
    gaps.append((runs[-1][1], n, runs[-1][1] - 1, None))
    for lo, hi, left, right in gaps:
        for k in range(lo, hi):
            best_w, best_src = 0.0, left if left is not None else right
            for boundary, pin in ((left, n - 1), (right, 0)):
                if boundary is None:
                    continue
                other = right if boundary == left else left
                if other is None and abs(arc[pin] - arc[boundary]) <= FAN_MAX:
                    # Fan-out to a pin: weight = convergence from the pin onto the run.
                    span = float(np.linalg.norm(path[pin] - path[boundary]))
                    w = 1.0 - float(np.linalg.norm(path[k] - path[boundary])) / span if span > 1e-12 else 0.0
                else:
                    s = abs(arc[k] - arc[boundary])
                    if other is None:
                        reach = min(INTERIOR_RAMP, abs(arc[pin] - arc[boundary]) / 2)
                    else:
                        reach = min(INTERIOR_RAMP, abs(arc[right] - arc[left]) / 2)
                    w = 1.0 - s / reach if reach > 1e-12 else 0.0
                    w = w * w * (3 - 2 * w) if w > 0 else 0.0
                if w > best_w:
                    best_w, best_src = w, boundary
            weight[k] = max(0.0, min(1.0, best_w))
            source[k] = best_src
    weight[0] = weight[-1] = 0.0
    return weight, source


def _separate(paths: dict[str, np.ndarray], wires: dict[str, Any], diameters: dict[str, float], *,
              layout: str = "auto") -> bool:
    """One pass: spread every group of conductors that still coincide. Returns True if anything moved."""
    by_harness: dict[str, list[str]] = {}
    for wire_id, path in paths.items():
        if len(path) >= 2:
            by_harness.setdefault(wires[wire_id]["harness"], []).append(wire_id)
    moved = False
    for members_all in by_harness.values():
        if len(members_all) < 2:
            continue
        # shared[a][b]: vertices of a within tolerance of b's polyline (b's parameter there).
        near: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
        for a, b in itertools.permutations(members_all, 2):
            tol = 0.3 * min(diameters.get(a, 1e-3), diameters.get(b, 1e-3))
            dist, param = _project(paths[a], paths[b])
            mask = dist < tol
            mask[0] = mask[-1] = False  # pins are never part of a shared run
            ta = np.gradient(paths[a], axis=0)
            tb = np.gradient(paths[b], axis=0)
            ta /= np.maximum(np.linalg.norm(ta, axis=1, keepdims=True), 1e-12)
            tb /= np.maximum(np.linalg.norm(tb, axis=1, keepdims=True), 1e-12)
            tb_at = tb[np.clip(np.rint(param).astype(int), 0, len(tb) - 1)]
            for start, stop in _runs(mask):
                if stop - start < 2 or np.mean(np.abs(np.einsum("ij,ij->i", ta[start:stop], tb_at[start:stop]))) < CROSSING_COS:
                    mask[start:stop] = False  # conductors crossing, not running together
            near[(a, b)] = (mask, param)
        # Connected groups of conductors that coincide somewhere.
        parent = {m: m for m in members_all}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for (a, b), (mask, _) in near.items():
            if mask.sum() >= 2:
                parent[find(a)] = find(b)
        groups: dict[str, list[str]] = {}
        for m in members_all:
            groups.setdefault(find(m), []).append(m)
        for members in groups.values():
            if len(members) < 2:
                continue
            # Reference: the conductor coinciding with the others over most vertices.
            def coverage(m: str) -> int:
                return int(sum(near[(m, o)][0].sum() for o in members if o != m))
            ref = max(members, key=coverage)
            ref_path = paths[ref]
            on_ref = {ref: np.ones(len(ref_path), dtype=bool)}
            params = {ref: np.arange(len(ref_path), dtype=float)}
            on_ref_any = np.zeros(len(ref_path), dtype=bool)
            for m in members:
                if m == ref:
                    continue
                # Shared wherever m runs with any member; frames come from the reference.
                mask = np.zeros(len(paths[m]), dtype=bool)
                for o in members:
                    if o != m:
                        mask |= near[(m, o)][0]
                on_ref[m], params[m] = mask, near[(m, ref)][1]
                on_ref_any |= near[(ref, m)][0]
            group = [m for m in members if m == ref or on_ref[m].sum() >= 2]
            if len(group) < 2:
                continue
            on_ref[ref] = on_ref_any
            # Start-side pin of each conductor relative to the reference direction.
            start_pin, end_pin, first_param, last_param = {}, {}, {}, {}
            for m in group:
                p = params[m][on_ref[m]]
                forward = len(p) < 2 or p[-1] >= p[0]
                start_pin[m], end_pin[m] = (paths[m][0], paths[m][-1]) if forward else (paths[m][-1], paths[m][0])
                first_param[m] = float(p.min()) if len(p) else 0.0
                last_param[m] = float(p.max()) if len(p) else 0.0
            pins = np.array([start_pin[m] for m in group])
            spread = pins - pins.mean(axis=0)
            if np.linalg.norm(spread) > 1e-9:
                normal = np.linalg.svd(spread, full_matrices=False)[2][0]
            else:
                normal = np.array([1.0, 0.0, 0.0])
            normals, binormals = _transport(ref_path, normal)
            n0, b0 = _frame_at(normals, binormals, min(first_param.values()))
            local = np.column_stack([spread @ n0, spread @ b0])
            connectors = {(wires[m].get("ends") or [None])[0] and (wires[m]["ends"][0] or {}).get("connector") for m in group}
            ribbon = layout == "flat" or (len(group) <= 6 and len(connectors) == 1 and None not in connectors)
            if ribbon:
                order = sorted(range(len(group)), key=lambda i: local[i, 0])
                slots_sorted = _flat([diameters.get(group[i], 1e-3) for i in order])
                slots = np.zeros((len(group), 2))
                for rank, i in enumerate(order):
                    slots[i] = slots_sorted[rank]
            else:
                ring = _ring(len(group), max(diameters.get(m, 1e-3) for m in group))
                best, best_cost = tuple(range(len(group))), math.inf
                candidates = itertools.permutations(range(len(group))) if len(group) <= 6 else [best]
                for perm in candidates:
                    cost = float(sum(np.linalg.norm(local[i] - ring[perm[i]]) for i in range(len(group))))
                    if cost < best_cost:
                        best, best_cost = perm, cost
                slots = np.array([ring[best[i]] for i in range(len(group))])
            # Like a real lead, the cross-section twists along the run so the slots also
            # meet the far connector's pins in order (no crossing in the far fan-in).
            p_start, p_end = min(first_param.values()), max(last_param.values())
            ends = np.array([end_pin[m] for m in group])
            n1, b1 = _frame_at(normals, binormals, p_end)
            far = ends - ends.mean(axis=0)
            far_local = np.column_stack([far @ n1, far @ b1])
            twist = math.atan2(float(np.sum(slots[:, 0] * far_local[:, 1] - slots[:, 1] * far_local[:, 0])),
                               float(np.sum(slots[:, 0] * far_local[:, 0] + slots[:, 1] * far_local[:, 1])))

            def mismatch(angle: float) -> float:
                c, s_ = math.cos(angle), math.sin(angle)
                rotated = np.column_stack([c * slots[:, 0] - s_ * slots[:, 1], s_ * slots[:, 0] + c * slots[:, 1]])
                return float(np.linalg.norm(rotated - far_local, axis=1).sum())

            # A lead whose pin order changes between its ends (a TX/RX crossover) is not a
            # pure twist: keep the twist only where it actually matches the far pins better.
            if mismatch(twist) >= mismatch(0.0):
                twist = 0.0
            arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(ref_path, axis=0), axis=1))]

            def arc_at(param: float) -> float:
                i = min(int(math.floor(param)), len(arc) - 2)
                return float(arc[i] + (param - i) * (arc[i + 1] - arc[i]))

            a0, a1 = arc_at(p_start), arc_at(p_end)

            def frame(param: float) -> tuple[np.ndarray, np.ndarray]:
                n, b = _frame_at(normals, binormals, param)
                phi = twist * min(1.0, max(0.0, (arc_at(param) - a0) / (a1 - a0))) if a1 - a0 > 1e-9 else 0.0
                c, s_ = math.cos(phi), math.sin(phi)
                return c * n + s_ * b, -s_ * n + c * b
            for index, m in enumerate(group):
                if np.linalg.norm(slots[index]) < 1e-12:
                    continue
                path = paths[m]
                weight, source = _weights(path, on_ref[m])
                if not weight.any():
                    continue
                offsets = np.zeros_like(path)
                for k in np.flatnonzero(weight > 0):
                    n, b = frame(float(params[m][source[k]]))
                    offsets[k] = weight[k] * (slots[index][0] * n + slots[index][1] * b)
                paths[m] = path + offsets
                moved = True
    return moved


def display_paths(routes: dict[str, Any], diameters: dict[str, float], *, layout: str = "auto") -> dict[str, np.ndarray]:
    """Per-wire display polylines in metres; ``flat`` makes touching, side-by-side leads.

    These are inspection paths, not a replacement for physical routing checks.
    """
    if layout not in ("auto", "flat"):
        raise ValueError("Unknown conductor display layout")
    wires = routes.get("wires", {})
    paths = {wire_id: np.asarray(wire["path"], dtype=float) for wire_id, wire in wires.items() if wire.get("path")}
    for _ in range(MAX_PASSES):
        if not _separate(paths, wires, diameters, layout=layout):
            break
    return paths


def overlaps(paths: dict[str, np.ndarray], wires: dict[str, Any], diameters: dict[str, float],
             tolerance: float = 5e-5) -> list[dict[str, Any]]:
    """Conductor pairs of one harness whose display centrelines merge along the core:
    closer than their radii allow outside the fan-outs. Fan-outs are excluded
    because conductors legitimately converge there (pin pitch below the bundle
    pitch, crossovers such as TX/RX, shared splice terminals)."""
    out = []
    ids = [w for w in paths if len(paths[w]) >= 3]
    for a, b in itertools.combinations(ids, 2):
        if wires[a]["harness"] != wires[b]["harness"]:
            continue
        head, tail = (wires[a].get("fanout") or [0, 0])[:2]
        core = paths[a][max(1, head): len(paths[a]) - max(1, tail)]
        if len(core) == 0:
            continue
        need = (diameters.get(a, 0.0) + diameters.get(b, 0.0)) / 2
        dist, _ = _project(core, paths[b])
        close = dist < need - tolerance
        # Conductors joined at a splice share its end point and are crimped together near it.
        for end_a in (paths[a][0], paths[a][-1]):
            if min(np.linalg.norm(end_a - paths[b][0]), np.linalg.norm(end_a - paths[b][-1])) < 1e-6:
                close &= np.linalg.norm(core - end_a, axis=1) > SPLICE_REACH
        if close.any():
            out.append({"a": a, "b": b, "min_distance": float(dist.min()), "required": need,
                        "fraction": float(close.mean())})
    return out


__all__ = ["display_paths", "overlaps"]
