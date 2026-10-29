"""Parts of the robot in their own frames, as an engineer models them in Onshape.

Every object type becomes a Part Studio modeled at the object's own origin
(the catalog frame), with one named mate connector per interface. Instances
are placed only in Assemblies, at the poses the assembly compiler derived from
the joins. This module turns the spec + catalog into those local parts:

* :class:`LocalPart`: one Onshape part (a geometry variant of a type: left or
  mirrored right hand, or an instance-specific outline/size), its ordered
  standard operations (grouped into feature folders), its mate connectors and
  its part properties;
* :func:`build_part_catalog`: every part, which Part Studio and document
  (robot document or the reusable parts library) it lives in, and which
  component or fastener instances use it;
* :func:`instance_poses`: the expected robot-frame transform of every
  instance (compiled poses; fasteners from their seat frames).

Fabricated parts get holes, bores, sockets and strap slots from their catalog
interfaces (the joins they serve), so their geometry cannot disagree with the
assembly. Purchased bank models bypass native solid authoring; catalog solids
remain offline proxy plans and explicit missing-model fallbacks.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.assembly import FLIP, ResolvedInterface, frame_matrix, invert, mirrored_interface
from r3_model.project import Project
from r3_model.units import to_si

from .geometry import Hole, Loop, PartPlan, Plane, Prism, Revolve, unit
from .plan import PlanError, _feature_ops, _Frame, _solid_ops, _envelope_solids, cylinder_op, d_op, sphere_op

TIP_CLEARANCE = 1.5e-3  # drill depth beyond the thread in blind tapped holes

FOLDERS = ("Reference geometry", "Base shape", "Mounting holes", "Bores & shafts", "Cutouts", "Mirror", "Mate connectors")


@dataclass
class ConnectorSpec:
    """A named Part Studio mate connector in the part's frame."""

    name: str
    frame: np.ndarray  # 4x4 local
    interface: str
    hole: int | None = None  # far-face seat of hole <n> (screw head / nut bearing face)


@dataclass
class LocalPart:
    key: str
    name: str
    studio: str
    type: str
    plan: PartPlan
    connectors: list[ConnectorSpec] = field(default_factory=list)
    library: bool = False
    mirror: list[float] | None = None
    mirror_of: str | None = None  # key of the part this one is a Mirror feature of
    properties: dict[str, str] = field(default_factory=dict)
    instances: list[str] = field(default_factory=list)
    bank_model: dict[str, Any] | None = None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _color(hex_color: str) -> str:
    return hex_color if hex_color.startswith("#") and len(hex_color) == 7 else "#888888"


def cad_name(ctype: Any) -> str:
    return ctype.cad_name or ctype.name.split(",")[0].strip()


# ------------------------------------------------------------------ operations
def _folder_for(op: Any, source: str) -> str:
    if source == "solid":
        return "Base shape" if getattr(op, "op", "add") != "remove" else "Cutouts"
    if isinstance(op, Hole):
        return "Mounting holes"
    if getattr(op, "op", "add") == "add":
        return "Bores & shafts"
    return "Bores & shafts" if source == "bore" else "Cutouts"


def _label(op: Any, text: str) -> None:
    op.name = text


def _interface_ops(project: Project, iface: ResolvedInterface) -> list[Any]:
    """Holes, bores, sockets and slots a fabricated part needs for one interface."""
    frame = iface.frame
    origin, x_axis, y_axis, z_axis = frame[:3, 3], frame[:3, 0], frame[:3, 1], frame[:3, 2]
    ops: list[Any] = []
    if iface.kind in ("hole_pattern", "face_mount"):
        groups: dict[tuple, list] = {}
        for hole in iface.holes:
            if hole.kind == "thread":
                continue  # a stud on this part (modeled by its solids)
            point = origin + x_axis * hole.x + y_axis * hole.y
            nominal = f"M{hole.d * 1e3:g}" if hole.kind == "tapped" else ""
            if hole.kind == "clearance":
                screw = _nearest_screw(project, hole.d)
                op = Hole(point=point, axis=-z_axis, diameter=hole.d, depth=None, style="clearance",
                          thread=screw.thread if screw else "", fit="Normal")
            else:
                screw = project.catalog.fastener(f"shcs-m{hole.d * 1e3:g}")
                through = bool(iface.params.get("through_thread"))
                op = Hole(point=point, axis=-z_axis, diameter=screw.tap_drill, depth=None if through else hole.depth + TIP_CLEARANCE,
                          style="tapped", thread=nominal, pitch=screw.pitch, tapped_depth=None if through else hole.depth)
            groups.setdefault((hole.kind, round(hole.d, 9), round(hole.depth, 9)), []).append(op)
        for (kind, d, depth), group in groups.items():
            count = f"{len(group)}x " if len(group) > 1 else ""
            if kind == "clearance":
                text = f"{iface.name}: {count}{group[0].thread or 'Ø'} clearance Ø{d * 1e3:g}"
            else:
                text = f"{iface.name}: {count}{group[0].thread} tapped {depth * 1e3:g} mm"
            for op in group:
                _label(op, text)
                op.folder = "Mounting holes"
                ops.append(op)
        return ops
    if iface.kind in ("bore", "spline") and (iface.kind == "bore" or iface.params.get("gender") == "female"):
        d = iface.quantity("d")
        depth = iface.quantity("depth")
        flat = iface.quantity("flat_offset")
        if flat is not None:
            op = d_op(origin, -z_axis, d, flat, x_axis, depth, "remove", "")
            text = f"{iface.name}: D-bore Ø{d * 1e3:g} flat {flat * 1e3:g} mm"
        else:
            op = cylinder_op(origin, -z_axis, d, depth, "remove", "")
            text = f"{iface.name}: {'spline bore' if iface.kind == 'spline' else 'bore'} Ø{d * 1e3:g}"
        _label(op, text)
        op.folder = "Bores & shafts"
        return [op]
    if iface.kind == "ball_socket":
        d = iface.quantity("d")
        opening = iface.quantity("opening", d * 0.7)
        socket = sphere_op(origin, d, "remove", f"{iface.name}: ball socket Ø{d * 1e3:g}")
        neck = cylinder_op(origin, z_axis, opening, 0.02, "remove", f"{iface.name}: socket opening Ø{opening * 1e3:g}")
        socket.folder = neck.folder = "Bores & shafts"
        return [socket, neck]
    if iface.kind == "strap_slot" and iface.params.get("slots"):
        out = []
        plane = Plane.through(origin, z_axis)
        for index, rect in enumerate(iface.params["slots"]):
            x0, y0, x1, y1 = (to_si(v) for v in rect)
            corners = [origin + x_axis * u + y_axis * v for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            loop = Loop.polygon([plane.to2d(c) for c in corners])
            out.append(Prism(plane, [loop], None, float(np.sign(-z_axis @ plane.normal)), "remove",
                             f"{iface.name}: slot {index + 1} {abs(x1 - x0) * 1e3:g} x {abs(y1 - y0) * 1e3:g}", folder="Cutouts"))
        return out
    return []


def _nearest_screw(project: Project, hole_d: float):
    best = None
    for fastener in project.catalog.fasteners.values():
        if fastener.kind != "socket_head_screw" or fastener.d >= hole_d:
            continue
        if best is None or fastener.d > best.d:
            best = fastener
    return best


def _resolved_interfaces(project: Project, type_id: str, mirror: list[float] | None) -> list[ResolvedInterface]:
    ctype = project.catalog.component(type_id)
    out = []
    for name, spec in ctype.interfaces.items():
        iface = ResolvedInterface("", name, spec.kind, frame_matrix(spec.origin, spec.z, spec.x), dict(spec.params),
                                  ctype.mechanical.material or "")
        out.append(mirrored_interface(iface, mirror) if mirror else iface)
    return out


def component_ops(project: Project, type_id: str, params: dict[str, Any], mirror: list[float] | None) -> list[Any]:
    """Ordered operations of one component variant in its own (mirrored) frame."""
    from r3_catalog.schema import Solid

    ctype = project.catalog.component(type_id)
    mech = ctype.mechanical
    frame = _Frame(np.diag(mirror) if mirror else np.eye(3), np.zeros(3))
    extra = [Solid.model_validate(s) for s in params.get("solids", [])]
    solids = list(mech.solids) if mech.solids else ([] if extra else _envelope_solids(mech, params))
    ops: list[Any] = []
    for solid in [*solids, *extra]:
        for op in _solid_ops(frame, solid, params):
            op.folder = _folder_for(op, "solid")
            op.name = solid.name or solid.kind
            ops.append(op)
    for feature in mech.features:
        for op in _feature_ops(frame, feature, project.catalog):
            op.folder = _folder_for(op, "bore" if feature.kind in ("bore", "d_bore", "shaft", "d_shaft", "spline") else "feature")
            op.name = feature.id.replace("_", " ")
            ops.append(op)
    if mech.fabricated:
        for iface in _resolved_interfaces(project, type_id, mirror):
            ops.extend(_interface_ops(project, iface))
    if not ops or getattr(ops[0], "op", "add") == "remove" or isinstance(ops[0], Hole):
        raise PlanError(f"{type_id}: first operation must add material")
    return ops


def interface_connectors(project: Project, type_id: str, mirror: list[float] | None) -> list[ConnectorSpec]:
    """``mc_<interface>`` at every interface frame, plus ``mc_<interface>_h<n>``
    at the far face of every clearance hole (screw-head / nut seats)."""
    out = []
    for iface in _resolved_interfaces(project, type_id, mirror):
        out.append(ConnectorSpec(f"mc_{iface.name}", iface.frame, iface.name))
        if iface.kind not in ("hole_pattern", "face_mount"):
            continue
        for index, hole in enumerate(iface.holes):
            if hole.kind != "clearance":
                continue
            seat = iface.frame @ np.array([[1, 0, 0, hole.x], [0, 1, 0, hole.y], [0, 0, 1, -hole.depth], [0, 0, 0, 1.0]]) @ FLIP
            out.append(ConnectorSpec(f"mc_{iface.name}_h{index + 1}", seat, iface.name, index))
    return out


# ------------------------------------------------------------------ fasteners
def fastener_ops(ftype: Any, length: float | None) -> list[Any]:
    """A fastener in its own frame: seat (under the head / nut bearing face) at the
    origin; the shank or nut body runs along -z (insertion direction)."""
    from .plan import _hexagon

    seat = np.zeros(3)
    down = np.array([0.0, 0.0, -1.0])
    if ftype.kind in ("socket_head_screw", "button_head_screw"):
        k, dk, d = ftype.head_height, ftype.head_diameter, ftype.d
        profile = [(0.0, -k), (dk / 2, -k), (dk / 2, 0.0), (d / 2, 0.0), (d / 2, length), (0.0, length)]
        return [Revolve(seat, down, profile=profile, name=f"{ftype.thread} x {length * 1e3:g} screw", folder="Base shape")]
    if ftype.kind == "nut":
        plane = Plane.through(seat, down)
        loops = [_hexagon(plane, seat, ftype.across_flats), Loop.circle(plane.to2d(seat), ftype.tap_drill / 2)]
        return [Prism(plane, loops, ftype.height, float(np.sign(down @ plane.normal)), "add", f"{ftype.thread} hex nut",
                      folder="Base shape")]
    raise PlanError(f"fastener kind {ftype.kind} is modeled as an assembly object, not a plain fastener part")


def fastener_connector() -> ConnectorSpec:
    """``mc_seat``: z out of the seat into the hole (opposes the hole's far-face connector)."""
    return ConnectorSpec("mc_seat", frame_matrix([0.0, 0.0, 0.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]), "seat")


# ------------------------------------------------------------------ catalog of parts
def _variant_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if k in ("outline", "size", "solids")}


def _suffix(params: dict[str, Any]) -> str:
    if "size" in params:
        size = [to_si(v) * 1e3 for v in params["size"][:2]]
        return " " + "x".join(f"{v:g}" for v in size)
    return ""


def build_part_catalog(project: Project) -> tuple[dict[str, LocalPart], dict[str, str]]:
    """(parts by key, instance id -> part key) for every component and plain fastener."""
    spec, catalog = project.spec, project.catalog
    parts: dict[str, LocalPart] = {}
    instance_part: dict[str, str] = {}
    from .aliases import geometry_type

    mirrored = {geometry_type(project, c.type) for c in spec.components.values() if c.params.get("mirror")}
    plain = {geometry_type(project, c.type) for c in spec.components.values() if not c.params.get("mirror")}
    mirrored_types = mirrored & plain  # types used in both hands get "L"/"R" names
    for cid, comp in spec.components.items():
        type_id = geometry_type(project, comp.type)
        ctype = catalog.component(type_id)
        mirror = [float(v) for v in comp.params["mirror"]] if comp.params.get("mirror") else None
        variant = _variant_params(comp.params)
        key = f"{type_id}{'#R' if mirror else ''}{'@' + _digest(variant) if variant else ''}"
        if key not in parts:
            base = cad_name(ctype)
            hand = (" R" if mirror else " L") if type_id in mirrored_types else ""
            name = f"{base}{hand}{_suffix(comp.params)}"
            mech = ctype.mechanical
            plan = PartPlan(name=name, kind="fabricated" if mech.fabricated else "purchased",
                            material=mech.material or ctype.name, density=float(mech.density or 0.0),
                            mass=None if mech.fabricated else float(mech.mass), color=_color(mech.color),
                            ops=component_ops(project, type_id, comp.params, mirror), owner=cid,
                            mass_source="geometry" if mech.fabricated else "catalog")
            if mech.fabricated and not plan.density:
                raise PlanError(f"{comp.type}: fabricated part needs a material density")
            props = {"part_number": ctype.part_number or type_id, "vendor": ctype.manufacturer or ("R3 (fabricated)" if mech.fabricated else ""),
                     "material": mech.material or "", "mass_source": "geometry x density" if mech.fabricated else
                     f"catalog {mech.mass * 1e3:.2f} g ({mech.provenance.status})", "catalog_type": type_id}
            parts[key] = LocalPart(key=key, name=name, studio=ctype.cad_family or cad_name(ctype), type=type_id, plan=plan,
                                   connectors=interface_connectors(project, type_id, mirror),
                                   library=not mech.fabricated and ctype.kind != "pcb", mirror=mirror, properties=props)
        if not ctype.mechanical.fabricated and ctype.kind != "pcb":
            from .purchased import bank_entry
            entry = bank_entry(ctype)
            if entry and entry.available:
                parts[key].bank_model = entry.model_dump(mode="json")
                parts[key].studio = parts[key].name
                parts[key].properties["model_bank_id"] = entry.id
                parts[key].properties["model_bank_digest"] = _digest(parts[key].bank_model)
        parts[key].instances.append(cid)
        instance_part[cid] = key
    for key, part in parts.items():
        if part.mirror:
            left = next((k for k, p in parts.items() if p.type == part.type and not p.mirror and
                         k.split("@")[1:] == key.split("@")[1:]), None)
            part.mirror_of = left
    for fastener in spec.mechanical.fasteners:
        if fastener.body:
            continue
        for type_id, length, iid in ((fastener.type, fastener.length, fastener.part), (fastener.nut, None, fastener.nut_part)):
            if not type_id or not iid:
                continue
            ftype = catalog.fastener(type_id)
            key = f"{type_id}x{length * 1e3:g}" if length else type_id
            if key not in parts:
                label = f"SHCS {ftype.thread}x{length * 1e3:g}" if length else f"Nyloc nut {ftype.thread}"
                family = f"Socket head screws {ftype.thread}" if length else f"Nuts {ftype.thread}"
                plan = PartPlan(name=label, kind="fastener", material=ftype.material, density=float(ftype.density),
                                color=_color(ftype.color), ops=fastener_ops(ftype, length), owner=fastener.thread_into or "",
                                mass_source="geometry")
                parts[key] = LocalPart(key=key, name=label, studio=family, type=type_id, plan=plan,
                                       connectors=[fastener_connector()], library=True,
                                       properties={"part_number": f"{ftype.standard or ftype.id} {ftype.thread}"
                                                   + (f"x{length * 1e3:g}" if length else ""), "vendor": "",
                                                   "material": ftype.material, "mass_source": "geometry x density",
                                                   "catalog_type": type_id})
            from r3_catalog.modelbank import ModelBank
            from .purchased import fastener_model_id
            bank = ModelBank()
            entry = next((e for e in bank.entries() if e.id == fastener_model_id(type_id, length)), None)
            if entry and entry.available:
                parts[key].bank_model = entry.model_dump(mode="json")
                parts[key].studio = parts[key].name
                parts[key].plan.mass = entry.key_specs["mass"].value if "mass" in entry.key_specs else None
                parts[key].properties["model_bank_id"] = entry.id
                parts[key].properties["model_bank_digest"] = _digest(parts[key].bank_model)
            parts[key].instances.append(iid)
            instance_part[iid] = key
    return parts, instance_part


def instance_poses(project: Project) -> dict[str, np.ndarray]:
    """Expected robot-frame transform of every component and plain-fastener part."""
    from r3_model.assembly import pose_matrix

    poses = {cid: pose_matrix(c.pose.xyz, c.pose.rpy) for cid, c in project.spec.components.items()}
    for fastener in project.spec.mechanical.fasteners:
        if fastener.body:
            continue
        seat = np.asarray(fastener.position, dtype=float)
        axis = unit(fastener.axis)
        helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        x = unit(np.cross(helper, axis))
        poses[fastener.part] = frame_matrix(seat, -axis, x)
        if fastener.nut_part:
            # The nut body runs along its local -z, away from the stack (+axis).
            poses[fastener.nut_part] = frame_matrix(seat + axis * float(fastener.grip), -axis, x)
    return poses


def world_manifold(part: LocalPart, pose: np.ndarray):
    """The part's plan solid moved to ``pose`` (offline exact-interference checks)."""
    from .geometry import plan_manifold

    return plan_manifold(part.plan).transform(np.asarray(pose)[:3, :4])


__all__ = ["FOLDERS", "ConnectorSpec", "LocalPart", "build_part_catalog", "component_ops", "fastener_connector", "fastener_ops",
           "instance_poses", "interface_connectors", "world_manifold"]
