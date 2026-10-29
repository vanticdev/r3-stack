"""Clustering must preserve edge balance when thin solids collapse."""

import numpy as np
import pytest
import trimesh
from types import SimpleNamespace

from r3_wiring.geometry import cluster_decimate
from r3_wiring.geometry_audit import unbalanced_edge_count
from r3_wiring.obstacles import _cad_shape


@pytest.mark.parametrize("reverse", [False, True])
def test_collapsed_tetrahedron_cancels_opposite_faces(reverse: bool) -> None:
    # The two tips share a cell. Two faces degenerate and the remaining two
    # coincide with opposite winding. Keeping one would leave three open edges.
    vertices = np.array([[0.1, 0.1, 0.1], [2.1, 0.1, 0.1],
                         [0.1, 2.1, 0.1], [0.2, 0.2, 0.2]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
    if reverse:
        faces = faces[:, ::-1]
    assert unbalanced_edge_count(faces) == 0
    _, clustered = cluster_decimate(vertices, faces, cell=1.0)
    assert clustered.shape == (0, 3)


def test_thin_feature_cancellation_preserves_remaining_solid() -> None:
    box = trimesh.creation.box(extents=[0.02, 0.02, 0.02])
    thin = np.array([[0.0301, 0.0001, 0.0001], [0.0321, 0.0001, 0.0001],
                     [0.0301, 0.0021, 0.0001], [0.0302, 0.0002, 0.0002]])
    tip_faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
    vertices = np.vstack([box.vertices, thin])
    faces = np.vstack([box.faces, tip_faces + len(box.vertices)])
    merged, clustered = cluster_decimate(vertices, faces, cell=0.001)
    assert len(clustered) == len(box.faces)
    assert unbalanced_edge_count(clustered) == 0
    assert trimesh.Trimesh(merged, clustered, process=False).volume == pytest.approx(box.volume)


def test_subcell_obstacle_keeps_its_cad_surface(tmp_path) -> None:
    box = trimesh.creation.box(extents=[0.0002, 0.0002, 0.0002])
    box.apply_translation([0.0004, 0.0004, 0.0004])
    mesh = tmp_path / "build" / "cad" / "magnet.stl"
    mesh.parent.mkdir(parents=True)
    box.export(mesh)
    shape, source = _cad_shape(SimpleNamespace(root=tmp_path), {"mesh": "magnet.stl"})
    assert source == "cad_mesh"
    assert len(shape.faces) == len(box.faces)
    assert unbalanced_edge_count(shape.faces) == 0
    assert shape.sdf(np.array([[0.0004, 0.0004, 0.0004]]))[0] < 0
