"""Read exported STL without rounding distinct CAD coordinates or repairing it."""
from pathlib import Path

import numpy as np
import trimesh


def load_exported_stl(path: str | Path) -> trimesh.Trimesh:
    """Join only identical STL vertices; leave every coordinate and facet intact.

    STL repeats vertices per triangle. Trimesh's default decimal-rounded merge
    can collapse distinct small features in SI units and manufacture open edges.
    Exact deduplication restores shared indices without welding nearby geometry,
    filling holes, changing winding, or discarding faces. Callers still validate.
    """
    mesh = trimesh.load_mesh(path, file_type="stl", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError("Exported STL must contain one triangle mesh")
    vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    return trimesh.Trimesh(vertices=vertices, faces=inverse[mesh.faces],
                          face_normals=mesh.face_normals, metadata=mesh.metadata, process=False)
