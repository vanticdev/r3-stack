"""Organized Part Studios built with standard Onshape features (no FeatureScript).

One Part Studio per object type (or tightly related family), each part modeled
at its own origin the way an engineer does it in the UI:

* every sketch sits on the standard Top/Front/Right plane, is fully constrained
  and dimensioned (Onshape reports ``WELL_DEFINED``), and is named for what it
  draws; profiles away from the plane are extruded with a *start offset*, so a
  Part Studio needs no construction planes at all;
* holes are Hole features (ISO clearance or tapped) started from the part face
  they enter, found in ``bodydetails`` (no plane is created for them);
* right-hand parts are a Mirror feature of the left part about the Front plane;
* every interface gets a named mate connector ``mc_<interface>`` (plus
  ``mc_<interface>_h<n>`` screw/nut seats at the far face of clearance holes),
  placed on the part origin with a transform;
* key dimensions reference the Variable Studio (``#plate_thickness`` ...);
* part properties: name, appearance, part number, vendor, material, mass source.

Feature ids are recorded per feature folder so :mod:`r3_onshape.organize` can
group them (feature folders are a browser-native operation; see
``docs/architecture/headless.md``).
"""

from __future__ import annotations

import math
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from . import features as fx
from .client import OnshapeError, Transport
from .geometry import PLANE_IDS, Hole, Loop, Plane, Prism, Revolve, Segment, unit
from .parts import ConnectorSpec, LocalPart

ORIGIN_ID = "IB"  # deterministic id of the Part Studio origin point
SECONDARY = {"PLUS_X": 0.0, "PLUS_Y": -math.pi / 2, "MINUS_X": math.pi, "MINUS_Y": math.pi / 2}


class StudioBuildError(RuntimeError):
    def __init__(self, part: str, feature: str, detail: str = "") -> None:
        super().__init__(f"{part}: feature {feature!r} failed {detail}".strip())
        self.part, self.feature = part, feature


@dataclass
class Variable:
    name: str
    expression: str
    description: str
    value: float  # SI


@dataclass
class BuiltStudio:
    element_id: str
    name: str
    part_ids: dict[str, str] = field(default_factory=dict)  # part key -> Onshape part id
    folders: dict[str, list[str]] = field(default_factory=dict)  # folder -> feature ids in order
    connectors: dict[str, dict[str, str]] = field(default_factory=dict)  # part key -> mc name -> feature id
    features: int = 0
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ sketch constraints
def _s(pid: str, value: str) -> dict[str, Any]:
    return {"btType": "BTMParameterString-149", "parameterId": pid, "value": value}


def _origin(pid: str = "externalSecond") -> dict[str, Any]:
    return fx.qlist(pid, [fx.det(ORIGIN_ID)])


def _constraint(cid: str, kind: str, params: list[dict[str, Any]]) -> dict[str, Any]:
    return {"btType": "BTMSketchConstraint-2", "constraintType": kind, "entityId": cid, "parameters": params}


def _dim(cid: str, first: str, second: dict[str, Any] | str, direction: str, expression: str) -> dict[str, Any]:
    other = _s("localSecond", second) if isinstance(second, str) else second
    return _constraint(cid, "DISTANCE", [_s("localFirst", first), other, fx.enum("direction", "DimensionDirection", direction),
                                         fx.qty("length", expression), fx.qty("labelDistance", "0.004 m"),
                                         fx.qty("labelAngle", "0 deg")])


def _mm(value: float) -> str:
    return f"{abs(value) * 1000:.6g} mm"


class SketchWriter:
    """Entities and the constraints that fully define them (DOF 0)."""

    def __init__(self, expressions: dict[tuple[str, int], str] | None = None) -> None:
        self.entities: list[dict[str, Any]] = []
        self.constraints: list[dict[str, Any]] = []
        self.expressions = expressions or {}  # (axis 'x'|'y', rounded um) -> expression override
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def _coordinate(self, axis: str, value: float) -> str:
        return self.expressions.get((axis, int(round(value * 1e6))), _mm(value))

    def fix_point(self, point_id: str, p: np.ndarray) -> None:
        """Locate a sketch point from the origin: horizontal and vertical dimensions
        (an alignment constraint when a coordinate is zero, coincident at the origin)."""
        x, y = float(p[0]), float(p[1])
        if abs(x) < 1e-9 and abs(y) < 1e-9:
            self.constraints.append(_constraint(self._id("k"), "COINCIDENT", [_s("localFirst", point_id), _origin()]))
            return
        for axis, value, direction, align in (("x", x, "HORIZONTAL", "VERTICAL"), ("y", y, "VERTICAL", "HORIZONTAL")):
            if abs(value) < 1e-9:
                self.constraints.append(_constraint(self._id("k"), align, [_s("localFirst", point_id), _origin()]))
            else:
                self.constraints.append(_dim(self._id("d"), point_id, _origin(), direction, self._coordinate(axis, value)))

    def line(self, p0: np.ndarray, p1: np.ndarray, construction: bool = False) -> str:
        eid = self._id("l")
        d = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
        length = float(np.linalg.norm(d))
        d = d / length
        entity = {"btType": "BTMSketchCurveSegment-155", "entityId": eid, "startPointId": f"{eid}.start", "endPointId": f"{eid}.end",
                  "startParam": 0.0, "endParam": length, "isConstruction": construction,
                  "geometry": {"btType": "BTCurveGeometryLine-117", "pntX": float(p0[0]), "pntY": float(p0[1]),
                               "dirX": float(d[0]), "dirY": float(d[1])}}
        self.entities.append(entity)
        return eid

    def arc(self, center: np.ndarray, radius: float, a0: float, a1: float) -> str:
        eid = self._id("a")
        if a1 < a0:
            a0, a1 = a1, a0
        self.entities.append({"btType": "BTMSketchCurveSegment-155", "entityId": eid, "startPointId": f"{eid}.start",
                              "endPointId": f"{eid}.end", "centerId": f"{eid}.center", "startParam": a0, "endParam": a1,
                              "geometry": {"btType": "BTCurveGeometryCircle-115", "radius": radius, "xCenter": float(center[0]),
                                           "yCenter": float(center[1]), "xDir": 1.0, "yDir": 0.0, "clockwise": False}})
        return eid

    def coincident(self, a: str, b: str) -> None:
        self.constraints.append(_constraint(self._id("k"), "COINCIDENT", [_s("localFirst", a), _s("localSecond", b)]))

    # -------------------------------------------------------------- shapes
    def circle(self, center: Iterable[float], radius: float) -> None:
        eid = self._id("c")
        c = np.asarray(list(center), dtype=float)
        self.entities.append({"btType": "BTMSketchCurve-4", "entityId": eid, "centerId": f"{eid}.center",
                              "geometry": {"btType": "BTCurveGeometryCircle-115", "radius": radius, "xCenter": float(c[0]),
                                           "yCenter": float(c[1]), "xDir": 1.0, "yDir": 0.0, "clockwise": False}})
        self.fix_point(f"{eid}.center", c)
        self.constraints.append(_constraint(self._id("d"), "DIAMETER", [_s("localFirst", eid), fx.qty("length", _mm(2 * radius)),
                                                                       fx.qty("labelAngle", "45 deg"), fx.qty("labelRatio", "0.5")]))

    def point(self, p: Iterable[float]) -> None:
        eid = self._id("p")
        q = np.asarray(list(p), dtype=float)
        self.entities.append({"btType": "BTMSketchPoint-158", "entityId": eid, "x": float(q[0]), "y": float(q[1]), "isUserPoint": True})
        self.fix_point(eid, q)

    def loop(self, loop: Loop) -> None:
        if loop.circle_center is not None:
            self.circle(loop.circle_center, loop.circle_radius)
            return
        segments = list(loop.segments)
        ids: list[tuple[str, Segment]] = []
        for seg in segments:
            if seg.kind == "line":
                ids.append((self.line(np.asarray(seg.p0), np.asarray(seg.p1)), seg))
            else:
                ids.append((self.arc(np.asarray(seg.center), seg.radius, seg.a0, seg.a1), seg))
        ends = []
        for eid, seg in ids:
            start, end = (seg.start, seg.end) if seg.kind == "line" or seg.a1 >= seg.a0 else (seg.end, seg.start)
            ends += [(f"{eid}.start", np.asarray(start)), (f"{eid}.end", np.asarray(end))]
        used: set[str] = set()
        for i, (na, pa) in enumerate(ends):
            for nb, pb in ends[i + 1:]:
                if na.split(".")[0] == nb.split(".")[0] or na in used or nb in used:
                    continue
                if float(np.linalg.norm(pa - pb)) < 1e-9:
                    self.coincident(na, nb)
                    used.update((na, nb))
        arcs = [(eid, seg) for eid, seg in ids if seg.kind == "arc"]
        lines = [(eid, seg) for eid, seg in ids if seg.kind == "line"]
        if not arcs:
            # Polygon: every vertex located from the origin (2 x N dimensions for 2 x N degrees of freedom).
            for eid, seg in lines:
                self.fix_point(f"{eid}.start", np.asarray(seg.p0))
            return
        for eid, seg in arcs:
            self.fix_point(f"{eid}.center", np.asarray(seg.center))
            self.constraints.append(_constraint(self._id("d"), "RADIUS", [_s("localFirst", eid), fx.qty("length", _mm(seg.radius)),
                                                                         fx.qty("labelAngle", "45 deg"), fx.qty("labelRatio", "0.5")]))
        if len(arcs) == 1 and len(lines) == 1:
            line_id, line = lines[0]
            arc_id, arc = arcs[0]
            direction = np.asarray(line.p1) - np.asarray(line.p0)
            center = np.asarray(arc.center)
            rel = center - np.asarray(line.p0)
            cross = float(direction[0] * rel[1] - direction[1] * rel[0])
            on_center = abs(cross) < 1e-9 * float(np.linalg.norm(direction))
            if abs(direction[0]) < 1e-9 or abs(direction[1]) < 1e-9:
                self.constraints.append(_constraint(self._id("k"), "VERTICAL" if abs(direction[0]) < 1e-9 else "HORIZONTAL",
                                                    [_s("localFirst", line_id)]))
            if on_center:  # half disc (sphere profile): the diameter passes through the center
                self.constraints.append(_constraint(self._id("k"), "COINCIDENT", [_s("localFirst", f"{arc_id}.center"),
                                                                                 _s("localSecond", line_id)]))
            else:  # D shape: flat at its offset from the center
                offset = abs(cross) / float(np.linalg.norm(direction))
                self.constraints.append(_dim(self._id("d"), f"{arc_id}.center", line_id, "MINIMUM", _mm(offset)))
            return
        for eid, _ in lines:  # obround slot: lines tangent to both arcs
            for aid, _ in arcs:
                self.constraints.append(_constraint(self._id("k"), "TANGENT", [_s("localFirst", eid), _s("localSecond", aid)]))

    def feature(self, name: str, plane: str) -> dict[str, Any]:
        return {"btType": "BTMSketch-151", "featureType": "newSketch", "name": name,
                "parameters": [fx.qlist("sketchPlane", [fx.det(PLANE_IDS[plane])])], "entities": self.entities,
                "constraints": self.constraints}


# ------------------------------------------------------------------ mate connectors
def connector_transform(frame: np.ndarray) -> dict[str, Any]:
    """Mate-connector parameters (flipPrimary, secondaryAxisType, translation,
    single-axis rotation) that put a connector on the part origin at ``frame``.
    Onshape composes them as F = Rx(flip) · Rz(secondary) · T(t) · R_axis(theta)
    (verified against the connector frames Onshape reports)."""
    target = frame[:3, :3]
    for flip in (False, True):
        for secondary, angle in SECONDARY.items():
            c, s = math.cos(angle), math.sin(angle)
            base = (np.diag([1.0, -1.0, -1.0]) if flip else np.eye(3)) @ np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
            rest = base.T @ target
            for axis, (i, j, k) in (("ABOUT_Z", (2, 0, 1)), ("ABOUT_X", (0, 1, 2)), ("ABOUT_Y", (1, 2, 0))):
                if abs(rest[i, i] - 1.0) > 1e-9:
                    continue
                theta = math.atan2(rest[k, j], rest[j, j])
                cc, ss = math.cos(theta), math.sin(theta)
                check = np.eye(3)
                check[j, j], check[j, k], check[k, j], check[k, k] = cc, -ss, ss, cc
                if np.abs(check - rest).max() < 1e-9:
                    t = base.T @ frame[:3, 3]
                    return {"flip": flip, "secondary": secondary, "axis": axis, "rotation": theta, "translation": t}
    raise ValueError(f"no mate-connector transform for frame {frame.tolist()}")


def connector_frame(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """The connector frame actually built and its residual clock: a frame whose x is
    not axis-aligned after its z is set (e.g. a pinion flat clocked 40 deg) gets the
    nearest axis-aligned x; mates add the residual rotation about z."""
    try:
        connector_transform(frame)
        return frame, 0.0
    except ValueError:
        pass
    z = frame[:3, 2]
    best: tuple[float, np.ndarray] | None = None
    for candidate in np.vstack([np.eye(3), -np.eye(3)]):
        if abs(float(candidate @ z)) > 1e-9:
            continue
        built = np.eye(4)
        built[:3, :3] = np.column_stack([candidate, np.cross(z, candidate), z])
        built[:3, 3] = frame[:3, 3]
        x = frame[:3, 0]
        residual = math.atan2(float(np.cross(candidate, x) @ z), float(candidate @ x))
        if best is None or abs(residual) < abs(best[0]):
            best = (residual, built)
    assert best is not None
    return best[1], best[0]


def mate_connector(name: str, frame: np.ndarray, owner_part_id: str) -> dict[str, Any]:
    t = connector_transform(connector_frame(frame)[0])
    params = [fx.enum("originType", "OriginCreationType", "ON_ENTITY"), fx.qlist("originQuery", [fx.det(ORIGIN_ID)]),
              fx.enum("entityInferenceType", "EntityInferenceType", "POINT"),
              fx.boolean("flipPrimary", t["flip"]), fx.enum("secondaryAxisType", "MateConnectorAxisType", t["secondary"]),
              fx.boolean("transform", True), fx.enum("rotationType", "RotationType", t["axis"]), fx.qty("rotation", fx.deg(t["rotation"])),
              fx.qty("translationX", fx.mm(t["translation"][0])), fx.qty("translationY", fx.mm(t["translation"][1])),
              fx.qty("translationZ", fx.mm(t["translation"][2])),
              fx.boolean("requireOwnerPart", True), fx.qlist("ownerPart", [fx.det(owner_part_id)])]
    return fx.feature("mateConnector", name, params)


# ------------------------------------------------------------------ builder
def _plane_name(plane: Plane) -> str:
    return {"TOP": "Top", "FRONT": "Front", "RIGHT": "Right"}[plane.base]


class PartStudioBuilder:
    """Builds one family's parts into one Part Studio."""

    def __init__(self, api: Transport, did: str, wid: str, eid: str, name: str,
                 variables: dict[str, Variable] | None = None, bindings: dict[str, Any] | None = None) -> None:
        self.api, self.did, self.wid, self.eid = api, did, wid, eid
        self.base = f"/partstudios/d/{did}/w/{wid}/e/{eid}"
        self.out = BuiltStudio(element_id=eid, name=name)
        self.variables = variables or {}
        self.bindings = bindings or {}  # {"extrude": {op name: var}, "points": {interface: {"x": expr-var, ...}}}
        self.known: set[str] = set()

    # -------------------------------------------------------------- REST
    def _add(self, feature: dict[str, Any], part: str, folder: str) -> str:
        response = self.api.json("POST", self.base + "/features", body={"btType": "BTFeatureDefinitionCall-1406", "feature": feature})
        self.out.features += 1
        state = (response.get("featureState") or {}).get("featureStatus", "OK")
        fid = response["feature"]["featureId"]
        if state == "ERROR":
            raise StudioBuildError(part, feature.get("name", ""), f"(regeneration error, feature {fid})")
        if state == "WARNING":
            self.out.warnings.append(f"{part}: {feature.get('name', '')}")
        self.out.folders.setdefault(folder, []).append(fid)
        return fid

    def _parts(self) -> dict[str, str]:
        return {p["partId"]: p.get("name", "") for p in self.api.json("GET", f"/parts/d/{self.did}/w/{self.wid}/e/{self.eid}")}

    def _new_part(self, before: set[str], label: str) -> str:
        after = self._parts()
        new = [pid for pid in after if pid not in before]
        if len(new) != 1:
            raise StudioBuildError(label, "first feature", f"(created {len(new)} bodies)")
        return new[0]

    # -------------------------------------------------------------- operations
    def _expressions(self, iface_name: str) -> dict[tuple[str, int], str]:
        return self.bindings.get("points", {}).get(iface_name, {})

    def _prism(self, part: LocalPart, op: Prism, mode: str, scope: str | None, index: int) -> None:
        writer = SketchWriter()
        for loop in op.loops:
            writer.loop(loop)
        label = op.name or f"profile {index}"
        sketch = self._add(writer.feature(f"{label} sketch", op.plane.base), part.name, op.folder or "Base shape")
        depth_var = self.bindings.get("extrude", {}).get(op.name)
        depth = None if op.depth is None else (f"#{depth_var}" if depth_var else fx.mm(op.depth))
        title = f"{label} extrude {op.depth * 1e3:g} mm" if op.depth is not None else f"{label} cut through all"
        params = [fx.enum("bodyType", "ExtendedToolBodyType", "SOLID"), fx.qlist("entities", [fx.region(sketch)]),
                  fx.boolean("oppositeDirection", op.direction < 0)]
        params += [fx.enum("operationType", "NewBodyOperationType", {"new": "NEW", "add": "ADD", "remove": "REMOVE"}[mode])]
        if mode != "new" and scope:
            params += [fx.boolean("defaultScope", False), fx.qlist("booleanScope", [fx.det(scope)])]
        if depth is None:
            params.append(fx.enum("endBound", "BoundingType", "THROUGH_ALL"))
        else:
            params += [fx.enum("endBound", "BoundingType", "BLIND"), fx.qty("depth", depth)]
        if abs(op.plane.offset) > 1e-12 and op.depth is not None:
            # The profile starts op.plane.offset along the plane normal. Onshape measures the start offset along
            # the sketch-plane normal (flipped by startOffsetOppositeDirection), whatever the extrude direction.
            params += [fx.boolean("startOffset", True), fx.enum("startOffsetBound", "StartOffsetType", "BLIND"),
                       fx.qty("startOffsetDistance", fx.mm(abs(op.plane.offset))),
                       fx.boolean("startOffsetOppositeDirection", op.plane.offset < 0)]
        elif abs(op.plane.offset) > 1e-12:
            # Through-all cut whose profile plane is offset: cut both ways from the standard plane.
            params += [fx.boolean("hasSecondDirection", True), fx.enum("secondDirectionBound", "BoundingType", "THROUGH_ALL")]
        self._add(fx.feature("extrude", title, params), part.name, op.folder or "Base shape")

    def _revolve(self, part: LocalPart, op: Revolve, mode: str, scope: str | None, index: int) -> None:
        axis = unit(op.axis)
        candidates = [op.radial_dir(), *np.eye(3)]
        plane, radial = None, None
        for candidate in candidates:  # the standard plane through the origin that contains the axis
            r = candidate - (candidate @ axis) * axis
            if np.linalg.norm(r) < 1e-9:
                continue
            r = unit(r)
            try:
                trial = Plane.containing(op.point, axis, r)
            except ValueError:
                continue
            if abs(trial.offset) < 1e-9:
                plane, radial = trial, r
                break
        if plane is None:
            raise StudioBuildError(part.name, op.name, "(revolve axis is not in a standard plane through the origin)")
        matrix = np.column_stack([plane.to2d(radial), plane.to2d(axis)])
        loop = op.loop_rt().transformed(matrix, plane.to2d(op.point))
        label = op.name or f"revolve {index}"
        writer = SketchWriter()
        writer.loop(loop)
        sketch = self._add(writer.feature(f"{label} profile sketch", plane.base), part.name, op.folder or "Base shape")
        axis_writer = SketchWriter()
        start, end = plane.to2d(op.point), plane.to2d(np.asarray(op.point) + axis * 0.01)
        line = axis_writer.line(start, end)
        axis_writer.fix_point(f"{line}.start", start)
        axis_writer.fix_point(f"{line}.end", end)
        axis_sketch = self._add(axis_writer.feature(f"{label} axis sketch", plane.base), part.name, op.folder or "Base shape")
        params = [fx.enum("bodyType", "ExtendedToolBodyType", "SOLID"), fx.qlist("entities", [fx.region(sketch)]),
                  fx.qlist("axis", [fx.created_by(axis_sketch, "EDGE", "WIRE")]), fx.boolean("fullRevolve", True),
                  fx.enum("operationType", "NewBodyOperationType", {"new": "NEW", "add": "ADD", "remove": "REMOVE"}[mode])]
        if mode != "new" and scope:
            params += [fx.boolean("defaultScope", False), fx.qlist("booleanScope", [fx.det(scope)])]
        self._add(fx.feature("revolve", f"{label} revolve", params), part.name, op.folder or "Base shape")

    def _start_face(self, part_id: str, normal: np.ndarray, offset: float) -> str:
        """Deterministic id of the part's planar face with outward ``normal`` at ``offset`` (point · normal)."""
        details = self.api.json("GET", f"/parts/d/{self.did}/w/{self.wid}/e/{self.eid}/partid/{urllib.parse.quote(part_id, safe='')}/bodydetails")
        for body in details.get("bodies", []):
            for face in body.get("faces", []):
                surface = face.get("surface") or {}
                if str(surface.get("type", "")).upper() != "PLANE":
                    continue
                n = np.array([surface["normal"][k] for k in "xyz"]) * (1.0 if face.get("orientation", True) else -1.0)
                o = np.array([surface["origin"][k] for k in "xyz"])
                if float(n @ normal) > 1 - 1e-9 and abs(float(o @ normal) - offset) < 1e-7:
                    return face["id"]
        raise StudioBuildError(part_id, "hole start face", f"(no planar face with normal {normal.tolist()} at {offset * 1e3:.3f} mm)")

    def _holes(self, part: LocalPart, part_id: str, group: list[Hole]) -> None:
        first = group[0]
        axis = unit(first.axis)
        entry = Plane.through(first.point, axis)
        writer = SketchWriter(self._expressions(first.name.split(":")[0]))
        for hole in group:
            writer.point(entry.to2d(hole.point))
        label = first.name or "holes"
        sketch = self._add(writer.feature(f"{label} points sketch", entry.base), part.name, "Mounting holes")
        opposite = bool(axis @ entry.normal > 0)  # Hole drills against the plane normal by default
        hole = fx.hole(label[:120], sketch, first.style, first.diameter, first.depth, opposite, [fx.det(part_id)], first.thread,
                       first.tapped_depth, 0.0, first.fit)
        if abs(entry.offset) > 1e-12:
            face = self._start_face(part_id, -axis, float(np.asarray(first.point) @ -axis))
            for p in hole["parameters"]:
                if p.get("parameterId") == "startStyle":
                    p["value"] = "PLANE"
            hole["parameters"].append(fx.qlist("startBoundEntity", [fx.det(face)]))
        self._add(hole, part.name, "Mounting holes")

    PASSES = ("Base shape", "Bores & shafts", "Cutouts", "Mounting holes")

    def start_part(self, part: LocalPart) -> str:
        """Create the part's body with its first operation, then its other base-shape operations."""
        ops = list(part.plan.ops)
        before = set(self._parts())
        first = ops[0]
        if isinstance(first, Prism):
            self._prism(part, first, "new", None, 0)
        elif isinstance(first, Revolve):
            self._revolve(part, first, "new", None, 0)
        else:
            raise StudioBuildError(part.name, "first", "(must add material)")
        part_id = self._new_part(before, part.name)
        self.out.part_ids[part.key] = part_id
        self.apply(part, "Base shape", skip_first=True)
        return part_id

    def apply(self, part: LocalPart, folder: str, skip_first: bool = False) -> None:
        """Apply the part's operations of one feature folder, in plan order (holes grouped per sketch)."""
        part_id = self.out.part_ids[part.key]
        ops = list(enumerate(part.plan.ops))[1 if skip_first else 0:]
        holes: dict[tuple, list[Hole]] = {}
        for index, op in ops:
            if (op.folder or ("Mounting holes" if isinstance(op, Hole) else "Base shape")) != folder:
                continue
            if isinstance(op, Hole):
                axis = unit(op.axis)
                key = (Plane.through(op.point, axis), tuple(np.round(axis, 9)), op.style, round(op.diameter, 9),
                       op.depth and round(op.depth, 9), op.thread, op.tapped_depth and round(op.tapped_depth, 9), op.name)
                holes.setdefault(key, []).append(op)
            elif isinstance(op, Prism):
                self._prism(part, op, op.op, part_id, index)
            else:
                self._revolve(part, op, op.op, part_id, index)
        for group in holes.values():
            self._holes(part, part_id, group)

    def build_part(self, part: LocalPart) -> str:
        part_id = self.start_part(part)
        for folder in self.PASSES[1:]:
            self.apply(part, folder)
        return part_id

    def mirror_part(self, part: LocalPart, source_key: str) -> str:
        """Right-hand part: a Mirror feature of the left part about the Front (XZ) plane."""
        before = set(self._parts())
        source = self.out.part_ids[source_key]
        params = [fx.enum("patternType", "MirrorType", "PART"), fx.qlist("entities", [fx.det(source)]),
                  fx.qlist("mirrorPlane", [fx.det(PLANE_IDS["FRONT"])]), fx.enum("operationType", "NewBodyOperationType", "NEW")]
        self._add(fx.feature("mirror", f"{part.name} (mirror of {source_key.split('#')[0]} about Front)", params), part.name, "Mirror")
        part_id = self._new_part(before, part.name)
        self.out.part_ids[part.key] = part_id
        return part_id

    def connectors(self, part: LocalPart) -> None:
        pid = self.out.part_ids[part.key]
        names: dict[str, str] = {}
        suffix = f" ({part.name})" if any(p for p in self.out.part_ids if p != part.key) else ""
        for spec in part.connectors:
            fid = self._add(mate_connector(f"{spec.name}{suffix}", spec.frame, pid), part.name, "Mate connectors")
            names[spec.name] = fid
        self.out.connectors[part.key] = names

    def properties(self, part: LocalPart, metadata_ids: dict[str, str]) -> None:
        pid = self.out.part_ids[part.key]
        plan = part.plan
        color = plan.color
        props: list[dict[str, Any]] = [
            {"propertyId": metadata_ids["Name"], "value": part.name},
            {"propertyId": metadata_ids["Appearance"], "value": {"color": {"red": int(color[1:3], 16), "green": int(color[3:5], 16),
                                                                         "blue": int(color[5:7], 16)}, "opacity": 255}},
        ]
        for label, key in (("Part number", "part_number"), ("Vendor", "vendor"), ("Description", "mass_source")):
            if label in metadata_ids and part.properties.get(key):
                text = part.properties[key] if label != "Description" else f"{part.properties.get('catalog_type', '')}: mass from {part.properties[key]}"
                props.append({"propertyId": metadata_ids[label], "value": text})
        if plan.mass is not None:
            props.append({"propertyId": metadata_ids["Mass"], "value": f"{plan.mass:.6g} kg"})
        if plan.density:
            material = {"id": plan.material, "displayName": plan.material, "libraryName": "R3 materials",
                        "properties": [{"name": "DENS", "displayName": "Density", "type": "REAL", "category": "Physical",
                                        "value": plan.density, "units": "kg/m^3", "description": "Density"}]}
            props.append({"propertyId": metadata_ids["Material"], "value": material})
        self.api.json("POST", f"/metadata/d/{self.did}/w/{self.wid}/e/{self.eid}/p/{urllib.parse.quote(pid, safe='')}",
                      body={"jsonType": "metadata-part", "partId": pid, "properties": props})


def metadata_property_ids(api: Transport, did: str, wid: str, eid: str, part_id: str) -> dict[str, str]:
    """Property name -> id from a part's metadata (Name, Part number, Vendor, Material, Mass, ...)."""
    data = api.json("GET", f"/metadata/d/{did}/w/{wid}/e/{eid}/p/{urllib.parse.quote(part_id, safe='')}")
    out = {p.get("name"): p.get("propertyId") for p in data.get("properties", []) if p.get("name")}
    from .direct import METADATA

    out.setdefault("Name", METADATA["name"])
    out.setdefault("Appearance", METADATA["appearance"])
    out.setdefault("Material", METADATA["material"])
    out.setdefault("Mass", METADATA["mass"])
    out.setdefault("Part number", METADATA["part_number"])
    out.setdefault("Description", METADATA["description"])
    return out


def build_studio(api: Transport, did: str, wid: str, eid: str, name: str, parts: list[LocalPart],
                 variables: dict[str, Variable] | None = None, bindings: dict[str, Any] | None = None) -> BuiltStudio:
    builder = PartStudioBuilder(api, did, wid, eid, name, variables, bindings)
    ordered = sorted(parts, key=lambda p: (p.mirror_of is not None, p.name))
    mirrors = [p for p in ordered if p.mirror_of and any(q.key == p.mirror_of for q in ordered)]
    modeled = [p for p in ordered if p not in mirrors]
    # Category passes keep each feature folder contiguous: all base shapes, then bores, cutouts, holes, mirrors.
    for part in modeled:
        builder.start_part(part)
    for folder in PartStudioBuilder.PASSES[1:]:
        for part in modeled:
            builder.apply(part, folder)
    for part in mirrors:
        builder.mirror_part(part, part.mirror_of)
    meta_ids = metadata_property_ids(api, did, wid, eid, next(iter(builder.out.part_ids.values())))
    for part in ordered:
        builder.properties(part, meta_ids)
        builder.connectors(part)
    return builder.out


__all__ = ["BuiltStudio", "PartStudioBuilder", "SketchWriter", "StudioBuildError", "Variable", "build_studio", "connector_transform",
           "mate_connector", "metadata_property_ids", "OnshapeError", "ConnectorSpec"]
