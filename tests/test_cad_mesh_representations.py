"""Audit CAD exports separately from the deliberately coarser routing surface."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from r3_wiring.obstacles import _cad_shape
from r3_wiring.witness import audit_cad
from r3_wiring.geometry_audit import audit_routing_mesh


@pytest.mark.parametrize("offset", [[.130, .066, .045], [.130, -.066, .045], [0, 0, .060], [.0014, .0014, .0014]])
def test_thin_bored_part_roundtrip_has_no_mesh_diagnostic(tmp_path, offset):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    import yaml
    from r3_model.project import Project
    from r3_model.pulled import load_pulled
    from r3_wiring.obstacles import build_obstacles
    from pulled_fixture import write_pull

    (tmp_path / "robot.yaml").write_text(yaml.safe_dump({"robot": {"name": "mesh audit fixture"},
        "components": {"cap": {"type": "coupon"}}}))
    directory = tmp_path / "catalog/components"
    directory.mkdir(parents=True)
    (directory / "coupon.yaml").write_text(yaml.safe_dump({"components": [{"id": "coupon", "kind": "structure", "name": "Coupon",
        "mechanical": {"size": [.005,.005,.0024], "mass": .001}}]}))
    ring = BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(2.5,2.4).Shape(),BRepPrimAPI_MakeCylinder(1.3,2.4).Shape()).Shape()
    pose = np.eye(4); pose[:3,3] = offset
    write_pull(tmp_path, {"cap": ring}, poses={"cap": pose})
    project = Project.load(tmp_path)
    obstacles, _ = build_obstacles(project)
    diagnostics, audit = audit_cad(project, obstacles)
    assert diagnostics == [] and audit["invalid"] == []
    assert audit["parts"]["cap"]["routing_mesh"]["unbalanced_edges"] == 0
    assert abs(audit["parts"]["cap"]["volume_error"]) < 1e-5


def test_empty_routing_surface_cannot_pass_by_having_zero_unbalanced_edges():
    # A subcell solid can collapse completely. The obstacle builder must keep its
    # original CAD surface; an empty triangle set is not a valid closed obstacle.
    audit = audit_routing_mesh("magnet", np.array([[.0004, .0004, .0004]]), np.empty((0, 3), dtype=int), {}, .001)
    assert audit.unbalanced_edges == 0
    assert audit.problems == ["routing mesh has no triangles (the obstacle disappeared during clustering)"]
