"""Phase B CAD: an organized Onshape document built the way an engineer builds it.

Layout (one live robot document, plus the reusable parts library):

* ``R3 Parts Library``: one Part Studio per purchased part family (electronics,
  wheels, bearings, fasteners...), versioned and inserted by reference. It is
  rebuilt only when its content digest changes.
* ``<robot>``: tab folders ``01 Variables`` (Variable Studio), ``02 Fabricated
  parts`` (one Part Studio per fabricated object type), ``04 Subassemblies`` and
  ``05 Top-level assembly`` (``03 Purchased parts`` is the linked library).

Assemblies are mated on the parts' interface mate connectors from the compiled
joins (tree joins plus the moving loop closures); fasteners mate on the far-face
seat connectors of their clearance holes. Onshape's solver places everything;
R3 then reads the solved occurrence transforms back and requires them to equal
the compiled poses. The exact-geometry fit checks (:mod:`r3_onshape.fit`), the
motion sweep and the meshes for the simulator use the Onshape Part Studio
geometry moved by those solved transforms.

Organizational writes that exist only in Onshape's editor protocol (feature,
tab and assembly folders; hiding reference geometry) go through the
onshape-native bridge when it is available (:mod:`r3_onshape.bridge`); the
organization check reads everything it can over REST and reports exactly what
it could not verify headlessly.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.assembly import FLIP, frame_matrix, invert
from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.units import to_si

from .client import OnshapeError, Transport
from .geometry import plan_manifold
from .model import Face, GeometryModel, PartGeometry, part_from_onshape, CHORD_TOLERANCE, ANGLE_TOLERANCE
from .parts import LocalPart, build_part_catalog
from .studio import BuiltStudio, Variable, build_studio, connector_frame

ORGANIZER_VERSION = "phase-b-1.0"
DEFAULT_NAME = re.compile(r"^(Sketch|Extrude|Revolve|Fillet|Chamfer|Hole|Mate connector|Plane|Fastened|Revolute|Mirror|Ball|Slider)\s*\d+$")
TOP_LEVEL = "Top-level assembly"
TAB_FOLDERS = ("01 Variables", "02 Fabricated parts", "04 Subassemblies", "05 Top-level assembly")
POSE_TOL = 1e-6  # m and rad-equivalent for solved vs compiled transforms
MAX_PLANES = 3
ELECTRONIC_KINDS = {"computer", "pcb", "radio", "sensor", "esc", "battery", "fuse", "antenna", "motor", "servo", "connector"}


# ===================================================================== planning (offline, testable)
@dataclass
class Placement:
    iid: str  # component id, fastener:<id> or nut:<id>
    key: str  # LocalPart key
    level: str  # subassembly name or TOP_LEVEL
    pose: np.ndarray  # robot frame (= the subassembly frame)
    group: str  # instance folder: Structure | Electronics | Fasteners


@dataclass
class MatePlan:
    name: str
    kind: str  # FASTENED | REVOLUTE | BALL | SLIDER
    a: tuple[str, str]  # (iid, connector name)
    b: tuple[str, str]
    clock: float
    offset: float
    level: str
    folder: str
    join: str
    limits: tuple[float, float] | None = None
    aligned: bool = False  # z axes aligned instead of opposed (a nut seat continues outward from the hole's far face)


@dataclass
class AssemblyPlan:
    placements: dict[str, Placement]
    mates: list[MatePlan]
    levels: list[str]


def _level_of(project: Project, cid: str) -> str:
    for name, members in project.spec.cad.subassemblies.items():
        if cid in members:
            return name
    return TOP_LEVEL


def _group(project: Project, iid: str) -> str:
    if ":" in iid:
        return "Fasteners"
    ctype = project.catalog.component(project.spec.components[iid].type)
    if ctype.kind in ELECTRONIC_KINDS:
        return "Electronics"
    if ctype.id.startswith(("kingpin-screw", "ball-stud", "hat-standoff", "wheel-nut")):
        return "Fasteners"
    return "Structure"


def _connector_world(parts: dict[str, LocalPart], placement: Placement, name: str) -> np.ndarray:
    spec = next(c for c in parts[placement.key].connectors if c.name == name)
    built, _ = connector_frame(spec.frame)
    return placement.pose @ built


def _residual(parts: dict[str, LocalPart], key: str, name: str) -> float:
    spec = next(c for c in parts[key].connectors if c.name == name)
    return connector_frame(spec.frame)[1]


def plan_assembly(project: Project, parts: dict[str, LocalPart], instance_part: dict[str, str]) -> AssemblyPlan:
    """Placements (compiled poses) and mates for every instance. Mates: every tree
    join, the moving loop closures that are not coaxial duplicates (ball joints),
    and one fastened mate per plain fastener/nut on its hole's seat connector."""
    from .parts import fastener_connector, instance_poses

    spec, result = project.spec, project.assembly
    poses = instance_poses(project)
    placements: dict[str, Placement] = {}
    for iid, key in instance_part.items():
        level = _level_of(project, iid) if iid in spec.components else TOP_LEVEL
        placements[iid] = Placement(iid, key, level, poses[iid], _group(project, iid))
    joins = {join.join_id: join for join in spec.assembly.joins}
    tree = {edge.join.join_id for edge in result.tree}
    mates: list[MatePlan] = []

    def level_for(*objs: str) -> str:
        levels = {placements[o].level for o in objs}
        return levels.pop() if len(levels) == 1 else TOP_LEVEL

    for join_id, (a, b) in result.valid.items():
        join = joins[join_id]
        moving_loop = join_id not in tree and join.kind == "ball"
        if join_id not in tree and not moving_loop:
            continue
        kind = {"fasten": "FASTENED", "drive_flat": "FASTENED", "revolute": "REVOLUTE", "slider": "SLIDER", "ball": "BALL"}[join.kind]
        clock = result.joins[join_id]["clock"] + _residual(parts, instance_part[a.obj], f"mc_{a.name}") \
            + _residual(parts, instance_part[b.obj], f"mc_{b.name}")
        if kind == "FASTENED":
            name = f"Fasten {b.obj} to {a.obj}" + (" (D-flat)" if join.kind == "drive_flat" else "")
            folder = "Mounting mates"
        elif kind == "REVOLUTE":
            name = f"Revolute {join.joint or join_id}"
            folder = "Joint mates"
        else:
            name = f"{kind.title()} {join_id}"
            folder = "Joint mates"
        limits = tuple(float(v) for v in join.limits) if join.limits else None
        mates.append(MatePlan(name, kind, (a.obj, f"mc_{a.name}"), (b.obj, f"mc_{b.name}"), clock, float(join.offset),
                              level_for(a.obj, b.obj), folder, join_id, limits))  # type: ignore[arg-type]
    seat = fastener_connector()
    for fastener in spec.mechanical.fasteners:
        if fastener.body or not fastener.join:
            continue
        join = joins[fastener.join]
        a, b = result.valid[fastener.join]
        match = result.matches[fastener.join]
        index = int(fastener.id.rsplit("_", 1)[1]) - 1 if fastener.id != fastener.join else 0
        i, j, _ = match.pairs[index]
        holes = {"a": (a, a.holes[i], i), "b": (b, b.holes[j], j)}
        if fastener.nut:
            head_side = join.fasteners.head if join.fasteners else "a"
            other_side = "b" if head_side == "a" else "a"
        else:
            head_side = "a" if holes["a"][1].kind == "clearance" else "b"
            other_side = None
        level = level_for(a.obj, b.obj)
        for iid, side in ((fastener.part, head_side), (fastener.nut_part, other_side)):
            if iid is None or side is None:
                continue
            iface, _, index_hole = holes[side]
            connector = f"mc_{iface.name}_h{index_hole + 1}"
            part_pose = placements[iface.obj].pose
            # A screw head seats against the far face (opposed z, flipped); a nut's body continues outward from it (aligned z).
            nut = iid.startswith("nut:")
            world = _connector_world(parts, Placement("", instance_part[iface.obj], "", part_pose, ""), connector)
            placements[iid].pose = (world if nut else world @ FLIP) @ invert(seat.frame)
            placements[iid].level = level
            label = parts[instance_part[iid]].name
            mates.append(MatePlan(f"Fasten {label} {fastener.id}" + (" nut" if nut else ""), "FASTENED",
                                  (iface.obj, connector), (iid, "mc_seat"), 0.0, 0.0, level, "Fastener mates", fastener.join,
                                  aligned=nut))
    levels = [*project.spec.cad.subassemblies, TOP_LEVEL]
    return AssemblyPlan(placements, mates, levels)


def expected_world(plan: AssemblyPlan) -> dict[str, np.ndarray]:
    return {iid: p.pose for iid, p in plan.placements.items()}


# ===================================================================== variables
def robot_variables(project: Project) -> tuple[dict[str, Variable], dict[str, dict[str, Any]]]:
    """Variable Studio contents and the Part Studio features that must reference them."""
    spec, catalog = project.spec, project.catalog
    steering = next((t for t in spec.mechanical.transmissions if t.type == "ackermann_steering"), None)
    wheelbase = to_si(steering.params.get("wheelbase", 0.26)) if steering else 0.26
    track = to_si(steering.params.get("kingpin_track", 0.135)) if steering else 0.135
    plate = float(catalog.component(spec.components[spec.assembly.root].type).mechanical.size[2])
    deck_type = next((c.type for c in spec.components.values() if "deck" in c.type), None)
    deck = float(catalog.component(deck_type).mechanical.size[2]) if deck_type else 0.003
    variables = {
        "plate_thickness": Variable("plate_thickness", f"{plate * 1e3:g} mm", "Chassis plate thickness (6061-T6 sheet)", plate),
        "deck_thickness": Variable("deck_thickness", f"{deck * 1e3:g} mm", "Printed electronics deck thickness", deck),
        "wheelbase": Variable("wheelbase", f"{wheelbase * 1e3:g} mm", "Rear axle to front kingpin axis", wheelbase),
        "kingpin_track": Variable("kingpin_track", f"{track * 1e3:g} mm", "Distance between the kingpin axes", track),
    }
    root_x = float(spec.components[spec.assembly.root].pose.xyz[0])

    def offset(var: str, value: float) -> str:
        delta = variables[var].value - value
        return f"#{var} - {delta * 1e3:g} mm" if delta >= 0 else f"#{var} + {-delta * 1e3:g} mm"

    def half(value: float) -> str:
        delta = track / 2 - value
        return f"#kingpin_track / 2 - {delta * 1e3:g} mm" if delta >= 0 else f"#kingpin_track / 2 + {-delta * 1e3:g} mm"

    chassis_type = catalog.component(spec.components[spec.assembly.root].type)
    points: dict[str, dict[tuple[str, int], str]] = {}
    for name, iface in chassis_type.interfaces.items():
        if not name.startswith("arm_"):
            continue
        ox, oy = to_si(iface.origin[0]), to_si(iface.origin[1])
        # Plate-frame x of the arm holes = wheelbase - (rear axle to plate origin): "#wheelbase - 130 mm".
        entries = {("x", int(round(ox * 1e6))): offset("wheelbase", ox)}
        for hole in iface.params.get("holes", []):
            y = oy + to_si(hole[1])
            entries[("y", int(round(y * 1e6)))] = half(abs(y))
        points[name] = entries
    bindings = {cad_name_of(project, spec.assembly.root): {"extrude": {"plate": "plate_thickness"}, "points": points}}
    if deck_type:
        bindings[project.catalog.component(deck_type).cad_name or "Electronics deck"] = {"extrude": {"deck": "deck_thickness"}}
    return variables, bindings


def cad_name_of(project: Project, cid: str) -> str:
    ctype = project.catalog.component(project.spec.components[cid].type)
    return ctype.cad_family or ctype.cad_name or ctype.name


# ===================================================================== documents
@dataclass
class StudioRef:
    name: str
    element_id: str
    part_ids: dict[str, str]
    connectors: dict[str, dict[str, str]]
    folders: dict[str, list[str]]


@dataclass
class DocumentInfo:
    did: str
    wid: str
    name: str
    vid: str = ""  # library version inserted by the robot document
    studios: dict[str, StudioRef] = field(default_factory=dict)
    assemblies: dict[str, str] = field(default_factory=dict)  # level -> element id
    variable_studio: str = ""
    digest: str = ""

    def url(self, eid: str | None = None) -> str:
        base = f"https://cad.onshape.com/documents/{self.did}/w/{self.wid}"
        return f"{base}/e/{eid}" if eid else base

    def part_ref(self, key: str) -> tuple[str, str, str]:
        for studio in self.studios.values():
            if key in studio.part_ids:
                return studio.element_id, studio.part_ids[key], studio.name
        raise KeyError(key)


def _new_document(api: Transport, name: str, folder: str | None, description: str) -> DocumentInfo:
    body: dict[str, Any] = {"name": name, "isPublic": True, "description": description}
    if folder:
        body["parentId"] = folder
    info = api.json("POST", "/documents", body=body)
    return DocumentInfo(did=info["id"], wid=info["defaultWorkspace"]["id"], name=name)


def _default_elements(api: Transport, doc: DocumentInfo) -> list[dict[str, Any]]:
    return api.json("GET", f"/documents/d/{doc.did}/w/{doc.wid}/elements")


def _build_studios(api: Transport, doc: DocumentInfo, groups: dict[str, list[LocalPart]], result: StageResult,
                   variables: dict[str, Variable] | None = None, bindings: dict[str, Any] | None = None) -> None:
    defaults = _default_elements(api, doc)
    # Real purchased models bypass build_studio entirely. Separate imported
    # Part Studios keep vendor bodies and provenance intact within a family.
    native_groups = {}
    for name, members in groups.items():
        for part in members:
            from .purchased import public_source
            if public_source(part):
                continue  # insert the validated public version directly; never rebuild it
            if part.bank_model:
                from .purchased import import_studio
                built = import_studio(api, doc.did, doc.wid, part)
                doc.studios[part.studio] = StudioRef(part.studio, built.element_id, built.part_ids, built.connectors, built.folders)
            else:
                native_groups.setdefault(name, []).append(part)
    for name, members in native_groups.items():
        eid = api.json("POST", f"/partstudios/d/{doc.did}/w/{doc.wid}", body={"name": name})["id"]
        if doc.variable_studio:
            api.request("POST", f"/variables/d/{doc.did}/w/{doc.wid}/e/{eid}/variablestudioreferences",
                        body={"references": [{"referenceElementId": doc.variable_studio, "entireVariableStudio": True}]})
        built = build_studio(api, doc.did, doc.wid, eid, name, members, variables, (bindings or {}).get(name))
        doc.studios[name] = StudioRef(name, eid, built.part_ids, built.connectors, built.folders)
        for warning in built.warnings:
            result.add(Diagnostic.info("cad_feature_warning", f"{warning} regenerated with a warning", subjects=[name]))
    for element in defaults:  # new documents start with an empty "Part Studio 1" and "Assembly 1"
        if element.get("elementType") in ("PARTSTUDIO", "ASSEMBLY") and element["id"] not in {s.element_id for s in doc.studios.values()}:
            api.request("DELETE", f"/elements/d/{doc.did}/w/{doc.wid}/e/{element['id']}")


def library_digest(parts: dict[str, LocalPart]) -> str:
    from .direct import _jsonable

    payload = {key: {"plan": _jsonable(p.plan), "connectors": [[c.name, c.frame.round(9).tolist()] for c in p.connectors],
                     "name": p.name, "studio": p.studio, "properties": p.properties}
               for key, p in sorted(parts.items()) if p.library}
    payload["builder"] = ORGANIZER_VERSION
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def ensure_library(api: Transport, project: Project, parts: dict[str, LocalPart], result: StageResult) -> DocumentInfo:
    """The reusable parts library (reused when its digest matches the lock)."""
    digest = library_digest(parts)
    locked = (project.lock.get("cad_library") or {})
    if locked.get("digest") == digest and locked.get("document_id"):
        try:
            api.json("GET", f"/documents/{locked['document_id']}")
            doc = DocumentInfo(did=locked["document_id"], wid=locked["workspace_id"], name=locked.get("name", ""),
                               vid=locked["version_id"], digest=digest)
            for name, ref in (locked.get("studios") or {}).items():
                doc.studios[name] = StudioRef(name, ref["element_id"], ref["part_ids"], ref["connectors"], ref.get("folders", {}))
            result.add(Diagnostic.info("cad_library_reused", "parts library matches its digest", data={"document_id": doc.did}))
            return doc
        except OnshapeError:
            pass
    name = project.spec.cad.library_name
    doc = _new_document(api, name, project.spec.cad.folder,
                        "Purchased parts and fasteners modeled at their own origins with named interface mate connectors "
                        "(R3 direct CAD). Inserted by version into robot documents.")
    doc.digest = digest
    groups: dict[str, list[LocalPart]] = {}
    for part in parts.values():
        if part.library:
            groups.setdefault(part.studio, []).append(part)
    try:
        _build_studios(api, doc, dict(sorted(groups.items())), result)
        version = api.json("POST", f"/documents/d/{doc.did}/versions",
                           body={"documentId": doc.did, "workspaceId": doc.wid, "name": f"R3 {digest}",
                                 "description": f"parts library digest {digest}"})
        doc.vid = version["id"]
    except Exception:
        api.request("DELETE", f"/documents/{doc.did}")
        raise
    old = locked.get("document_id")
    if old and old != doc.did:
        try:
            api.request("DELETE", f"/documents/{old}")
        except OnshapeError:
            pass
    project.write_lock("cad_library", {"document_id": doc.did, "workspace_id": doc.wid, "version_id": doc.vid, "digest": digest,
                                       "name": name, "url": doc.url(),
                                       "studios": {n: {"element_id": s.element_id, "part_ids": s.part_ids, "connectors": s.connectors,
                                                       "folders": s.folders} for n, s in doc.studios.items()}})
    return doc


# ===================================================================== assemblies
class AssemblyBuilder:
    def __init__(self, api: Transport, project: Project, robot: DocumentInfo, library: DocumentInfo, parts: dict[str, LocalPart],
                 plan: AssemblyPlan, result: StageResult) -> None:
        self.api, self.project, self.robot, self.library, self.parts, self.plan, self.result = api, project, robot, library, parts, plan, result
        self.instance_ids: dict[str, dict[str, str]] = {}  # level -> iid -> instance id
        self.sub_instances: dict[str, str] = {}  # sub level -> instance id in the top level
        self.mate_ids: dict[str, list[tuple[str, MatePlan]]] = {}

    def base(self, level: str) -> str:
        return f"/assemblies/d/{self.robot.did}/w/{self.robot.wid}/e/{self.robot.assemblies[level]}"

    def _source(self, key: str) -> tuple[DocumentInfo, str, str, str]:
        from .purchased import public_source
        source = public_source(self.parts[key])
        if source:
            if self.api.json("GET", f"/documents/{source.did}").get("isPublic") is not True:
                raise ValueError(f"{source.did}: bank source is not public")
            doc = DocumentInfo(did=source.did, wid="", vid=source.vid, name=self.parts[key].name)
            return doc, source.eid, source.part_id, source.vid
        for doc in (self.robot, self.library):
            try:
                eid, pid, _ = doc.part_ref(key)
                return doc, eid, pid, doc.vid
            except KeyError:
                continue
        raise KeyError(key)

    def _insert_parts(self, level: str, iids: list[str]) -> None:
        by_key: dict[str, list[str]] = {}
        for iid in iids:
            by_key.setdefault(self.plan.placements[iid].key, []).append(iid)
        for key, members in by_key.items():
            doc, eid, pid, vid = self._source(key)
            body: dict[str, Any] = {"documentId": doc.did, "elementId": eid, "partId": pid, "isAssembly": False, "isWholePartStudio": False}
            if vid:
                body["versionId"] = vid
            for _ in members:
                self.api.json("POST", self.base(level) + "/instances", body=body)
        definition = self.api.json("GET", self.base(level))
        pools: dict[tuple[str, str], list[str]] = {}
        for instance in definition["rootAssembly"]["instances"]:
            if instance.get("type") == "Part":
                pools.setdefault((instance.get("elementId"), instance.get("partId")), []).append(instance["id"])
        ids: dict[str, str] = {}
        for key, members in by_key.items():
            _, eid, pid, _ = self._source(key)
            pool = pools.get((eid, pid), [])
            if len(pool) < len(members):
                raise RuntimeError(f"{level}: inserted {len(members)} instances of {key} but found {len(pool)}")
            for iid, instance_id in zip(members, pool):
                ids[iid] = instance_id
        self.instance_ids[level] = ids

    def _place(self, level: str, path: list[str], pose: np.ndarray) -> None:
        if np.abs(pose - np.eye(4)).max() < 1e-15:
            return
        self.api.json("POST", self.base(level) + "/occurrencetransforms",
                      body={"occurrences": [{"path": path}], "transform": pose.flatten().tolist(), "isRelative": True})

    def _path(self, level: str, iid: str) -> list[str]:
        placement = self.plan.placements[iid]
        if placement.level == level:
            return [self.instance_ids[level][iid]]
        return [self.sub_instances[placement.level], self.instance_ids[placement.level][iid]]

    def _connector_feature(self, iid: str, name: str) -> str:
        key = self.plan.placements[iid].key
        from .purchased import public_source
        if public_source(self.parts[key]):
            return self.parts[key].bank_model["onshape_connectors"][name]
        for doc in (self.robot, self.library):
            for studio in doc.studios.values():
                if key in studio.connectors and name in studio.connectors[key]:
                    return studio.connectors[key][name]
        raise KeyError(f"{iid}: no connector {name}")

    def _mate(self, level: str, mate: MatePlan) -> str:
        from . import features as fx

        def query(end: tuple[str, str]) -> dict[str, Any]:
            return {"btType": "BTMPartStudioMateConnectorQuery-1324", "path": self._path(level, end[0]),
                    "featureId": self._connector_feature(*end), "queryData": "", "partStudioMateConnectorQuery": True}

        params: list[dict[str, Any]] = [
            fx.enum("mateType", "Mate type", mate.kind),
            {"btType": "BTMParameterQueryWithOccurrenceList-67", "parameterId": "mateConnectorsQuery", "queries": [query(mate.a), query(mate.b)]},
            fx.boolean("primaryAxisAlignment", not mate.aligned),  # opposed z axes: the compiler's mate
        ]
        if abs(mate.clock) > 1e-12 or abs(mate.offset) > 1e-12:
            params += [fx.boolean("mateOffsetEnabled", True), fx.qty("mateOffsetDistanceZ", fx.mm(-mate.offset)),
                       fx.enum("rotationType", "Rotation axis", "ABOUT_Z"), fx.qty("rotation", fx.deg(-mate.clock))]
        if mate.limits and mate.kind == "REVOLUTE":
            params += [fx.boolean("limitsEnabled", True), fx.nullable_qty("limitAxialZMin", fx.deg(mate.limits[0])),
                       fx.nullable_qty("limitAxialZMax", fx.deg(mate.limits[1]))]
        body = {"btType": "BTMMate-64", "featureType": "mate", "name": mate.name[:100], "namespace": "", "parameters": params}
        response = self.api.json("POST", self.base(level) + "/features", body={"btType": "BTFeatureDefinitionCall-1406", "feature": body})
        status = (response.get("featureState") or {}).get("featureStatus", "OK")
        if status == "ERROR":
            self.result.add(Diagnostic.error("assembly_mate_failed", f"{level}: mate {mate.name!r} regenerated with an error",
                                             subjects=[mate.a[0], mate.b[0], mate.join], data={"level": level}))
        return response["feature"]["featureId"]

    def build(self) -> None:
        levels = self.plan.levels
        for level in levels:
            self.robot.assemblies[level] = self.api.json("POST", f"/assemblies/d/{self.robot.did}/w/{self.robot.wid}", body={"name": level})["id"]
        for level in levels:
            members = [iid for iid, p in self.plan.placements.items() if p.level == level]
            if level == TOP_LEVEL:
                for sub in levels[:-1]:
                    self.api.json("POST", self.base(level) + "/instances",
                                  body={"documentId": self.robot.did, "elementId": self.robot.assemblies[sub], "isAssembly": True})
                definition = self.api.json("GET", self.base(level))
                by_element = {i["elementId"]: i["id"] for i in definition["rootAssembly"]["instances"] if i.get("type") == "Assembly"}
                self.sub_instances = {sub: by_element[self.robot.assemblies[sub]] for sub in levels[:-1]}
            self._insert_parts(level, members)
            for iid in members:
                self._place(level, [self.instance_ids[level][iid]], self.plan.placements[iid].pose)
            for mate in (m for m in self.plan.mates if m.level == level):
                self.mate_ids.setdefault(level, []).append((self._mate(level, mate), mate))

    # -------------------------------------------------------------- verification
    def solved(self) -> dict[str, np.ndarray]:
        """Robot-frame transform of every instance, read from the top-level assembly."""
        definition = self.api.json("GET", self.base(TOP_LEVEL))
        reverse: dict[tuple[str, ...], str] = {}
        for iid, placement in self.plan.placements.items():
            reverse[tuple(self._path(TOP_LEVEL, iid))] = iid
        out = {}
        for occurrence in definition["rootAssembly"].get("occurrences", []):
            iid = reverse.get(tuple(occurrence["path"]))
            if iid is not None:
                out[iid] = np.asarray(occurrence["transform"], dtype=float).reshape(4, 4)
        return out

    def verify(self) -> dict[str, Any]:
        solved = self.solved()
        expected = expected_world(self.plan)
        worst_position, worst_angle, moved, missing = 0.0, 0.0, [], []
        for iid, pose in expected.items():
            got = solved.get(iid)
            if got is None:
                missing.append(iid)
                continue
            dp = float(np.linalg.norm(got[:3, 3] - pose[:3, 3]))
            r = got[:3, :3].T @ pose[:3, :3]
            da = math.acos(max(-1.0, min(1.0, (float(np.trace(r)) - 1.0) / 2.0)))
            worst_position, worst_angle = max(worst_position, dp), max(worst_angle, da)
            if dp > POSE_TOL or da > POSE_TOL:
                moved.append({"instance": iid, "position_error": dp, "angle_error": da})
        if missing:
            self.result.add(Diagnostic.error("assembly_instance_missing", f"{len(missing)} instances not found in the solved assembly",
                                             subjects=missing[:20], data={"count": len(missing)}))
        if moved:
            self.result.add(Diagnostic.error(
                "assembly_pose_mismatch", f"{len(moved)} solved occurrence transforms differ from the compiled poses "
                f"(worst {worst_position * 1e3:.4f} mm / {math.degrees(worst_angle):.4f} deg)",
                subjects=[m["instance"] for m in moved[:20]], data={"count": len(moved), "worst_position": worst_position,
                                                                   "worst_angle": worst_angle, "instances": moved[:40]}))
        states = {}
        for level in self.plan.levels:
            features = self.api.json("GET", self.base(level) + "/features")
            states.update({f["featureId"]: s.get("featureStatus") for f in features.get("features", []) for s in
                           [features.get("featureStates", {}).get(f["featureId"], {})]})
        failed = [m.name for level in self.mate_ids for fid, m in self.mate_ids[level] if states.get(fid) not in ("OK", None)]
        if failed:
            self.result.add(Diagnostic.error("assembly_mate_unsatisfied", f"{len(failed)} mates do not regenerate OK", subjects=failed[:20],
                                             data={"count": len(failed)}))
        return {"instances": len(expected), "solved": len(solved), "worst_position_error": worst_position,
                "worst_angle_error": worst_angle, "pose_mismatches": len(moved), "mates": sum(len(v) for v in self.mate_ids.values()),
                "mates_failed": failed, "solved_transforms": {k: v.tolist() for k, v in solved.items()}}


# ===================================================================== geometry for the fit checks
def transform_part(part: PartGeometry, matrix: np.ndarray, name: str) -> PartGeometry:
    rotation, translation = matrix[:3, :3], matrix[:3, 3]
    move = lambda p: p @ rotation.T + translation  # noqa: E731
    faces = []
    for f in part.faces:
        corners = np.array([[x, y, z] for x in (f.lo[0], f.hi[0]) for y in (f.lo[1], f.hi[1]) for z in (f.lo[2], f.hi[2])]) @ rotation.T + translation
        faces.append(Face(id=f.id, part=name, kind=f.kind, origin=move(f.origin), direction=f.direction @ rotation.T, radius=f.radius,
                          area=f.area, lo=corners.min(axis=0), hi=corners.max(axis=0), convex=f.convex,
                          triangles=None if f.triangles is None else move(f.triangles.reshape(-1, 3)).reshape(-1, 3, 3)))
    out = PartGeometry(name=name, part_id=part.part_id, faces=faces)
    out.vertices, out.triangles = move(part.vertices), part.triangles.copy()
    return out


def fetch_local_geometry(api: Transport, doc: DocumentInfo, keys: set[str], fixture: dict[str, Any] | None = None) -> dict[str, PartGeometry]:
    """Exact local geometry (bodydetails + fine tessellation) of every needed part."""
    wvm, wvmid = ("v", doc.vid) if doc.vid else ("w", doc.wid)
    out = {}
    for studio in doc.studios.values():
        wanted = {key: pid for key, pid in studio.part_ids.items() if key in keys}
        if not wanted:
            continue
        base = f"/partstudios/d/{doc.did}/{wvm}/{wvmid}/e/{studio.element_id}"
        details = api.json("GET", base + "/bodydetails")
        bodies = {b["id"]: b for b in details.get("bodies", [])}
        for key, pid in wanted.items():
            tess = api.json("GET", base + "/tessellatedfaces", query={"partId": pid, "chordTolerance": CHORD_TOLERANCE,
                                                                       "angleTolerance": ANGLE_TOLERANCE, "outputFaceAppearances": False})
            out[key] = part_from_onshape(key, pid, bodies.get(pid, {}), tess)
            if fixture is not None:
                fixture[key] = {"part_id": pid, "body": bodies.get(pid, {}), "tessellation": tess}
    return out


def local_mass(api: Transport, doc: DocumentInfo, keys: set[str]) -> dict[str, dict[str, Any]]:
    wvm, wvmid = ("v", doc.vid) if doc.vid else ("w", doc.wid)
    out = {}
    for studio in doc.studios.values():
        wanted = {key: pid for key, pid in studio.part_ids.items() if key in keys}
        if not wanted:
            continue
        mass = api.json("GET", f"/partstudios/d/{doc.did}/{wvm}/{wvmid}/e/{studio.element_id}/massproperties",
                        query={"massAsGroup": False, "useMassPropertyOverrides": True})
        bodies = mass.get("bodies", {})
        for key, pid in wanted.items():
            out[key] = bodies.get(pid, {})
    return out


def compose_world(local: dict[str, PartGeometry], plan: AssemblyPlan, solved: dict[str, np.ndarray]) -> GeometryModel:
    parts = {}
    for iid, placement in plan.placements.items():
        geometry = local.get(placement.key)
        if geometry is None:
            continue
        parts[iid] = transform_part(geometry, solved.get(iid, placement.pose), iid)
    return GeometryModel(parts=parts)


# ===================================================================== organization (native bridge)
def organize(bridge: Any, robot: DocumentInfo, library: DocumentInfo, plan: AssemblyPlan, result: StageResult,
             library_fresh: bool, builder: Any) -> dict[str, Any]:
    """Feature folders, tab folders, assembly instance/mate folders, hidden
    reference geometry. Returns what was done (verified by the bridge)."""
    report: dict[str, Any] = {"feature_folders": {}, "tab_folders": [], "assembly_folders": {}, "hidden": {}, "errors": []}

    def guard(label: str, fn, *args):
        try:
            return fn(*args)
        except Exception as error:  # noqa: BLE001 - record and continue organizing
            report["errors"].append(f"{label}: {str(error)[:300]}")
            result.add(Diagnostic.warning("cad_organize_failed", f"{label}: {str(error)[:300]}", subjects=[label]))
            return None

    docs = [robot] + ([library] if library_fresh else [])
    for doc in docs:
        for studio in doc.studios.values():
            done = []
            for folder in ("Base shape", "Bores & shafts", "Cutouts", "Mounting holes", "Mirror", "Mate connectors"):
                ids = studio.folders.get(folder) or []
                if ids and guard(f"{doc.name}/{studio.name}/{folder}", bridge.feature_folder, doc.did, doc.wid, studio.element_id, folder, ids):
                    done.append(folder)
            report["feature_folders"][f"{doc.name}/{studio.name}"] = done
            hide = list(studio.folders.get("Mate connectors") or []) + ["Top", "Front", "Right", "Origin"]
            outcome = guard(f"{doc.name}/{studio.name}/hide", bridge.hide_partstudio_features, doc.did, doc.wid, studio.element_id, hide)
            report["hidden"][f"{doc.name}/{studio.name}"] = {"features": len(hide), "ok": outcome is not None}
    groups = [(TAB_FOLDERS[0], [robot.variable_studio]), (TAB_FOLDERS[1], [s.element_id for s in robot.studios.values()]),
              (TAB_FOLDERS[2], [robot.assemblies[level] for level in plan.levels[:-1]]), (TAB_FOLDERS[3], [robot.assemblies[TOP_LEVEL]])]
    report["tab_folders"] = guard(f"{robot.name}/tabs", bridge.tab_folders, robot.did, robot.wid, groups) or []
    if library_fresh:
        lib_groups = _library_tab_groups(library)
        report["library_tab_folders"] = guard(f"{library.name}/tabs", bridge.tab_folders, library.did, library.wid, lib_groups) or []
    for level, eid in robot.assemblies.items():
        report.setdefault("assembly_hidden", {})[level] = guard(f"{robot.name}/{level}/hide", _hide_assembly_connectors, bridge, robot, eid)
        report["assembly_folders"][level] = guard(f"{robot.name}/{level}/folders", _assembly_folders, bridge, robot, eid, plan, level,
                                                  builder) or []
    bridge.close_tabs()
    return report


def _library_tab_groups(library: DocumentInfo) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = {"01 Electronics": [], "02 Mechanical": [], "03 Fasteners": []}
    for name, studio in library.studios.items():
        lowered = name.lower()
        if any(w in lowered for w in ("screw", "nut", "standoff m-f", "ball stud", "kingpin", "spacer m2.5")):
            groups["03 Fasteners"].append(studio.element_id)
        elif any(w in lowered for w in ("pi", "hat", "receiver", "gps", "encoder board", "esc", "lipo", "fuse", "motor", "servo", "antenna")):
            groups["01 Electronics"].append(studio.element_id)
        else:
            groups["02 Mechanical"].append(studio.element_id)
    return [(k, v) for k, v in groups.items() if v]


MATE_LIST = 'assembly_node:["mateList"]'


def _hide_assembly_connectors(bridge: Any, robot: DocumentInfo, eid: str) -> dict[str, Any]:
    """Hide every mate connector still shown in an assembly (inherited Part Studio connectors)."""
    state = bridge.display_rows(robot.did, robot.wid, eid)
    shown = [r for r in state["rows"] if r.get("effective_visible") and r.get("reference")
             and (r.get("inherited_connector") or "MateConnector" in str(r.get("native_type", "")))]
    hidden = 0
    for start in range(0, len(shown), 100):
        batch = shown[start:start + 100]
        result = bridge.set_visibility(state["snapshot"], [{"reference": r["reference"], "visible": False} for r in batch])
        hidden += len(batch)
        state = {**state, "snapshot": result.get("snapshot", state["snapshot"])}
    return {"connectors_hidden": hidden}


def _assembly_folders(bridge: Any, robot: DocumentInfo, eid: str, plan: AssemblyPlan, level: str, builder: Any) -> list[str]:
    """Instance folders (Structure / Electronics / Fasteners / Subassemblies) and mate folders
    (Mounting / Joint / Fastener mates), by R3's own instance and mate feature ids."""
    groups: dict[str, str] = {inst: plan.placements[iid].group for iid, inst in builder.instance_ids.get(level, {}).items()}
    if level == TOP_LEVEL:
        groups.update({inst: "Subassemblies" for inst in builder.sub_instances.values()})
    mate_folders = {fid: mate.folder for fid, mate in builder.mate_ids.get(level, [])}
    rows = bridge.element_tree(robot.did, robot.wid, eid)["rows"]
    by_group: dict[str, list[str]] = {}
    for row in rows:
        if row.get("kind") == "assembly_instance" and row.get("parent_id") == "assembly_sidebar" and row.get("node_id") in groups:
            by_group.setdefault(groups[row["node_id"]], []).append(row["id"])
        elif row.get("kind") == "assembly_sidebar_feature" and row.get("parent_id") == MATE_LIST and row.get("feature_id") in mate_folders:
            by_group.setdefault(mate_folders[row["feature_id"]], []).append(row["id"])
    made = []
    for group in ("Subassemblies", "Structure", "Electronics", "Fasteners", "Mounting mates", "Joint mates", "Fastener mates"):
        if by_group.get(group):
            bridge.assembly_folder(robot.did, robot.wid, eid, group, by_group[group])
            made.append(group)
    return made


# ===================================================================== organization check
def organization_check(api: Transport, bridge: Any, robot: DocumentInfo, library: DocumentInfo, parts: dict[str, LocalPart],
                       bindings: dict[str, Any], result: StageResult, organize_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Diagnostics for the document's organization, read over REST (headless)
    wherever Onshape exposes the data, else through the bridge."""
    report: dict[str, Any] = {"studios": {}, "tabs": {}, "assemblies": {}, "unchecked": []}

    def err(code: str, message: str, subjects: list[str], **data: Any) -> None:
        result.add(Diagnostic.error(code, message, subjects=subjects, data=data))

    quota_hit = ""  # set when Onshape's per-endpoint quota for Part Studio features is spent
    expected_names = {}
    for key, part in parts.items():
        expected_names.setdefault(part.studio, set()).add(part.name)
    for doc in (robot, library):
        wvm, wvmid = ("w", doc.wid)
        for name, studio in doc.studios.items():
            base = f"/partstudios/d/{doc.did}/{wvm}/{wvmid}/e/{studio.element_id}"
            entry: dict[str, Any] = {}
            try:
                feats = [] if quota_hit else api.json("GET", base + "/features").get("features", [])
            except OnshapeError as error:
                if error.status != 429:
                    raise
                quota_hit, feats = str(error), []
            entry["features"] = len(feats) if feats else None
            defaults = [f["name"] for f in feats if DEFAULT_NAME.match(f.get("name", ""))]
            if defaults:
                err("cad_default_feature_name", f"{doc.name}/{name}: {len(defaults)} features keep default names: {defaults[:5]}",
                    [name], count=len(defaults), names=defaults[:20])
            planes = [f for f in feats if f.get("featureType") == "cPlane"]
            entry["planes"] = len(planes) if feats else None  # unknown while the feature list is unavailable
            if len(planes) > MAX_PLANES:
                err("cad_excess_construction_planes", f"{doc.name}/{name}: {len(planes)} construction planes (> {MAX_PLANES})",
                    [name], planes=len(planes))
            connector_names = {f["name"].split(" (")[0] for f in feats if f.get("featureType") == "mateConnector"}
            for key, part in parts.items():
                if key not in studio.part_ids or not feats:
                    continue
                missing = sorted({c.name for c in part.connectors} - connector_names)
                if missing:
                    err("cad_missing_mate_connector", f"{doc.name}/{name}: {part.name} lacks {missing[:5]}", [part.name], missing=missing)
            sketches = api.json("GET", base + "/sketches").get("sketches", [])
            loose = [s["name"] for s in sketches if s.get("sketchSolveStatus") != "WELL_DEFINED"]
            entry["sketches"], entry["sketches_well_defined"] = len(sketches), len(sketches) - len(loose)
            if loose:
                err("cad_unconstrained_sketch", f"{doc.name}/{name}: {len(loose)} sketches are not fully defined: {loose[:5]}",
                    [name], sketches=loose[:20])
            actual = {p.get("name") for p in api.json("GET", f"/parts/d/{doc.did}/{wvm}/{wvmid}/e/{studio.element_id}")}
            wrong = sorted(expected_names.get(name, set()) ^ actual)
            if wrong:
                err("cad_part_name_mismatch", f"{doc.name}/{name}: part names differ from the plan: {wrong[:6]}", [name], names=wrong)
            for var_feature, var in (bindings.get(name, {}).get("extrude") or {}).items():
                for feature in feats:
                    if feature.get("featureType") == "extrude" and feature.get("name", "").startswith(var_feature + " extrude"):
                        exprs = [p.get("expression", "") for p in feature.get("parameters", []) if p.get("parameterId") == "depth"]
                        if not any(f"#{var}" in e for e in exprs):
                            err("cad_missing_variable_reference", f"{doc.name}/{name}: {feature['name']} hard-codes a dimension "
                                f"where #{var} exists", [name], feature=feature["name"], variable=var)
            for iface, entries in ((bindings.get(name, {}).get("points") or {}).items() if feats else ()):
                sketch = next((f for f in feats if f.get("featureType") == "newSketch" and f.get("name", "").startswith(f"{iface}:")), None)
                needed = {expr.split()[0] for expr in entries.values()}
                found = json.dumps(sketch) if sketch else ""
                for var in needed:
                    if var not in found:
                        err("cad_missing_variable_reference", f"{doc.name}/{name}: {iface} hole sketch does not reference {var}",
                            [name], sketch=iface, variable=var.lstrip("#"))
            report["studios"][f"{doc.name}/{name}"] = entry
    if quota_hit:
        report["unchecked"] += ["cad_default_feature_name (Part Studios)", "cad_missing_mate_connector", "cad_missing_variable_reference",
                                "cad_excess_construction_planes"]
        result.add(Diagnostic.warning("cad_organization_check_partial", "Onshape's API quota for Part Studio feature lists is "
                                      "exhausted; feature-name, mate-connector, variable and plane checks were not run",
                                      data={"error": quota_hit[:200], "unchecked": report["unchecked"]},
                                      hint="Re-run `python -m r3_onshape.organized organize examples/robots/rc-car` after the quota resets."))
    # ---- tab folders (REST: getDocumentContents)
    for doc in (robot, library):
        contents = api.json("GET", f"/documents/d/{doc.did}/w/{doc.wid}/contents")
        elements = {e["id"]: e for e in contents.get("elements", [])}
        root = contents.get("folders") or {}
        loose = [ref.get("elementId") for ref in root.get("groups", []) if ref.get("btType", "").startswith("BTDocumentElementReference")]
        loose = [eid for eid in loose if eid in elements and elements[eid].get("elementType") in ("PARTSTUDIO", "ASSEMBLY", "VARIABLESTUDIO")]
        folders = [g.get("groupName") for g in root.get("groups", []) if g.get("btType", "").startswith("BTElementGroup")]
        report["tabs"][doc.name] = {"folders": folders, "loose": [elements[e].get("name") for e in loose]}
        if loose:
            err("cad_tab_not_in_folder", f"{doc.name}: {len(loose)} tabs are not in a folder: {[elements[e].get('name') for e in loose][:6]}",
                [doc.name], tabs=[elements[e].get("name") for e in loose])
    # ---- assembly mates named (REST), folders and visibility (bridge)
    for level, eid in robot.assemblies.items():
        features = api.json("GET", f"/assemblies/d/{robot.did}/w/{robot.wid}/e/{eid}/features")
        names = [f.get("name", "") for f in features.get("features", [])]
        defaults = [n for n in names if DEFAULT_NAME.match(n)]
        if defaults:
            err("cad_default_feature_name", f"{level}: {len(defaults)} mates keep default names", [level], names=defaults[:20])
        report["assemblies"][level] = {"mates": len(names)}
    if bridge is None:
        report["unchecked"] += ["cad_feature_not_in_folder", "cad_assembly_unfoldered", "cad_visible_construction_geometry"]
        result.add(Diagnostic.info("cad_organization_check_partial", "feature folders, assembly folders and visibility are not exposed by "
                                   "Onshape's REST API; without the onshape-native bridge they were not checked",
                                   data={"unchecked": report["unchecked"]}))
        return report
    for doc in (robot, library):
        for name, studio in doc.studios.items():
            tree = bridge.element_tree(doc.did, doc.wid, studio.element_id)
            loose = [r.get("name") for r in tree["rows"] if r.get("kind") == "feature" and r.get("parent_id") == "root"
                     and r.get("feature_type") not in ("variableStudioReference",)]
            report["studios"][f"{doc.name}/{name}"]["features_outside_folders"] = len(loose)
            if loose:
                err("cad_feature_not_in_folder", f"{doc.name}/{name}: {len(loose)} features are not in a feature folder: {loose[:5]}",
                    [name], features=loose[:20])
            hidden = (organize_report or {}).get("hidden", {}).get(f"{doc.name}/{name}")
            if hidden is not None and not hidden.get("ok"):
                err("cad_visible_construction_geometry", f"{doc.name}/{name}: reference geometry was not hidden", [name])
    for level, eid in robot.assemblies.items():
        tree = bridge.element_tree(robot.did, robot.wid, eid)
        mate_ids = {r.get("feature_id") for r in tree["rows"] if r.get("kind") == "assembly_feature" and r.get("feature_type") == "mate"}
        loose = [r.get("name") for r in tree["rows"] if (r.get("kind") == "assembly_instance" and r.get("parent_id") == "assembly_sidebar")
                 or (r.get("kind") == "assembly_sidebar_feature" and r.get("parent_id") == MATE_LIST and r.get("feature_id") in mate_ids)]
        report["assemblies"][level]["outside_folders"] = loose
        if loose:
            err("cad_assembly_unfoldered", f"{level}: {len(loose)} instances/mates are not in folders: {loose[:5]}", [level], items=loose[:20])
        try:
            rows = bridge.display_rows(robot.did, robot.wid, eid)["rows"]
            visible = [r.get("name") for r in rows if r.get("effective_visible") and (r.get("inherited_connector")
                                                                                      or r.get("native_type", "").endswith("MateConnector"))]
            report["assemblies"][level]["visible_connectors"] = len(visible)
            if visible:
                err("cad_visible_construction_geometry", f"{level}: {len(visible)} mate connectors are visible", [level], items=visible[:20])
        except Exception as error:  # noqa: BLE001
            report["unchecked"].append(f"visibility {level}: {str(error)[:120]}")
    bridge.close_tabs()
    return report


# ===================================================================== views
def render_views(api: Transport, robot: DocumentInfo, library: DocumentInfo, out_dir: Path, result: StageResult) -> list[str]:
    """Headless REST shaded views: each subassembly and the top level (iso + top),
    plus every Part Studio (iso)."""
    from .views import ElementRef, shaded_view

    out_dir.mkdir(parents=True, exist_ok=True)
    images = []

    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

    jobs = [(f"assembly_{slug(level)}_{view}.png", ElementRef(robot.did, "w", robot.wid, eid, "assembly"), view)
            for level, eid in robot.assemblies.items() for view in (("iso", "top", "front") if level == TOP_LEVEL else ("iso",))]
    jobs += [(f"studio_{slug(name)}.png", ElementRef(robot.did, "w", robot.wid, s.element_id), "iso") for name, s in robot.studios.items()]
    jobs += [(f"library_{slug(name)}.png", ElementRef(library.did, "w", library.wid, s.element_id), "iso") for name, s in library.studios.items()]
    for filename, element, view in jobs:
        try:
            (out_dir / filename).write_bytes(shaded_view(api, element, view, width=1400, height=1000))
            images.append(filename)
        except (OnshapeError, KeyError, ValueError, OSError) as error:
            result.add(Diagnostic.warning("cad_views_failed", f"{filename}: {str(error)[:200]}", subjects=["cad"]))
    return images


# ===================================================================== pipeline
def _save_state(path: Path, robot: DocumentInfo, builder: AssemblyBuilder) -> None:
    state = {"robot": {"did": robot.did, "wid": robot.wid, "name": robot.name, "variable_studio": robot.variable_studio,
                       "assemblies": robot.assemblies, "studios": {n: asdict(s) for n, s in robot.studios.items()}},
             "instance_ids": builder.instance_ids, "sub_instances": builder.sub_instances,
             "mate_ids": {level: [[fid, asdict(m)] for fid, m in mates] for level, mates in builder.mate_ids.items()}}
    path.write_text(json.dumps(state, indent=1, default=str))


def _load_state(path: Path, robot: DocumentInfo, builder: AssemblyBuilder) -> None:
    state = json.loads(path.read_text())
    saved = state["robot"]
    robot.variable_studio, robot.assemblies = saved["variable_studio"], saved["assemblies"]
    robot.studios = {n: StudioRef(**s) for n, s in saved["studios"].items()}
    builder.instance_ids, builder.sub_instances = state["instance_ids"], state["sub_instances"]
    builder.mate_ids = {level: [(fid, MatePlan(**{**m, "a": tuple(m["a"]), "b": tuple(m["b"]),
                                                  "limits": tuple(m["limits"]) if m["limits"] else None})) for fid, m in mates]
                        for level, mates in state["mate_ids"].items()}



def input_digest(project: Project) -> str:
    """Cache key of the organized build: every part plan and connector, the
    assembly plan (placements and mates), variables, subassemblies and the
    builder/validator versions."""
    from .direct import _jsonable
    from .fit import VALIDATOR_VERSION

    parts, instances = build_part_catalog(project)
    plan = plan_assembly(project, parts, instances)
    variables, bindings = robot_variables(project)
    payload = {
        "organizer": ORGANIZER_VERSION, "validator": VALIDATOR_VERSION,
        "parts": {k: {"plan": _jsonable(p.plan), "connectors": [[c.name, c.frame.round(9).tolist()] for c in p.connectors],
                      "name": p.name, "studio": p.studio, "library": p.library, "mirror_of": p.mirror_of, "bank_model": p.bank_model} for k, p in sorted(parts.items())},
        "placements": {k: [v.key, v.level, v.group, v.pose.round(9).tolist()] for k, v in sorted(plan.placements.items())},
        "mates": [asdict(m) for m in plan.mates],
        "variables": {k: v.expression for k, v in variables.items()}, "bindings": repr(sorted((k, repr(v)) for k, v in bindings.items())),
        "mechanical": project.spec.mechanical.model_dump(mode="json"), "cad": project.spec.cad.model_dump(mode="json"),
        # Servo end points shape the steering table; max_steer_rad is derived from it (an output, not an input).
        "software_steering": {k: project.spec.software.config.get(k) for k in ("steering_pulse_us",)},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _world_mass(props: dict[str, Any], pose: np.ndarray) -> dict[str, Any]:
    rotation = pose[:3, :3]
    centroid = np.asarray(props.get("centroid", [0, 0, 0])[:3], dtype=float)
    tensor = np.asarray(props.get("inertia", [0.0] * 9)[:9], dtype=float).reshape(3, 3)
    world = rotation @ tensor @ rotation.T  # inertia about the centroid, rotated into the robot frame
    return {"mass": float(props.get("mass", [0.0])[0]), "volume": float(props.get("volume", [0.0])[0]),
            "com": (rotation @ centroid + pose[:3, 3]).tolist(),
            "inertia": [world[0, 0], world[1, 1], world[2, 2], world[0, 1], world[0, 2], world[1, 2]]}


def sync_organized(project: Project, api: Transport, result: StageResult, attempt_label: str = "", keep_failed: bool = False) -> dict[str, Any]:
    """Build (library if needed + a fresh robot document), organize, verify and import. Returns the report."""
    from .bridge import connect
    from .fit import verify_fit
    from .motion import run_motion
    from .sync import antenna_position, full_lock_views, mesh_filename, stl_bytes

    started = time.time()
    spec = project.spec
    parts, instances = build_part_catalog(project)
    plan = plan_assembly(project, parts, instances)
    variables, bindings = robot_variables(project)
    report: dict[str, Any] = {"parts_planned": len(parts), "instances": len(instances), "mates_planned": len(plan.mates)}
    locked_library = (project.lock.get("cad_library") or {}).get("digest")
    library = ensure_library(api, project, parts, result)
    # Organize the library when it was rebuilt, or when an earlier run never organized it (e.g. built without the bridge).
    library_fresh = library.digest != locked_library or not (project.lock.get("cad_library") or {}).get("organized")
    report["library"] = {"document_id": library.did, "version_id": library.vid, "url": library.url(), "studios": len(library.studios),
                         "rebuilt": library_fresh}
    name = spec.cad.document_name or spec.robot.name
    resume = os.environ.get("R3_CAD_RESUME") == "1" and (project.build_dir("cad") / "build_state.json").exists()
    if resume:  # debugging: continue organization/verification on the last built document
        saved = json.loads((project.build_dir("cad") / "build_state.json").read_text())["robot"]
        robot = DocumentInfo(did=saved["did"], wid=saved["wid"], name=saved["name"])
    else:
        robot = _new_document(api, f"{name} ({attempt_label})" if attempt_label else name, spec.cad.folder,
                              "Generated by R3 from robot.yaml: objects joined at interfaces, parts modeled at their own origins, "
                              "assemblies mated on interface mate connectors. Purchased parts: linked from the R3 Parts Library.")
    report["document"] = {"document_id": robot.did, "workspace_id": robot.wid, "url": robot.url()}
    result.metrics["onshape_document"] = robot.url()
    ok = False
    build_dir = project.build_dir("cad")
    state_path = build_dir / "build_state.json"
    try:
        builder = AssemblyBuilder(api, project, robot, library, parts, plan, result)
        if resume:
            _load_state(state_path, robot, builder)
        else:
            robot.variable_studio = api.json("POST", f"/variables/d/{robot.did}/w/{robot.wid}/variablestudio", body={"name": "Variables"})["id"]
            api.request("POST", f"/variables/d/{robot.did}/w/{robot.wid}/e/{robot.variable_studio}/variables",
                        body=[{"name": v.name, "type": "LENGTH", "expression": v.expression, "description": v.description}
                              for v in variables.values()])
            groups: dict[str, list[LocalPart]] = {}
            for part in parts.values():
                if not part.library:
                    groups.setdefault(part.studio, []).append(part)
            _build_studios(api, robot, dict(sorted(groups.items())), result, variables, bindings)
            builder.build()
            _save_state(state_path, robot, builder)
        verification = builder.verify()
        report["assembly"] = {k: v for k, v in verification.items() if k != "solved_transforms"}
        bridge = connect()
        organize_report = organize(bridge, robot, library, plan, result, library_fresh, builder) if bridge is not None else None
        report["organize"] = organize_report or {"skipped": "onshape-native bridge not connected (headless)"}
        if organize_report is not None and library_fresh and not [e for e in organize_report["errors"] if library.name in e]:
            project.write_lock("cad_library", {**(project.lock.get("cad_library") or {}), "organized": True})
        report["organization_check"] = organization_check(api, bridge, robot, library, parts, bindings, result, organize_report)
        solved = {k: np.asarray(v) for k, v in verification["solved_transforms"].items()}
        keys = {p.key for p in plan.placements.values()}
        fixture: dict[str, Any] = {}
        local = {**fetch_local_geometry(api, robot, keys, fixture), **fetch_local_geometry(api, library, keys, fixture)}
        from .purchased import public_documents
        external_docs = public_documents(parts)
        for document in external_docs:
            local.update(fetch_local_geometry(api, document, keys, fixture))
        model = compose_world(local, plan, solved)
        fixture_dir = build_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(fixture_dir / "onshape_local_geometry.json.gz", "wt") as handle:
            json.dump({"parts": fixture, "placements": {k: [v.key, solved.get(k, v.pose).tolist()] for k, v in plan.placements.items()}}, handle)
        # The plans this geometry was built from: an offline recomposition (r3_onshape.recompose) reuses a part only
        # while its plan solid is unchanged. A new recording also invalidates the cached part-frame masses.
        from .recompose import MASS_FIXTURE, record_plans

        record_plans(project, parts, input_digest(project))
        (build_dir / MASS_FIXTURE).unlink(missing_ok=True)
        materials = {iid: parts[p.key].plan.material for iid, p in plan.placements.items()}
        fit = verify_fit(project, model, materials, result)
        report["fit"] = fit.summary()
        report["kinematics"] = run_motion(project, model, fit, result)
        report["rf"] = antenna_position(project, model)
        masses = {**local_mass(api, robot, keys), **local_mass(api, library, keys)}
        for document in external_docs:
            masses.update(local_mass(api, document, keys))
        mesh_dir = build_dir / "meshes"
        mesh_dir.mkdir(parents=True, exist_ok=True)
        imported: dict[str, Any] = {}
        for iid, placement in plan.placements.items():
            geometry = model.parts.get(iid)
            if geometry is None:
                continue
            part = parts[placement.key]
            data = stl_bytes(geometry)
            filename = mesh_filename(iid)
            (mesh_dir / filename).write_bytes(data)
            entry = {"part_id": geometry.part_id, "part_name": part.name, "part_key": placement.key, "kind": part.plan.kind,
                     "owner": part.plan.owner if part.plan.kind == "fastener" else iid, "material": part.plan.material,
                     "density": part.plan.density or None, "mass_status": "catalog_override" if part.plan.kind == "purchased" else "cad_geometry",
                     "bbox_min": geometry.lo.tolist(), "bbox_max": geometry.hi.tolist(), "mesh": f"meshes/{filename}",
                     "mesh_triangles": int(len(geometry.triangles)), "mesh_sha256": hashlib.sha256(data).hexdigest(),
                     "subassembly": placement.level, "studio": part.studio, "library": part.library}
            if part.bank_model:
                entry["model_bank_id"] = part.bank_model["id"]
                entry["model_sources"] = part.bank_model["sources"]
                entry["model_validation"] = part.bank_model["validation"]
            elif part.plan.kind == "purchased":
                entry["proxy"] = True
            entry.update(_world_mass(masses.get(placement.key, {}), solved.get(iid, placement.pose)))
            if part.plan.kind == "fastener":
                fastener = next((f for f in spec.mechanical.fasteners if iid in (f.part, f.nut_part)), None)
                entry["owner"] = (fastener.thread_into or (fastener.clamps[0] if fastener.clamps else "")) if fastener else ""
            imported[iid] = entry
        report["parts"] = imported
        images = render_views(api, robot, library, build_dir / "views", result)
        plans = {iid: parts[p.key].plan for iid, p in plan.placements.items()}
        from .sync import SyncReport

        lock_report = SyncReport()
        lock_report.kinematics = report["kinematics"]
        full_lock_views(model, plans, report["kinematics"], build_dir, lock_report, result)
        report["images"] = [f"views/{i}" for i in images] + lock_report.images
        report["microversion"] = api.json("GET", f"/documents/d/{robot.did}/w/{robot.wid}/currentmicroversion").get("microversion", "")
        report["seconds"] = round(time.time() - started, 1)
        ok = result.ok
        report["robot"] = robot
        report["library_doc"] = library
        return report
    except (OnshapeError, ValueError, KeyError, RuntimeError, TypeError, AttributeError) as error:
        import traceback

        result.add(Diagnostic.error("onshape_request_failed", f"{type(error).__name__}: {str(error)[:400]}", subjects=["cad"],
                                    data={"status": getattr(error, "status", None), "trace": traceback.format_exc()[-1500:]}))
        return report
    finally:
        if not ok and not (keep_failed or resume or os.environ.get("R3_CAD_KEEP_FAILED") == "1"):
            try:
                api.request("DELETE", f"/documents/{robot.did}")
                result.add(Diagnostic.warning("cad_attempt_deleted", f"failed CAD attempt deleted: {robot.url()}", subjects=["cad"],
                                              data={"document_id": robot.did}))
            except OnshapeError as error:
                result.add(Diagnostic.error("cad_attempt_delete_failed", str(error), subjects=["cad"], data={"document_id": robot.did}))


def write_model(project: Project, report: dict[str, Any], transport: str, digest: str) -> Path:
    """``build/cad/cad_model.json`` (same consumer contract as the direct-CAD stage)."""
    from .fit import VALIDATOR_VERSION
    from .sync import _json_safe

    robot: DocumentInfo = report["robot"]
    library: DocumentInfo = report["library_doc"]
    parts = report["parts"]
    hashes = {name: part["mesh_sha256"] for name, part in sorted(parts.items())}
    mesh_digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    top = robot.assemblies[TOP_LEVEL]
    model = {
        "inputs": {**project.fingerprint(), "cad_revision": report.get("microversion") or digest},
        "source": {"provider": "onshape", "transport": transport, "document_id": robot.did, "workspace_id": robot.wid,
                   "element_id": top, "assembly_id": top, "url": robot.url(top), "microversion": report.get("microversion", ""),
                   "generator_digest": digest, "generator_version": ORGANIZER_VERSION, "validator_version": VALIDATOR_VERSION,
                   "mesh_digest": mesh_digest, "mesh_sha256": hashes,
                   "library": {"document_id": library.did, "version_id": library.vid, "url": library.url(), "digest": library.digest},
                   "elements": {"assemblies": robot.assemblies, "studios": {n: s.element_id for n, s in robot.studios.items()},
                                "variable_studio": robot.variable_studio,
                                "library_studios": {n: s.element_id for n, s in library.studios.items()}}},
        "parts": parts,
        "fastener_owner": {name: part.get("owner") for name, part in parts.items() if part.get("kind") == "fastener"},
        "fit": report.get("fit", {}),
        "kinematics": {"steering": report.get("kinematics", {})},
        "rf": report.get("rf", {}),
        "assembly": report.get("assembly", {}),
        "organization": {"organize": report.get("organize"), "check": report.get("organization_check")},
        "images": report.get("images", []),
        "build_seconds": report.get("seconds"),
    }
    path = project.build_dir("cad") / "cad_model.json"
    path.write_text(json.dumps(_json_safe(model), indent=1, default=str))
    return path


def organize_pinned(project: Project) -> dict[str, Any]:
    """Re-run the browser-native organization and the organization check on the
    pinned document (e.g. after Onshape rate-limited the browser session during the
    build), and record the result in ``cad_model.json``."""
    from .bridge import connect
    from .client import default_transport

    api = default_transport()
    bridge = connect()
    if api is None or bridge is None:
        raise SystemExit("needs an Onshape transport and the onshape-native bridge")
    result = StageResult(stage="cad")
    parts, instances = build_part_catalog(project)
    plan = plan_assembly(project, parts, instances)
    _, bindings = robot_variables(project)
    library = ensure_library(api, project, parts, result)
    state_path = project.build_dir("cad") / "build_state.json"
    saved = json.loads(state_path.read_text())["robot"]
    robot = DocumentInfo(did=saved["did"], wid=saved["wid"], name=saved["name"])
    builder = AssemblyBuilder(api, project, robot, library, parts, plan, result)
    _load_state(state_path, robot, builder)
    library_fresh = not (project.lock.get("cad_library") or {}).get("organized")
    organize_report = organize(bridge, robot, library, plan, result, library_fresh, builder)
    if library_fresh and not [e for e in organize_report["errors"] if library.name in e]:
        project.write_lock("cad_library", {**(project.lock.get("cad_library") or {}), "organized": True})
    check = organization_check(api, bridge, robot, library, parts, bindings, result, organize_report)
    path = project.build_dir("cad") / "cad_model.json"
    if path.exists():
        model = json.loads(path.read_text())
        model["organization"] = {"organize": organize_report, "check": check,
                                 "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics]}
        path.write_text(json.dumps(model, indent=1, default=str))
    return {"organize": organize_report, "check": check, "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics]}


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "organize":
        print("usage: python -m r3_onshape.organized organize [project]")
        return 2
    project = Project.load(argv[1] if len(argv) > 1 else "examples/robots/rc-car")
    out = organize_pinned(project)
    errors = [d for d in out["diagnostics"] if d["severity"] == "error"]
    for d in out["diagnostics"]:
        if d["severity"] != "info":
            print(d["severity"], d["code"], d["message"][:200])
    print(f"{len(errors)} organization errors")
    return 1 if errors else 0


__all__ = ["AssemblyBuilder", "AssemblyPlan", "DEFAULT_NAME", "DocumentInfo", "MatePlan", "Placement", "TOP_LEVEL", "compose_world",
           "ensure_library", "expected_world", "fetch_local_geometry", "input_digest", "local_mass", "organization_check", "organize",
           "plan_assembly", "render_views", "robot_variables", "sync_organized", "transform_part", "write_model"]


if __name__ == "__main__":
    raise SystemExit(main())
