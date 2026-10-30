"""Pulled native supports retain real openings and solved poses."""
import numpy as np
import pytest
import yaml

from r3_model.project import Project
from r3_wiring.obstacles import build_obstacles
from r3_wiring.geometry import MeshShape


def test_native_support_preserves_opening_and_pose(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Pnt
    from pulled_fixture import write_pull
    from r3_model.graph import rpy_matrix

    folder = tmp_path / "catalog/components"
    folder.mkdir(parents=True)
    (folder / "bridge.yaml").write_text(yaml.safe_dump({"components": [{
        "id": "instrument-bridge", "kind": "structure", "name": "Instrument bridge",
        "mechanical": {"mass": .02, "size": ["60 mm", "20 mm", "40 mm"]}}]}))
    (tmp_path / "robot.yaml").write_text(yaml.safe_dump({"robot": {"name": "Optical fixture"},
        "components": {"bridge": {"type": "instrument-bridge"}}}))
    outer = BRepPrimAPI_MakeBox(gp_Pnt(-30, -10, 0), 60, 20, 40).Shape()
    opening = BRepPrimAPI_MakeBox(gp_Pnt(-20, -15, -1), 40, 30, 31).Shape()
    frame = np.eye(4)
    frame[:3, :3] = rpy_matrix([0, 0, np.pi / 2])
    frame[:3, 3] = [.1, .2, .01]
    write_pull(tmp_path, {"bridge": BRepAlgoAPI_Cut(outer, opening).Shape()}, poses={"bridge": frame})
    obstacles, diagnostics = build_obstacles(Project.load(tmp_path))
    shape = obstacles.by_component("bridge").shape
    assert isinstance(shape, MeshShape)
    distances = shape.sdf(np.array([[.1, .2, .025], [.1, .225, .025], [.1, .2, .045]]))
    assert distances == pytest.approx([.015, -.005, -.005], abs=2e-5)
    assert not obstacles.estimated
    assert not any(d.code == "cad_geometry_estimated" for d in diagnostics)
