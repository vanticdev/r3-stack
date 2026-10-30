"""Read a supplied STEP's real faces for offline fit checks; never author solids."""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from typing import Any

import numpy as np

from r3_connectors.frames import Frame
from .model import ANGLE_TOLERANCE, CHORD_TOLERANCE, Face, PartGeometry, _orient_faces_from_mesh, _weld


def supplied_geometry(part: Any) -> PartGeometry:
    from r3_connectors.models import resolve_step
    if part.mirror is not None:
        raise ValueError(f"{part.key}: a purchased STEP cannot be mirrored; source the required handed part")
    path = resolve_step(part.source_model)
    return _cached_geometry(part.key, str(path), path.stat().st_mtime_ns,
                            part.source_model.placement.model_dump_json())


@lru_cache(maxsize=64)
def _cached_geometry(name: str, path: str, modified: int, placement: str) -> PartGeometry:
    from r3_catalog.schema import Placement
    return read_step(name, Path(path), Frame.from_placement(Placement.model_validate_json(placement)))


def read_step(name: str, path: Path, placement: Frame) -> PartGeometry:
    """Tessellate STEP with Open CASCADE and retain its analytic face surfaces.

    STEPControl normalizes length units to millimetres. Placement and the
    returned geometry use metres, like the rest of the CAD stage.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError(f"{name}: cannot read supplied STEP {path.name}")
    shape = reader.OneShape()
    BRepMesh_IncrementalMesh(shape, CHORD_TOLERANCE * 1000, False, ANGLE_TOLERANCE, False)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    faces = []
    while explorer.More():
        cast = getattr(TopoDS, "Face_s", None) or TopoDS.Face
        face = cast(explorer.Current())
        explorer.Next()
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            raise ValueError(f"{name}: STEP face has no tessellation")
        vertices = np.array([mesh.Node(i).Transformed(location.Transformation()).Coord()
                             for i in range(1, mesh.NbNodes() + 1)]) / 1000
        indices = np.array([mesh.Triangle(i).Get() for i in range(1, mesh.NbTriangles() + 1)]) - 1
        if face.Orientation() == TopAbs_REVERSED:
            indices = indices[:, ::-1]
        triangles = placement.points(vertices)[indices]
        normal_vectors = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        area = np.linalg.norm(normal_vectors, axis=1).sum() / 2
        surface = BRepAdaptor_Surface(face)
        kind, origin, direction, radius = "OTHER", np.zeros(3), np.zeros(3), 0.0
        surface_type = surface.GetType()
        if surface_type == GeomAbs_Plane:
            value, kind = surface.Plane(), "PLANE"
        elif surface_type == GeomAbs_Cylinder:
            value, kind = surface.Cylinder(), "CYLINDER"
            radius = value.Radius() / 1000
        elif surface_type == GeomAbs_Cone:
            value, kind = surface.Cone(), "CONE"
            radius = value.RefRadius() / 1000
        elif surface_type == GeomAbs_Sphere:
            value, kind = surface.Sphere(), "SPHERE"
            radius = value.Radius() / 1000
        if kind != "OTHER":
            origin = placement.point(np.array(value.Location().Coord()) / 1000)
            direction = (np.zeros(3) if kind == "SPHERE" else
                         placement.vector(value.Axis().Direction().Coord()))
        points = triangles.reshape(-1, 3)
        faces.append(Face(str(len(faces)), name, kind, origin, direction, radius,
                          float(area), points.min(axis=0), points.max(axis=0), triangles=triangles))
    if not faces:
        raise ValueError(f"{name}: supplied STEP is empty")
    result = PartGeometry(name, f"step:{name}", faces)
    result.vertices, result.triangles = _weld(np.concatenate([face.triangles for face in faces]))
    _orient_faces_from_mesh(result)
    result.manifold()  # Reject open geometry instead of silently replacing it with an envelope.
    return result
