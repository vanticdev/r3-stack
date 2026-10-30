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
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from pulled_fixture import write_pull
    import yaml

    (tmp_path / "robot.yaml").write_text(yaml.safe_dump({"robot": {"name": "Subcell asset"},
        "components": {"magnet": {"type": "fixture-magnet"}}}))
    box = BRepPrimAPI_MakeBox(gp_Pnt(.3, .3, .3), .2, .2, .2).Shape()
    write_pull(tmp_path, {"magnet": box})
    shape, source = _cad_shape(SimpleNamespace(root=tmp_path), {"part_name": "magnet"})
    assert source == "pulled_stl"
    assert len(shape.faces) == 12
    assert unbalanced_edge_count(shape.faces) == 0
    assert shape.sdf(np.array([[0.0004, 0.0004, 0.0004]]))[0] < 0


def test_coincident_faces_keep_oriented_multiplicity() -> None:
    # Distinct closed tetrahedra acquire the same base after clustering. Their
    # tips stay distinct, so deleting one equally wound base opens three edges.
    first = np.array([[.1, .1, .1], [2.1, .1, .1], [.1, 2.1, .1], [.1, .1, 2.1]])
    second = first + [.2, .2, .2]
    second[3, 2] = 4.1
    tetra = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
    vertices, faces = np.vstack([first, second]), np.vstack([tetra, tetra + 4])
    assert unbalanced_edge_count(faces) == 0
    _, clustered = cluster_decimate(vertices, faces, cell=1.)
    assert len(clustered) == 8
    assert unbalanced_edge_count(clustered) == 0
