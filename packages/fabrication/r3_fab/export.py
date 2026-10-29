"""Print-ready files: STL and 3MF (mm) in the suggested orientation, and print estimates."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

FILAMENT_DIAMETER_MM = 1.75
LINE_WIDTH_MM = 0.45  # Prusa KB: 0.45 mm extrusion width for a 0.4 mm nozzle


def bed_placed(part: trimesh.Trimesh, z_rotation_deg: float, bed_mm: list[float] | None) -> trimesh.Trimesh:
    """Oriented part turned about z (to fit the bed), centred on the bed, bottom at z = 0."""
    out = part.copy()
    if z_rotation_deg:
        out.apply_transform(trimesh.transformations.rotation_matrix(math.radians(z_rotation_deg), [0, 0, 1]))
    lo, hi = out.bounds
    center = [bed_mm[0] / 2, bed_mm[1] / 2] if bed_mm else [0.0, 0.0]
    out.apply_translation([center[0] - (lo[0] + hi[0]) / 2, center[1] - (lo[1] + hi[1]) / 2, -lo[2]])
    return out


def write_files(part: trimesh.Trimesh, directory: Path, cid: str) -> dict[str, str]:
    """``<cid>.stl`` and ``<cid>.3mf`` (millimetres)."""
    directory.mkdir(parents=True, exist_ok=True)
    stl, threemf = directory / f"{cid}.stl", directory / f"{cid}.3mf"
    part.export(stl)
    trimesh.exchange.export.export_mesh(part, threemf, file_type="3mf")
    return {"stl": str(stl), "3mf": str(threemf)}


def print_estimate(volume_mm3: float, area_mm2: float, density: float, infill: float, perimeters: int) -> dict[str, float]:
    """Mass (shell of ``perimeters`` x 0.45 mm plus ``infill`` of the core; an estimate) and the
    1.75 mm filament length: L = m / (rho pi d^2 / 4)."""
    shell = min(volume_mm3, area_mm2 * perimeters * LINE_WIDTH_MM)
    material_mm3 = shell + max(0.0, volume_mm3 - shell) * infill
    mass = material_mm3 * 1e-9 * density
    length = material_mm3 / (math.pi * (FILAMENT_DIAMETER_MM / 2) ** 2)  # mm of filament
    return {"solid_volume_mm3": volume_mm3, "printed_volume_mm3": material_mm3, "mass_kg": mass, "filament_length_m": length * 1e-3,
            "solid_mass_kg": volume_mm3 * 1e-9 * density}


def readable(path: Path) -> bool:
    try:
        mesh = trimesh.load(path, force="mesh")
    except Exception:  # noqa: BLE001
        return False
    return isinstance(mesh, trimesh.Trimesh) and len(mesh.faces) > 0 and np.isfinite(mesh.vertices).all()
