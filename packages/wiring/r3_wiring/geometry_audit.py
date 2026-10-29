"""Audit of the CAD meshes the wiring stage routes against.

Two representations of every CAD part are checked, because each can be wrong in its own way:

1. **The CAD export** (``build/cad/meshes/*.stl``, Onshape ``tessellatedfaces`` at chord tolerance
   ``c`` and angle tolerance 2 deg). It must be a closed, consistently wound, outward solid whose
   bounds and volume match the Onshape mass properties within the tessellation error. Seams left by
   per-face tessellation are repaired first: coincident vertices are welded, collapsed and duplicate
   triangles removed, and boundary loops that are *seam gaps* (no wider than the chord tolerance) are
   closed. Larger openings are never filled: they are real holes.
2. **The routing mesh** (the export after ``cluster_decimate`` on a ``ROUTING_MESH_CELL`` grid).
   Clearance signs come from its generalized winding number, which is exact only when the mesh is a
   closed oriented 2-cycle (every directed edge cancelled by its reverse). Its bounds may move by at
   most one cluster-cell diagonal. Its volume is not checked: parts thinner than a few cells lose or
   gain most of their volume by design, while the distance error stays bounded by the cell diagonal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: Chord (sag) tolerance of the CAD export, m. Mirrors ``r3_onshape.model.CHORD_TOLERANCE`` (the
#: ``chordTolerance`` sent to Onshape ``/tessellatedfaces``; ``tests/test_wiring_mesh_audit.py`` checks
#: they agree). A part entry in ``cad_model.json`` may override it with ``chord_tolerance``.
CAD_CHORD_TOLERANCE = 5e-5
#: Vertex weld radius, m: 1/50 of the chord tolerance (1 um). The STL stores float32 metres
#: (~3e-8 m resolution at 0.3 m), so copies of one vertex on two face patches lie well inside it,
#: while distinct tessellation vertices are at least ~c apart.
WELD_FRACTION = 0.02
#: Bounding-box agreement for the CAD audit (exported STL vs. mass-property bounds).
BBOX_TOLERANCE = 0.0005
#: Base relative volume agreement (closed mesh vs. CAD mass properties).
VOLUME_TOLERANCE = 0.02
#: k in the chord-error volume envelope k * A * c (see ``volume_limits``).
CHORD_VOLUME_FACTOR = 1.0


@dataclass
class SeamRepair:
    """What welding and seam closing did to a mesh."""

    vertices: np.ndarray
    faces: np.ndarray
    welded_vertices: int = 0
    degenerate_faces: int = 0
    duplicate_faces: int = 0
    seam_loops_filled: int = 0
    open_loops: list[dict[str, float]] = field(default_factory=list)  # loops left open (real holes)

    def summary(self) -> dict[str, Any]:
        return {"welded_vertices": self.welded_vertices, "degenerate_faces": self.degenerate_faces,
                "duplicate_faces": self.duplicate_faces, "seam_loops_filled": self.seam_loops_filled,
                "open_loops": len(self.open_loops),
                "largest_open_loop_perimeter": max((loop["perimeter"] for loop in self.open_loops), default=0.0),
                "largest_open_loop_area": max((loop["area"] for loop in self.open_loops), default=0.0)}


@dataclass
class MeshAudit:
    component: str
    watertight: bool
    winding_consistent: bool
    volume: float
    cad_volume: float | None
    bbox_error: float | None  # max |mesh bound - cad bound|, m
    scale_ratio: float | None  # mesh extent / cad extent (1000 => mm exported as m)
    problems: list[str] = field(default_factory=list)
    surface_area: float = float("nan")  # m^2, welded export
    chord_tolerance: float = CAD_CHORD_TOLERANCE
    volume_error: float | None = None  # (V_mesh - V_cad) / V_cad
    allowed_deficit: float | None = None
    allowed_excess: float | None = None
    repair: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingMeshAudit:
    component: str
    cell: float  # m, clustering cell
    unbalanced_edges: int  # edges whose two directions do not cancel (winding number not exact there)
    bbox_error: float | None  # max |mesh bound - cad bound|, m
    bbox_limit: float
    volume_ratio: float | None  # informational only
    problems: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ topology helpers
def _directed_edges(faces: np.ndarray) -> np.ndarray:
    return np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]).astype(np.int64)


def edge_balance(faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Undirected edges (lo, hi) and their net use count #(lo->hi) - #(hi->lo). A closed oriented
    2-cycle has every net count 0; it is the boundary-free condition for an exact winding number."""
    faces = np.asarray(faces, dtype=np.int64)
    if not len(faces):
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int64)
    edges = _directed_edges(faces)
    sign = np.where(edges[:, 0] < edges[:, 1], 1, -1)
    keys, inverse = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True)
    net = np.zeros(len(keys), dtype=np.int64)
    np.add.at(net, inverse.reshape(-1), sign)
    return keys, net


def unbalanced_edge_count(faces: np.ndarray) -> int:
    return int(np.count_nonzero(edge_balance(faces)[1]))


def weld_vertices(vertices: np.ndarray, faces: np.ndarray, tolerance: float) -> tuple[np.ndarray, np.ndarray, int]:
    """Merge vertices closer than ``tolerance`` (connected components of the KD-tree pair graph),
    placing each merged vertex at its cluster mean. Returns (vertices, faces, number welded away)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    n = len(vertices)
    if n == 0:
        return vertices, faces, 0
    pairs = cKDTree(vertices).query_pairs(tolerance, output_type="ndarray")
    if not len(pairs):
        return vertices, faces, 0
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    count, labels = connected_components(graph, directed=False)
    merged = np.zeros((count, 3))
    np.add.at(merged, labels, vertices)
    merged /= np.bincount(labels, minlength=count)[:, None]
    return merged, labels[faces], n - count


def remove_degenerate_and_duplicate_faces(faces: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Drop triangles with a repeated vertex, then resolve triangles on the same three vertices as a
    2-chain: opposite windings cancel (a doubled internal wall has no volume), and one copy of the net
    winding is kept. Returns (faces, degenerate removed, duplicates removed)."""
    faces = np.asarray(faces, dtype=np.int64)
    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    degenerate = int(np.count_nonzero(~keep))
    faces = faces[keep]
    if not len(faces):
        return faces, degenerate, 0
    keys, first, inverse = np.unique(np.sort(faces, axis=1), axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    if len(keys) == len(faces):
        return faces, degenerate, 0
    # Winding parity relative to the sorted vertex order: an even permutation (cyclic rotation) is +1.
    rank = np.argsort(np.argsort(faces, axis=1), axis=1)
    inversions = ((rank[:, 0] > rank[:, 1]).astype(int) + (rank[:, 0] > rank[:, 2]).astype(int)
                  + (rank[:, 1] > rank[:, 2]).astype(int))
    parity = np.where(inversions % 2 == 0, 1, -1)
    net = np.zeros(len(keys), dtype=np.int64)
    np.add.at(net, inverse, parity)
    kept: list[tuple[int, np.ndarray]] = []
    for group, start in enumerate(first):
        if net[group] == 0:
            continue
        tri = faces[start] if parity[start] == np.sign(net[group]) else faces[start][[0, 2, 1]]
        kept.append((int(start), tri))
    kept.sort(key=lambda item: item[0])  # first-occurrence order, for reproducible output
    result = np.asarray([tri for _, tri in kept], dtype=np.int64).reshape(-1, 3)
    return result, degenerate, len(faces) - len(result)


def boundary_loops(faces: np.ndarray) -> tuple[list[list[int]], int]:
    """Boundary loops of a triangle mesh, ordered so that fan triangles (l0, li, li+1) wind like the
    surrounding faces. Edges used more than once net, or vertices where two loops pinch, make the
    loop ambiguous; those edges are returned as an untraceable count instead."""
    keys, net = edge_balance(faces)
    open_mask = net != 0
    ambiguous = int(np.count_nonzero(np.abs(net) > 1))
    simple = open_mask & (np.abs(net) == 1)
    # Existing faces run lo->hi when net > 0; the hole is traversed the other way.
    lo, hi = keys[simple, 0], keys[simple, 1]
    forward = net[simple] > 0
    start = np.where(forward, hi, lo)
    end = np.where(forward, lo, hi)
    successor: dict[int, int] = {}
    pinched: set[int] = set()
    for a, b in zip(start.tolist(), end.tolist()):
        if a in successor:
            pinched.add(a)
        successor[a] = b
    loops: list[list[int]] = []
    seen: set[int] = set()
    for origin in list(successor):
        if origin in seen:
            continue
        loop, vertex, ok = [], origin, True
        while vertex not in seen:
            seen.add(vertex)
            loop.append(vertex)
            if vertex in pinched or vertex not in successor:
                ok = False
                break
            vertex = successor[vertex]
        if ok and vertex == origin and len(loop) >= 3:
            loops.append(loop)
        else:
            ambiguous += len(loop)
    return loops, ambiguous


def loop_measure(vertices: np.ndarray, loop: list[int]) -> tuple[float, float]:
    """Perimeter and fan-fill area (sum of |triangle| of (l0, li, li+1)) of a boundary loop, m and m^2."""
    p = vertices[loop]
    perimeter = float(np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1).sum())
    area = float(0.5 * np.linalg.norm(np.cross(p[1:-1] - p[0], p[2:] - p[0]), axis=1).sum())
    return perimeter, area


def is_seam_gap(perimeter: float, area: float, chord: float, longest_edge: float) -> bool:
    """A seam gap is a sliver between two tessellations of one CAD edge (duplicate vertices that did
    not weld, or a T-junction where one side has an extra vertex on the other side's edge).

    * Width: its hydraulic width D_h = 4 A / P must not exceed the chord tolerance c. For a sliver
      triangle D_h equals its height, for a parallel slot twice its width (conservative). Closing a gap
      of width <= c moves the surface by no more than the tessellation itself already may.
    * Length: it runs along at most one mesh edge on each side, so P <= 2 (L_max + c), with L_max the
      longest edge of the mesh. A missing triangle or patch fails the width bound; a long crack fails
      the length bound.
    """
    if perimeter <= 0.0:
        return True
    return 4.0 * area / perimeter <= chord and perimeter <= 2.0 * (longest_edge + chord)


def repair_seams(vertices: np.ndarray, faces: np.ndarray, chord: float = CAD_CHORD_TOLERANCE) -> SeamRepair:
    """Weld, drop collapsed/duplicate triangles, and close seam-gap loops (never larger openings)."""
    vertices, faces, welded = weld_vertices(vertices, faces, WELD_FRACTION * chord)
    faces, degenerate, duplicate = remove_degenerate_and_duplicate_faces(faces)
    repair = SeamRepair(vertices, faces, welded, degenerate, duplicate)
    if not len(faces):
        return repair
    loops, ambiguous = boundary_loops(faces)
    if not loops and not ambiguous:
        return repair
    edges = _directed_edges(faces)
    longest = float(np.linalg.norm(vertices[edges[:, 0]] - vertices[edges[:, 1]], axis=1).max())
    fill: list[list[int]] = []
    for loop in loops:
        perimeter, area = loop_measure(vertices, loop)
        if is_seam_gap(perimeter, area, chord, longest):
            fill.extend([loop[0], loop[i], loop[i + 1]] for i in range(1, len(loop) - 1))
            repair.seam_loops_filled += 1
        else:
            repair.open_loops.append({"perimeter": perimeter, "area": area, "vertices": float(len(loop))})
    if ambiguous:
        repair.open_loops.append({"perimeter": float("nan"), "area": float("nan"), "vertices": float(ambiguous)})
    if fill:
        repair.faces = np.concatenate([faces, np.asarray(fill, dtype=np.int64)])
    return repair


# ------------------------------------------------------------------ audits
def volume_limits(area: float, cad_volume: float, chord: float) -> tuple[float, float]:
    """Allowed relative (deficit, excess) of the tessellated volume against the CAD volume.

    Every vertex of a chord-tolerance tessellation lies on the true surface and every facet within c
    of it, so the solid between the two surfaces is thinner than c everywhere and
        |V_mesh - V_cad| <= A c                         (A: surface area)
    A circular arc of sagitta s loses (2/3) s per unit length, so k = 1 is a conservative envelope.
    On convex faces (shafts, outer fillets) facets cut inside the solid, hence the deficit limit
        deficit <= max(VOLUME_TOLERANCE, k A c / V_cad).
    The excess limit stays at VOLUME_TOLERANCE: inscribed facets in small concave bores add far less
    than that, so a larger excess means extra or overlapping material, which is always an error.
    """
    deficit = max(VOLUME_TOLERANCE, CHORD_VOLUME_FACTOR * area * chord / cad_volume) if cad_volume > 0 else VOLUME_TOLERANCE
    return deficit, VOLUME_TOLERANCE


def audit_mesh(component: str, vertices: np.ndarray, faces: np.ndarray, part: dict[str, Any],
               chord: float | None = None) -> MeshAudit:
    """Audit a CAD export (see module docstring, item 1)."""
    import trimesh

    chord = float(chord or part.get("chord_tolerance") or CAD_CHORD_TOLERANCE)
    repair = repair_seams(vertices, faces, chord)
    tm = trimesh.Trimesh(repair.vertices, repair.faces, process=False)
    watertight = bool(len(tm.faces)) and bool(tm.is_watertight) and not repair.open_loops
    winding = bool(tm.is_winding_consistent)
    volume = float(tm.volume) if watertight else float("nan")
    area = float(tm.area)
    cad_volume = float(part["volume"]) if part.get("volume") else None
    bbox_error = scale = volume_error = allowed_deficit = allowed_excess = None
    problems: list[str] = []
    if "bbox_min" in part and "bbox_max" in part and len(tm.faces):
        lo, hi = np.asarray(part["bbox_min"], dtype=float), np.asarray(part["bbox_max"], dtype=float)
        mlo, mhi = tm.bounds
        bbox_error = float(max(np.abs(mlo - lo).max(), np.abs(mhi - hi).max()))
        extent = np.maximum(hi - lo, 1e-9)
        scale = float(np.median((mhi - mlo) / extent))
        if abs(scale - 1.0) > 0.05:
            problems.append(f"mesh is {scale:.3g}x the CAD bounds (units)")
        elif bbox_error > BBOX_TOLERANCE:
            problems.append(f"mesh bounds differ from CAD bounds by {bbox_error * 1e3:.2f} mm (placement)")
    if not watertight:
        holes = [loop for loop in repair.open_loops if math.isfinite(loop["perimeter"])]
        detail = (f"; largest hole {max(h['perimeter'] for h in holes) * 1e3:.2f} mm perimeter, "
                  f"{max(h['area'] for h in holes) * 1e6:.3f} mm2") if holes else ""
        problems.append(f"mesh is not closed after welding seams ({len(repair.open_loops)} open loops{detail}; "
                        "sign of the distance is unreliable)")
    if not winding:
        problems.append("triangle winding is inconsistent")
    if watertight and volume < 0:
        problems.append("triangles are wound inward (negative volume)")
    if watertight and cad_volume and volume > 0:
        allowed_deficit, allowed_excess = volume_limits(area, cad_volume, chord)
        volume_error = (volume - cad_volume) / cad_volume
        if volume_error < -allowed_deficit:
            problems.append(f"mesh volume {volume * 1e6:.4f} cm3 is {-volume_error:.2%} below CAD volume "
                            f"{cad_volume * 1e6:.4f} cm3 (chord-error limit {allowed_deficit:.2%})")
        elif volume_error > allowed_excess:
            problems.append(f"mesh volume {volume * 1e6:.4f} cm3 is {volume_error:.2%} above CAD volume "
                            f"{cad_volume * 1e6:.4f} cm3 (limit {allowed_excess:.2%})")
    return MeshAudit(component, watertight, winding, volume, cad_volume, bbox_error, scale, problems,
                     surface_area=area, chord_tolerance=chord, volume_error=volume_error,
                     allowed_deficit=allowed_deficit, allowed_excess=allowed_excess, repair=repair.summary())


def audit_routing_mesh(component: str, vertices: np.ndarray, faces: np.ndarray, part: dict[str, Any],
                       cell: float, cad_volume_mesh: float | None = None) -> RoutingMeshAudit:
    """Audit the vertex-clustered routing mesh (see module docstring, item 2).

    Clustering moves every vertex to the mean of the vertices in its cell, at most one cell diagonal
    cell * sqrt(3) away, so its bounds may differ from the CAD bounds by cell * sqrt(3) + BBOX_TOLERANCE.
    """
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    unbalanced = unbalanced_edge_count(faces)
    limit = cell * math.sqrt(3.0) + BBOX_TOLERANCE
    bbox_error = volume_ratio = None
    problems: list[str] = []
    if not len(faces):
        problems.append("routing mesh has no triangles (the obstacle disappeared during clustering)")
    if unbalanced:
        problems.append(f"routing mesh ({cell * 1e3:.1f} mm vertex clustering) has {unbalanced} unbalanced edges, "
                        "so its winding-number sign is unreliable near them")
    if "bbox_min" in part and "bbox_max" in part and len(vertices):
        lo, hi = np.asarray(part["bbox_min"], dtype=float), np.asarray(part["bbox_max"], dtype=float)
        bbox_error = float(max(np.abs(vertices.min(axis=0) - lo).max(), np.abs(vertices.max(axis=0) - hi).max()))
        if bbox_error > limit:
            problems.append(f"routing mesh bounds differ from CAD bounds by {bbox_error * 1e3:.2f} mm "
                            f"(clustering bound {limit * 1e3:.2f} mm)")
    reference = cad_volume_mesh or (float(part["volume"]) if part.get("volume") else None)
    if reference and len(faces):
        p = vertices[faces]
        volume = float(np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0)
        volume_ratio = volume / reference
    return RoutingMeshAudit(component, cell, unbalanced, bbox_error, limit, volume_ratio, problems)


__all__ = ["BBOX_TOLERANCE", "CAD_CHORD_TOLERANCE", "CHORD_VOLUME_FACTOR", "MeshAudit", "RoutingMeshAudit",
           "SeamRepair", "VOLUME_TOLERANCE", "WELD_FRACTION", "audit_mesh", "audit_routing_mesh", "boundary_loops",
           "edge_balance", "is_seam_gap", "loop_measure", "remove_degenerate_and_duplicate_faces", "repair_seams",
           "unbalanced_edge_count", "volume_limits", "weld_vertices"]
