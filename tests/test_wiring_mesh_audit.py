"""CAD mesh audit (r3_wiring.geometry_audit) on synthetic meshes: seams and chord error pass,
real holes, missing material, excess material and inverted meshes fail."""

from __future__ import annotations

import math

import numpy as np
import pytest
import trimesh

from r3_wiring.geometry_audit import (CAD_CHORD_TOLERANCE, VOLUME_TOLERANCE, audit_mesh, audit_routing_mesh,
                                      remove_degenerate_and_duplicate_faces, repair_seams, unbalanced_edge_count,
                                      volume_limits)

C = CAD_CHORD_TOLERANCE


def _part(volume: float, mesh: trimesh.Trimesh | None = None, bounds: np.ndarray | None = None) -> dict:
    lo, hi = bounds if bounds is not None else mesh.bounds
    return {"volume": volume, "bbox_min": list(map(float, lo)), "bbox_max": list(map(float, hi))}


def _pin_patches(radius: float, height: float, segments: int, jitter: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Cylinder tessellated per CAD face, like Onshape tessellatedfaces: the side wall and both caps
    each carry their own copy of the rim vertices (seams), jittered like float32 round-off."""
    rng = np.random.default_rng(seed)
    angle = 2 * np.pi * np.arange(segments) / segments
    ring = np.stack([radius * np.cos(angle), radius * np.sin(angle), np.zeros(segments)], axis=1)
    top = ring + [0.0, 0.0, height]
    vertices: list[np.ndarray] = []
    faces: list[list[int]] = []

    def add(points: np.ndarray) -> int:
        base = sum(len(v) for v in vertices)
        vertices.append(points + rng.uniform(-jitter, jitter, points.shape))
        return base

    s = add(np.concatenate([ring, top]))  # side wall patch
    for i in range(segments):
        j = (i + 1) % segments
        faces += [[s + i, s + j, s + segments + j], [s + i, s + segments + j, s + segments + i]]
    b = add(np.concatenate([[[0.0, 0.0, 0.0]], ring]))  # bottom cap patch (outward -z)
    faces += [[b, b + 1 + (i + 1) % segments, b + 1 + i] for i in range(segments)]
    t = add(np.concatenate([[[0.0, 0.0, height]], top]))  # top cap patch (outward +z)
    faces += [[t, t + 1 + i, t + 1 + (i + 1) % segments] for i in range(segments)]
    return np.concatenate(vertices), np.asarray(faces)


def _box(extents: tuple[float, float, float]) -> trimesh.Trimesh:
    return trimesh.creation.box(extents=extents)


def _sliver_box(length: float, height: float, width: float) -> tuple[np.ndarray, np.ndarray]:
    """Closed box whose top face has a T-junction sliver gap of the given width along its diagonal:
    one triangle keeps the diagonal a-c, the other side is split at m, offset ``width`` from it."""
    L, h = length, height
    v = np.array([[0, 0, 0], [L, 0, 0], [L, L, 0], [0, L, 0], [0, 0, h], [L, 0, h], [L, L, h], [0, L, h]], float)
    a, c = v[4], v[6]
    m = (a + c) / 2 + width * np.array([-1.0, 1.0, 0.0]) / math.sqrt(2.0)
    v = np.vstack([v, m])
    faces = [[0, 2, 1], [0, 3, 2],  # bottom
             [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],  # sides
             [4, 5, 6],  # top, keeps edge a-c
             [4, 8, 7], [8, 6, 7]]  # top, split at m
    return v, np.asarray(faces)


def test_chord_tolerance_matches_the_onshape_export() -> None:
    from r3_onshape.model import CHORD_TOLERANCE

    assert CAD_CHORD_TOLERANCE == CHORD_TOLERANCE


# (a) chord error + duplicated seam vertices pass ------------------------------------------------
def test_coarse_pin_with_seams_and_chord_deficit_passes() -> None:
    r, h, n = 0.002, 0.010, 16
    vertices, faces = _pin_patches(r, h, n, jitter=2e-7)
    assert not trimesh.Trimesh(vertices, faces, process=False).is_watertight  # seams are open before welding
    sagitta = r * (1 - math.cos(math.pi / n))
    assert sagitta <= C  # the tessellation honours the chord tolerance
    cad_volume = math.pi * r * r * h
    audit = audit_mesh("pin", vertices, faces, _part(cad_volume, bounds=np.array([[-r, -r, 0], [r, r, h]])))
    assert audit.problems == []
    assert audit.watertight and audit.winding_consistent
    assert audit.repair["welded_vertices"] == 2 * n  # each of the 2n rim vertices had one extra copy
    assert audit.repair["seam_loops_filled"] == 0
    # The inscribed 16-gon is 2.5 % low: above the base tolerance, inside the A c / V envelope.
    assert -0.03 < audit.volume_error < -VOLUME_TOLERANCE
    assert audit.allowed_deficit == pytest.approx(audit.surface_area * C / cad_volume)


def test_volume_limits_equation() -> None:
    deficit, excess = volume_limits(area=1.5e-4, cad_volume=1.25e-7, chord=5e-5)
    assert deficit == pytest.approx(1.5e-4 * 5e-5 / 1.25e-7)
    assert excess == VOLUME_TOLERANCE
    assert volume_limits(area=1e-3, cad_volume=1e-3, chord=5e-5)[0] == VOLUME_TOLERANCE  # bulky part: base


# (b) a real hole fails --------------------------------------------------------------------------
@pytest.mark.parametrize("drop", [[0, 1, 2, 3], [2 * 16 + 5]], ids=["side-patch", "one-cap-triangle"])
def test_missing_face_patch_fails(drop: list[int]) -> None:
    r, h, n = 0.002, 0.010, 16
    vertices, faces = _pin_patches(r, h, n, jitter=2e-7)
    faces = np.delete(faces, drop, axis=0)
    audit = audit_mesh("pin", vertices, faces, _part(math.pi * r * r * h, bounds=np.array([[-r, -r, 0], [r, r, h]])))
    assert not audit.watertight
    assert audit.repair["seam_loops_filled"] == 0 and audit.repair["open_loops"] == 1
    assert any("not closed" in p for p in audit.problems)


# (c) a large deficit fails ----------------------------------------------------------------------
def test_missing_chunk_fails() -> None:
    full = _box((0.02, 0.02, 0.01))
    chunk = trimesh.creation.box(extents=(0.01, 0.004, 0.02), transform=trimesh.transformations.translation_matrix(
        [0.005, 0.008, 0.0]))  # removes a 10 x 4 x 10 mm corner block: 10 % of the part
    notched = full.difference(chunk, engine="manifold")
    assert notched.is_watertight
    assert notched.volume == pytest.approx(0.9 * full.volume, rel=1e-6)
    audit = audit_mesh("bracket", notched.vertices, notched.faces, _part(float(full.volume), full))
    assert audit.watertight
    assert any("below CAD volume" in p for p in audit.problems)


def test_pin_deficit_beyond_the_chord_envelope_fails() -> None:
    r, h, n = 0.002, 0.010, 16
    vertices, faces = _pin_patches(r, h, n, jitter=2e-7)
    cad_volume = math.pi * r * r * h / 0.9  # mesh is ~12 % low; the envelope allows ~6 %
    audit = audit_mesh("pin", vertices, faces, _part(cad_volume, bounds=np.array([[-r, -r, 0], [r, r, h]])))
    assert audit.volume_error < -audit.allowed_deficit
    assert any("below CAD volume" in p for p in audit.problems)


# (d) inverted, overlapping or oversized meshes fail -----------------------------------------------
def test_inverted_mesh_fails() -> None:
    box = _box((0.02, 0.02, 0.01))
    audit = audit_mesh("box", box.vertices, box.faces[:, ::-1], _part(float(box.volume), box))
    assert any("wound inward" in p for p in audit.problems)


def test_self_intersecting_shells_fail() -> None:
    a = _box((0.02, 0.02, 0.01))
    b = _box((0.02, 0.02, 0.01))
    b.apply_translation([0.01, 0.0, 0.0])
    both = trimesh.util.concatenate([a, b])  # two closed shells overlapping by half
    union = a.union(b, engine="manifold")
    audit = audit_mesh("box", both.vertices, both.faces, _part(float(union.volume), union))
    assert any("above CAD volume" in p for p in audit.problems)


@pytest.mark.parametrize("excess, ok", [(0.015, True), (0.03, False)])
def test_volume_excess_is_limited_to_the_base_tolerance(excess: float, ok: bool) -> None:
    box = _box((0.002, 0.002, 0.02))  # thin: A c / V ~ 5 %, which must not widen the excess limit
    audit = audit_mesh("pin", box.vertices, box.faces, _part(float(box.volume) / (1 + excess), box))
    assert audit.allowed_deficit > 0.04
    assert (audit.problems == []) is ok


# (e) seam-loop size limit -------------------------------------------------------------------------
@pytest.mark.parametrize("fraction, filled", [(0.9, True), (1.1, False)])
def test_seam_loop_size_limit(fraction: float, filled: bool) -> None:
    vertices, faces = _sliver_box(0.02, 0.01, fraction * C)
    assert not trimesh.Trimesh(vertices, faces, process=False).is_watertight
    repair = repair_seams(vertices, faces)
    assert repair.seam_loops_filled == int(filled)
    assert len(repair.open_loops) == int(not filled)
    audit = audit_mesh("box", vertices, faces, _part(0.02 * 0.02 * 0.01, bounds=np.array([[0, 0, 0], [0.02, 0.02, 0.01]])))
    assert audit.watertight is filled
    assert (audit.problems == []) is filled
    if filled:
        assert audit.volume == pytest.approx(4e-6, rel=1e-9)


def test_opposite_duplicate_faces_cancel_and_same_duplicates_collapse() -> None:
    faces = np.array([[0, 1, 2], [1, 0, 2], [3, 4, 5], [4, 5, 3], [6, 7, 7]])
    out, degenerate, duplicate = remove_degenerate_and_duplicate_faces(faces)
    assert degenerate == 1 and duplicate == 3
    assert out.tolist() == [[3, 4, 5]]


# routing mesh -----------------------------------------------------------------------------------
def test_routing_mesh_must_be_a_closed_oriented_surface() -> None:
    box = _box((0.02, 0.02, 0.01))
    part = _part(float(box.volume), box)
    assert audit_routing_mesh("box", box.vertices, box.faces, part, 0.001).problems == []
    opened = audit_routing_mesh("box", box.vertices, box.faces[1:], part, 0.001)
    assert opened.unbalanced_edges == 3
    assert any("unbalanced edges" in p for p in opened.problems)


def test_routing_mesh_non_manifold_but_closed_passes() -> None:
    # Two boxes sharing one edge: not a 2-manifold (trimesh says not watertight), but every directed
    # edge cancels, so the winding number is exact and the routing sign is reliable.
    a = _box((0.01, 0.01, 0.01))
    b = _box((0.01, 0.01, 0.01))
    b.apply_translation([0.01, 0.01, 0.0])
    both = trimesh.util.concatenate([a, b])
    both.merge_vertices()
    assert unbalanced_edge_count(both.faces) == 0
    bounds = both.bounds
    audit = audit_routing_mesh("pair", both.vertices, both.faces, _part(2e-6, bounds=bounds), 0.001)
    assert audit.problems == []


def test_routing_mesh_bounds_limit_is_one_cell_diagonal() -> None:
    box = _box((0.02, 0.02, 0.01))
    shifted = box.vertices + [0.0025, 0.0, 0.0]
    audit = audit_routing_mesh("box", shifted, box.faces, _part(float(box.volume), box), 0.001)
    assert audit.bbox_limit == pytest.approx(0.001 * math.sqrt(3) + 0.0005)
    assert any("bounds differ" in p for p in audit.problems)
