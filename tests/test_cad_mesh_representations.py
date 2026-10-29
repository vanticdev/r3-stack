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
    # Small screw-cap/standoff scale: clustering changes the volume substantially.
    # The CAD audit checks the unclustered STL; routing checks edge balance/bounds.
    mesh = trimesh.creation.annulus(r_min=.0013, r_max=.0025, height=.0024, sections=64)
    mesh.apply_translation(offset)
    build = tmp_path / "build"
    path = build / "cad" / "cap.stl"
    path.parent.mkdir(parents=True)
    mesh.export(path)
    entry = {"mesh": "cap.stl", "volume": float(mesh.volume), "bbox_min": mesh.bounds[0].tolist(),
             "bbox_max": mesh.bounds[1].tolist()}
    class Project:
        root = tmp_path
        def read_build_json(self, path):
            return {"parts": {"cap": entry}} if path == "cad/cad_model.json" else {}
        def fingerprint(self): return {"robot_yaml_sha256": "fixture"}
    project = Project()
    shape, source = _cad_shape(project, entry)
    diagnostics, audit = audit_cad(project, SimpleNamespace(obstacles=[SimpleNamespace(component="cap", source=source, shape=shape)]))
    assert diagnostics == [] and audit["invalid"] == []
    assert audit["parts"]["cap"]["routing_mesh"]["unbalanced_edges"] == 0
    assert abs(audit["parts"]["cap"]["volume_error"]) < 1e-5


def test_empty_routing_surface_cannot_pass_by_having_zero_unbalanced_edges():
    # A subcell solid can collapse completely. The obstacle builder must keep its
    # original CAD surface; an empty triangle set is not a valid closed obstacle.
    audit = audit_routing_mesh("magnet", np.array([[.0004, .0004, .0004]]), np.empty((0, 3), dtype=int), {}, .001)
    assert audit.unbalanced_edges == 0
    assert audit.problems == ["routing mesh has no triangles (the obstacle disappeared during clustering)"]
