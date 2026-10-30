"""JSON for Onshape's standard features (sketch, cPlane, extrude, revolve, hole,
mate connector, mate). Nothing here references FeatureScript: queries are
deterministic ids, sketch-region / created-by queries and occurrence paths."""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

from .geometry import PLANE_IDS, Loop, Plane, Segment


def qty(pid: str, expression: str) -> dict[str, Any]:
    return {"btType": "BTMParameterQuantity-147", "parameterId": pid, "expression": expression}


def nullable_qty(pid: str, expression: str) -> dict[str, Any]:
    return {"btType": "BTMParameterNullableQuantity-807", "parameterId": pid, "isNull": False, "expression": expression}


def mm(value: float) -> str:
    return f"{value * 1000:.6f} mm"


def deg(value_rad: float) -> str:
    return f"{math.degrees(value_rad):.6f} deg"


def enum(pid: str, enum_name: str, value: str) -> dict[str, Any]:
    return {"btType": "BTMParameterEnum-145", "parameterId": pid, "enumName": enum_name, "value": value}


def boolean(pid: str, value: bool) -> dict[str, Any]:
    return {"btType": "BTMParameterBoolean-144", "parameterId": pid, "value": value}


def lookup(pid: str, value: dict[str, str]) -> dict[str, Any]:
    return {"btType": "BTMParameterLookupTablePath-1419", "parameterId": pid, "value": value}


def qlist(pid: str, queries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"btType": "BTMParameterQueryList-148", "parameterId": pid, "queries": queries}


def det(*ids: str) -> dict[str, Any]:
    return {"btType": "BTMIndividualQuery-138", "deterministicIds": list(ids)}


def created_by(feature_id: str, entity: str, body: str) -> dict[str, Any]:
    return {"btType": "BTMIndividualCreatedByQuery-137", "featureId": feature_id, "entityType": entity, "bodyType": body}


def region(sketch_id: str) -> dict[str, Any]:
    return {"btType": "BTMIndividualSketchRegionQuery-140", "featureId": sketch_id, "filterInnerLoops": True}


def feature(feature_type: str, name: str, params: list[dict[str, Any]]) -> dict[str, Any]:
    return {"btType": "BTMFeature-134", "featureType": feature_type, "name": name, "namespace": "", "parameters": params}


def translate(name: str, part_id: str, delta: Iterable[float]) -> dict[str, Any]:
    """Standard Transform -> Translate by XYZ, retaining its native dialog defaults.

    Onshape regenerates the entire feature definition, including inactive
    parameter branches. Omitting those defaults can create an ERROR feature.
    Values follow the native Transform /featurespecs metadata, not part data.
    """
    dx, dy, dz = delta
    params = [qlist("entities", [det(part_id)]), enum("transformType", "TransformType", "TRANSLATION_3D"),
              boolean("oppositeDirectionEntity", False),
              *[qlist(key, []) for key in ("transformLine", "transformAxis", "transformDirection")],
              qty("distance", "25 mm"), boolean("uniform", True),
              *[qty(key, "1") for key in ("scale", "scaleX", "scaleY", "scaleZ")],
              qlist("baseConnector", []), qlist("destinationConnector", []),
              boolean("oppositeDirectionMateAxis", False), enum("secondaryAxisType", "MateConnectorAxisType", "PLUS_X"),
              qty("angle", "30 deg"), boolean("oppositeDirection", False),
              qty("dx", mm(dx)), qty("dy", mm(dy)), qty("dz", mm(dz)),
              qlist("scalePoint", []), boolean("makeCopy", False)]
    return feature("transform", name, params)


# --------------------------------------------------------------------- sketches
def _segment_entity(eid: str, seg: Segment) -> tuple[dict[str, Any], tuple[np.ndarray, np.ndarray]]:
    if seg.kind == "line":
        p0, p1 = np.asarray(seg.p0, dtype=float), np.asarray(seg.p1, dtype=float)
        length = float(np.linalg.norm(p1 - p0))
        d = (p1 - p0) / length
        entity = {"btType": "BTMSketchCurveSegment-155", "entityId": eid, "startPointId": f"{eid}.start", "endPointId": f"{eid}.end",
                  "startParam": 0.0, "endParam": length,
                  "geometry": {"btType": "BTCurveGeometryLine-117", "pntX": float(p0[0]), "pntY": float(p0[1]),
                               "dirX": float(d[0]), "dirY": float(d[1])}}
        return entity, (p0, p1)
    a0, a1 = (seg.a0, seg.a1) if seg.a1 >= seg.a0 else (seg.a1, seg.a0)  # Onshape arcs run counter-clockwise
    c = np.asarray(seg.center, dtype=float)
    entity = {"btType": "BTMSketchCurveSegment-155", "entityId": eid, "startPointId": f"{eid}.start", "endPointId": f"{eid}.end",
              "centerId": f"{eid}.center", "startParam": a0, "endParam": a1,
              "geometry": {"btType": "BTCurveGeometryCircle-115", "radius": seg.radius, "xCenter": float(c[0]), "yCenter": float(c[1]),
                           "xDir": 1.0, "yDir": 0.0, "clockwise": False}}
    ends = (c + seg.radius * np.array([math.cos(a0), math.sin(a0)]), c + seg.radius * np.array([math.cos(a1), math.sin(a1)]))
    return entity, ends


def _coincident(cid: str, first: str, second: str) -> dict[str, Any]:
    return {"btType": "BTMSketchConstraint-2", "constraintType": "COINCIDENT", "entityId": cid, "parameters": [
        {"btType": "BTMParameterString-149", "value": first, "parameterId": "localFirst"},
        {"btType": "BTMParameterString-149", "value": second, "parameterId": "localSecond"}]}


def sketch_entities(loops: Iterable[Loop], points: Iterable[np.ndarray] = ()) -> tuple[list, list]:
    """Sketch entities for closed loops (endpoints joined by coincident
    constraints, matched geometrically) and standalone points (hole centers)."""
    entities: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    for li, loop in enumerate(loops):
        if loop.circle_center is not None:
            entities.append({"btType": "BTMSketchCurve-4", "entityId": f"l{li}c", "centerId": f"l{li}c.center",
                             "geometry": {"btType": "BTCurveGeometryCircle-115", "radius": loop.circle_radius,
                                          "xCenter": loop.circle_center[0], "yCenter": loop.circle_center[1],
                                          "xDir": 1.0, "yDir": 0.0, "clockwise": False}})
            continue
        ends: list[tuple[str, np.ndarray]] = []
        for si, seg in enumerate(loop.segments):
            eid = f"l{li}s{si}"
            entity, (start, end) = _segment_entity(eid, seg)
            entities.append(entity)
            ends += [(f"{eid}.start", start), (f"{eid}.end", end)]
        used: set[str] = set()
        for i, (name_a, pa) in enumerate(ends):
            for name_b, pb in ends[i + 1:]:
                if name_a.rsplit(".", 1)[0] == name_b.rsplit(".", 1)[0] or name_a in used or name_b in used:
                    continue
                if float(np.linalg.norm(pa - pb)) < 1e-9:
                    constraints.append(_coincident(f"l{li}k{len(constraints)}", name_a, name_b))
                    used.update((name_a, name_b))
    for pi, p in enumerate(points):
        entities.append({"btType": "BTMSketchPoint-158", "entityId": f"p{pi}", "x": float(p[0]), "y": float(p[1]), "isUserPoint": True})
    return entities, constraints


def sketch(name: str, plane_query: dict[str, Any], loops: Iterable[Loop] = (), points: Iterable[np.ndarray] = ()) -> dict[str, Any]:
    entities, constraints = sketch_entities(loops, points)
    return {"btType": "BTMSketch-151", "featureType": "newSketch", "name": name, "parameters": [qlist("sketchPlane", [plane_query])],
            "entities": entities, "constraints": constraints}


def default_plane_query(plane: Plane) -> dict[str, Any]:
    return det(PLANE_IDS[plane.base])


def cplane(name: str, plane: Plane) -> dict[str, Any]:
    # Offsets are entered as a positive distance plus a direction flip, as in the UI.
    return feature("cPlane", name, [qlist("entities", [default_plane_query(plane)]), enum("cplaneType", "CPlaneType", "OFFSET"),
                                    qty("offset", mm(abs(plane.offset))), boolean("oppositeDirection", plane.offset < 0)])


# --------------------------------------------------------------------- solids
def _boolean(op: str, scope: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    params = [enum("operationType", "NewBodyOperationType", {"new": "NEW", "add": "ADD", "remove": "REMOVE"}[op])]
    if op != "new" and scope is not None:
        params += [boolean("defaultScope", False), qlist("booleanScope", scope)]
    return params


def extrude(name: str, sketch_id: str, depth: float | None, opposite: bool, op: str, scope: list | None) -> dict[str, Any]:
    params = [enum("bodyType", "ExtendedToolBodyType", "SOLID"), qlist("entities", [region(sketch_id)]),
              boolean("oppositeDirection", opposite), *_boolean(op, scope)]
    if depth is None:
        params.append(enum("endBound", "BoundingType", "THROUGH_ALL"))
    else:
        params += [enum("endBound", "BoundingType", "BLIND"), qty("depth", mm(depth))]
    return feature("extrude", name, params)


def revolve(name: str, sketch_id: str, axis_sketch_id: str, op: str, scope: list | None) -> dict[str, Any]:
    params = [enum("bodyType", "ExtendedToolBodyType", "SOLID"), qlist("entities", [region(sketch_id)]),
              qlist("axis", [created_by(axis_sketch_id, "EDGE", "WIRE")]), boolean("fullRevolve", True), *_boolean(op, scope)]
    return feature("revolve", name, params)


ISO_PITCH_LABEL = {"M2": "0.40 mm", "M2.5": "0.45 mm", "M3": "0.50 mm", "M4": "0.70 mm"}
ISO_MAJOR = {"M2": 0.002, "M2.5": 0.0025, "M3": 0.003, "M4": 0.004}


def hole(name: str, points_sketch_id: str, style: str, diameter: float, depth: float | None, opposite: bool, scope: list,
         thread: str = "", tapped_depth: float | None = None, major: float = 0.0, fit: str = "Normal") -> dict[str, Any]:
    """Onshape Hole feature started at its sketch plane. ``style`` tapped uses
    the ISO tapped table (75 % engagement tap drill), clearance the ISO 273
    table, drilled a custom diameter."""
    if style == "tapped":
        table = {"standard": "ISO", "type": "Tapped", "size": thread, "pitch": ISO_PITCH_LABEL[thread], "engagement": "75%"}
    elif style == "clearance":
        table = {"standard": "ISO", "type": "Clearance", "size": thread, "fit": fit}
    else:
        table = {"standard": "Custom"}
    end = "THROUGH" if depth is None else "BLIND"
    params = [enum("style", "HoleStyle", "SIMPLE"), enum("styleV2", "HoleStyle", "SIMPLE"), enum("unitsSystem", "UnitsSystem", "METRIC"),
              enum("startStyle", "HoleStartStyle", "SKETCH"), lookup("standardTappedOrClearance", table), lookup("standardBlindInLast", table),
              enum("endStyle", "HoleEndStyle", end), enum("endStyleV2", "HoleEndStyleV2", end), boolean("oppositeDirection", opposite),
              qty("holeDiameter", mm(diameter)), qty("holeDiameterV2", mm(diameter)), qty("holeDiameterV3", mm(diameter)),
              qty("tapDrillDiameter", mm(diameter)), qty("tapDrillDiameterV2", mm(diameter)), qty("tapDrillDiameterV3", mm(diameter)),
              qlist("locations", [{"btType": "BTMIndividualSketchUniqueVerticesQuery-1472", "featureId": points_sketch_id}]),
              qlist("scope", scope)]
    major = major or ISO_MAJOR.get(thread, 0.0) if style == "tapped" else major
    if major:
        params.append(qty("majorDiameter", mm(major)))
    if depth is not None:
        params += [qty("holeDepth", mm(depth)), qty("holeDepthV3", mm(depth))]
    if style == "tapped":
        tapped = tapped_depth if tapped_depth is not None else depth
        # showTappedDepth=true regenerates with a warning on blind holes; the thread data is kept without it.
        params.append(boolean("isTappedThrough", depth is None))
        if tapped is not None:
            params += [qty("tappedDepth", mm(tapped)), qty("tappedDepthV3", mm(tapped))]
    return feature("hole", name, params)


# ------------------------------------------------------------------ assemblies
def mate_connector(feature_id: str, occurrence: str, entity_id: str, inference: str, flip: bool = False, rotation: float = 0.0,
                   translation: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> dict[str, Any]:
    """Implicit mate connector on a part face/edge of an assembly occurrence.
    Transform order (verified live): flip primary axis, rotate about the new
    z, then translate in that frame."""
    params: list[dict[str, Any]] = [
        enum("originType", "OriginCreationType", "ON_ENTITY"),
        {"btType": "BTMParameterQueryWithOccurrenceList-67", "parameterId": "originQuery", "queries": [
            {"btType": "BTMInferenceQueryWithOccurrence-1083", "path": [occurrence], "deterministicIds": [entity_id],
             "inferenceType": inference}]},
    ]
    if flip:
        params.append(boolean("flipPrimary", True))
    if rotation or any(translation):
        params += [boolean("transform", True), enum("rotationType", "RotationType", "ABOUT_Z"), qty("rotation", deg(rotation)),
                   qty("translationX", mm(translation[0])), qty("translationY", mm(translation[1])), qty("translationZ", mm(translation[2]))]
    return {"btType": "BTMMateConnector-66", "featureType": "mateConnector", "featureId": feature_id, "name": feature_id,
            "namespace": "", "parameters": params}


def mate(name: str, mate_type: str, a: dict[str, Any], b: dict[str, Any], limits: tuple[float, float] | None = None) -> dict[str, Any]:
    params: list[dict[str, Any]] = [
        enum("mateType", "Mate type", mate_type),
        {"btType": "BTMParameterQueryWithOccurrenceList-67", "parameterId": "mateConnectorsQuery", "queries": [
            {"btType": "BTMFeatureQueryWithOccurrence-157", "path": [], "featureId": a["featureId"], "queryData": ""},
            {"btType": "BTMFeatureQueryWithOccurrence-157", "path": [], "featureId": b["featureId"], "queryData": ""}]},
    ]
    if limits is not None:
        params += [boolean("limitsEnabled", True), nullable_qty("limitAxialZMin", deg(limits[0])), nullable_qty("limitAxialZMax", deg(limits[1]))]
    return {"btType": "BTMMate-64", "featureType": "mate", "name": name, "namespace": "", "parameters": params, "mateConnectors": [a, b]}
