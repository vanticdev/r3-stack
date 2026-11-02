"""Analytic faces and boolean solids read from immutable, pulled STEP files.

OpenCascade reads STEP units into metres. Tessellation uses the existing fit
chord/angle tolerances; no catalog geometry or native service is consulted.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    from r3_model.project import Project
    from r3_model.diagnostics import StageResult

from r3_model.pulled import PulledModel, PullError, load_pulled
from .model import (ANGLE_TOLERANCE, CHORD_TOLERANCE, Face, GeometryModel,
                    PartGeometry, _weld, _orient_faces_from_mesh)


def read_step_shape(path: Path):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    # STEPControl's target system length unit is metres; source units are read
    # from the STEP header, including exports authored in mm or inches.
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise PullError("cad_pull_invalid", f"Cannot read STEP solid: {path.name}")
    reader.SetSystemLengthUnit(1000.0)
    if not reader.TransferRoots():
        raise PullError("cad_pull_invalid", f"Cannot transfer STEP solid: {path.name}")
    shape = reader.OneShape()
    from OCP.BRepCheck import BRepCheck_Analyzer
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise PullError("cad_pull_invalid", f"Invalid STEP solid: {path.name}")
    return shape


def part_from_step(name: str, path: Path, pose: np.ndarray | None = None) -> PartGeometry:
    return _part_from_shape(name, read_step_shape(path), pose)


def _part_from_shape(name: str, shape: Any, pose: np.ndarray | None = None) -> PartGeometry:
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopLoc import TopLoc_Location
    from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone,
                             GeomAbs_Sphere, GeomAbs_Torus)
    solids, explorer = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    if len(solids) > 1:
        if TopExp_Explorer(shape, TopAbs_FACE, TopAbs_SOLID).More():
            raise PullError("cad_pull_invalid", f"{name}: STEP contains faces outside its native solids")
        # STEP carries native solid ownership. Welding separate solids together
        # at coincident coordinates destroys that topology (e.g. edge contact).
        # Keep the same tessellation/check tolerances within each real solid.
        children = [_part_from_shape(name, solid, pose) for solid in solids]
        faces, vertices, triangles, offset = [], [], [], 0
        for child in children:
            for face in child.faces:
                face.id = str(len(faces))
                faces.append(face)
            vertices.append(child.vertices)
            triangles.append(child.triangles + offset)
            offset += len(child.vertices)
        part = PartGeometry(name, name, faces, np.concatenate(vertices), np.concatenate(triangles))
        part.manifold()  # unchanged closed-manifold qualification, no repair
        return part
    mesher = BRepMesh_IncrementalMesh(shape, CHORD_TOLERANCE, False, ANGLE_TOLERANCE, False)
    if not mesher.IsDone():
        raise PullError("cad_pull_invalid", f"STEP tessellation failed: {name}")
    pose = np.eye(4) if pose is None else pose
    rot, trans = pose[:3, :3], pose[:3, 3]
    faces, soup = [], []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        cast = getattr(TopoDS, "Face_s", None) or TopoDS.Face
        face = cast(explorer.Current())
        explorer.Next()
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            raise PullError("cad_pull_invalid", f"STEP face has no tessellation: {name}")
        vertices = np.array([mesh.Node(i).Transformed(location.Transformation()).Coord()
                             for i in range(1, mesh.NbNodes() + 1)])
        indices = np.array([mesh.Triangle(i).Get() for i in range(1, mesh.NbTriangles() + 1)]) - 1
        if face.Orientation() == TopAbs_REVERSED:
            indices = indices[:, ::-1]
        triangles = (vertices @ rot.T + trans)[indices]
        surface = BRepAdaptor_Surface(face)
        kind, origin, axis, radius = "OTHER", np.zeros(3), np.zeros(3), 0.0
        typ = surface.GetType()
        if typ == GeomAbs_Plane:
            kind, geom = "PLANE", surface.Plane()
        elif typ == GeomAbs_Cylinder:
            kind, geom = "CYLINDER", surface.Cylinder()
            radius = geom.Radius()
        elif typ == GeomAbs_Cone:
            kind, geom = "CONE", surface.Cone()
            radius = geom.RefRadius()
        elif typ == GeomAbs_Sphere:
            kind, geom = "SPHERE", surface.Sphere()
            radius = geom.Radius()
        elif typ == GeomAbs_Torus:
            kind, geom = "TORUS", surface.Torus()
            radius = geom.MajorRadius()
        if kind != "OTHER":
            origin = np.array(geom.Location().Coord())
            axis = np.zeros(3) if kind == "SPHERE" else np.array(geom.Axis().Direction().Coord())
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        pts = triangles.reshape(-1, 3)
        faces.append(Face(str(len(faces)), name, kind, rot @ origin + trans,
                          rot @ axis, radius, props.Mass(), pts.min(0), pts.max(0),
                          triangles=triangles))
        soup.append(triangles)
    if not soup:
        raise PullError("cad_pull_invalid", f"STEP contains no faces: {name}")
    vertices, triangles = _weld(np.concatenate(soup))
    part = PartGeometry(name, name, faces, vertices, triangles)
    _orient_faces_from_mesh(part)
    # A failed boolean must never become a zero-interference result.
    try:
        part.manifold()
    except ValueError as error:
        raise PullError("cad_pull_invalid", str(error)) from error
    return part


def geometry_from_pulled(pulled: PulledModel) -> GeometryModel:
    if any(part.step is None for part in pulled.parts.values()):
        raise PullError('cad_analytic_geometry_pending',
            'Native meshes are available; analytic face qualification is not yet available for this mesh prototype')
    return GeometryModel({name: part_from_step(name, part.step, pulled.poses[name])
                          for name, part in pulled.parts.items()},
                         microversion=pulled.version_id, source="pulled_step")


def load_geometry(project_root: Path) -> GeometryModel:
    return geometry_from_pulled(load_pulled(project_root))


from functools import lru_cache
from typing import Any
from r3_connectors.frames import Frame


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
    """Compatibility API for externally supplied, part-local STEP solids."""
    return part_from_step(name, path, placement.matrix())


def verify_pulled(project: Project, result: StageResult, pulled: PulledModel | None = None) -> dict[str, Any]:
    """CAD-stage hook: ``payload = verify_pulled(project, result, pulled)``.

    Reads only pinned local inputs, appends closure/fit/motion diagnostics to
    the caller's StageResult, and returns the world-mesh presentation payload
    with fit and motion evidence. Missing/invalid inputs yield one PullError
    diagnostic and an empty payload. No native transport is used.
    """
    from r3_model.diagnostics import Diagnostic
    from r3_model.pulled_view import apply_pulled, cad_view
    from .fit import FitChecker
    from .motion import run_motion

    try:
        pulled = pulled or load_pulled(project.root)
        assembly = apply_pulled(project, pulled)
        result.extend(assembly.diagnostics)
        if any(part.step is None for part in pulled.parts.values()):
            result.add(Diagnostic.warning('cad_geometry_estimated',
                'Native mesh model is available; analytic fit and CAD motion qualification remain pending'))
            result.metrics.update(pulled_parts=len(pulled.parts), pulled_mates=len(pulled.mates),
                                  interference_verified=False)
            payload = cad_view(project)
            payload.update(geometry_qualification='display_mesh',
                           fit={'verified': False, 'status': 'not_run'},
                           motion={'verified': False, 'status': 'not_run'}, assembly=assembly.to_json())
            return payload
        model = geometry_from_pulled(pulled)
        materials = {name: part.material or "" for name, part in pulled.parts.items()}
        fit = FitChecker(project, model, materials, result).run()
        motion = run_motion(project, model, fit, result) if not assembly.errors else {}
        payload = cad_view(project)
        payload.update(fit=fit.summary(), motion=motion, assembly=assembly.to_json())
        result.metrics.update(pulled_parts=len(pulled.parts), pulled_mates=len(pulled.mates),
                              verified_fasteners=len(fit.verified_fasteners),
                              verified_interfaces=len(fit.verified_interfaces))
        return payload
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        return {}
