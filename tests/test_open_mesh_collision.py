"""Open display meshes keep conservative collision geometry instead of none."""
import numpy as np
import trimesh

from r3_wiring.mujoco_cables import convex_pieces


def test_closed_mesh_still_splits_exactly():
    box = trimesh.creation.box((1.0, 2.0, 3.0))
    pieces, hull_only = convex_pieces(np.asarray(box.vertices), np.asarray(box.faces))
    assert len(pieces) == 1 and hull_only == 0


def test_open_mesh_keeps_its_hull_and_reports_it():
    box = trimesh.creation.box((1.0, 2.0, 3.0))
    open_faces = np.asarray(box.faces)[:-2]  # an open seam, as display meshes can have
    pieces, hull_only = convex_pieces(np.asarray(box.vertices), open_faces)
    assert hull_only == 1 and len(pieces) == 1
    assert np.allclose(pieces[0].min(axis=0), box.vertices.min(axis=0))
    assert np.allclose(pieces[0].max(axis=0), box.vertices.max(axis=0))


def test_mesh_without_volume_has_no_hull_to_invent():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    pieces, hull_only = convex_pieces(vertices, np.array([[0, 1, 2]]))
    assert pieces == [] and hull_only == 0
