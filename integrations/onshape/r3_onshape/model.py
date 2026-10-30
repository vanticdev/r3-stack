"""Analytic STEP faces and tessellated solids used by mechanical checks.

Constructed only from local STEP via :mod:`r3_onshape.step_geometry`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

CHORD_TOLERANCE = 5e-5  # m: max chordal deviation of the tessellation
ANGLE_TOLERANCE = math.radians(2.0)  # rad: max angle between facet normals across an edge


@dataclass
class Face:
    id: str
    part: str
    kind: str  # PLANE | CYLINDER | CONE | SPHERE | TORUS | OTHER
    origin: np.ndarray
    direction: np.ndarray  # plane: outward normal; cylinder/cone: axis (unit); sphere: zeros
    radius: float
    area: float
    lo: np.ndarray
    hi: np.ndarray
    convex: bool | None = None  # cylinders/spheres: True = shaft/ball, False = bore/socket
    triangles: np.ndarray | None = None  # (n, 3, 3) facets of this face


@dataclass
class PartGeometry:
    name: str
    part_id: str
    faces: list[Face] = field(default_factory=list)
    vertices: np.ndarray | None = None
    triangles: np.ndarray | None = None  # (m, 3) vertex indices
    _manifold: Any = None

    @property
    def lo(self) -> np.ndarray:
        return self.vertices.min(axis=0)

    @property
    def hi(self) -> np.ndarray:
        return self.vertices.max(axis=0)

    def manifold(self):
        """Closed manifold3d solid of the welded tessellation."""
        if self._manifold is None:
            import manifold3d as m3d

            mesh = m3d.Mesh(vert_properties=np.asarray(self.vertices, dtype=np.float32),
                            tri_verts=np.asarray(self.triangles, dtype=np.uint32))
            solid = m3d.Manifold(mesh)
            if solid.status() != m3d.Error.NoError or solid.is_empty():
                raise ValueError(f"{self.name}: tessellation is not a closed manifold ({solid.status()})")
            self._manifold = solid
        return self._manifold

    def faces_of(self, kind: str) -> list[Face]:
        return [f for f in self.faces if f.kind == kind]


@dataclass
class GeometryModel:
    parts: dict[str, PartGeometry]
    microversion: str = ""
    chord_tolerance: float = CHORD_TOLERANCE
    angle_tolerance: float = ANGLE_TOLERANCE
    source: str = "onshape"

    def __getitem__(self, name: str) -> PartGeometry:
        return self.parts[name]

    def __contains__(self, name: str) -> bool:
        return name in self.parts


# ---------------------------------------------------------------- construction
def _weld(triangles: np.ndarray, tolerance: float = 1e-8) -> tuple[np.ndarray, np.ndarray]:
    """Merge coincident vertices of a triangle soup (face tessellations share
    their edge vertices) into an indexed mesh."""
    flat = triangles.reshape(-1, 3)
    keys = np.round(flat / tolerance).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    tri = inverse.reshape(-1, 3)
    keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])
    return flat[first], tri[keep]


def _orient_faces_from_mesh(part: PartGeometry) -> None:
    """Cross-check face orientation with the outward facet normals of the mesh
    (tessellations are wound outward): fixes plane normals and convexity."""
    for face in part.faces:
        if face.triangles is None or len(face.triangles) == 0:
            continue
        t = face.triangles
        normals = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        weights = np.linalg.norm(normals, axis=1)
        if weights.sum() == 0:
            continue
        centers = t.mean(axis=1)
        if face.kind == "PLANE":
            mean = (normals).sum(axis=0)
            if np.linalg.norm(mean) > 0 and float(mean @ face.direction) < 0:
                face.direction = -face.direction
        elif face.kind == "CYLINDER":
            a = face.direction
            radial = (centers - face.origin) - np.outer((centers - face.origin) @ a, a)
            face.convex = bool(float(np.sum(np.einsum("ij,ij->i", normals, radial))) > 0)
        elif face.kind == "SPHERE":
            face.convex = bool(float(np.sum(np.einsum("ij,ij->i", normals, centers - face.origin))) > 0)
