"""Read pulled STL/STEP for fabrication, in part-local millimetres."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


MM = 1e3  # m -> mm


@dataclass
class PartGeometry:
    mesh: trimesh.Trimesh  # component frame, mm, watertight
    source: str  # pulled_stl
    notes: list[str] = field(default_factory=list)
    holes: list[dict[str, Any]] = field(default_factory=list)  # cut holes: interface, kind, d_mm, centre/axis (mm)



def build_geometry(project: Any, cid: str) -> PartGeometry:
    """Read the pulled part-local STL in print units (millimetres)."""
    from r3_model.pulled import PullError
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_onshape.step_geometry import part_from_step

    pulled = load_pulled(project.root)
    if cid not in pulled.parts:
        raise PullError("cad_pull_missing", f"{cid}: no pulled part")
    part = pulled.parts[cid]
    from r3_model.mesh_io import load_exported_stl
    mesh = load_exported_stl(part.stl)
    if not isinstance(mesh, trimesh.Trimesh) or not mesh.is_volume:
        raise PullError("cad_pull_invalid", f"{cid}: pulled STL is not a closed positive-volume mesh")
    mesh.apply_scale(MM)
    # Holes are measured from STEP, never synthesized from catalog interfaces.
    exact = part_from_step(cid, part.step)
    holes = [{"interface": face.id, "kind": "bore", "d_mm": 2 * face.radius * MM,
              "center_mm": (face.origin * MM).tolist(), "axis": face.direction.tolist(),
              "depth_mm": float(np.ptp(face.triangles.reshape(-1, 3) @ face.direction)) * MM}
             for face in exact.faces_of("CYLINDER") if face.convex is False]
    return PartGeometry(mesh=mesh, source="pulled_stl", holes=holes,
                        notes=[f"Pinned pulled STL; bores measured from STEP: {part.step.name}"])


def rotation_from_euler_deg(angles: list[float]) -> np.ndarray:
    """R = Rz(rz) Ry(ry) Rx(rx), the spec rpy convention, from degrees."""
    from r3_model.graph import rpy_matrix

    return rpy_matrix([math.radians(float(a)) for a in angles])


def euler_deg_from_rotation(rotation: np.ndarray) -> list[float]:
    from r3_model.assembly import rpy_from_matrix

    return [round(math.degrees(a), 6) + 0.0 for a in rpy_from_matrix(rotation)]
