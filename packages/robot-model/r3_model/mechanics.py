"""Derive the exact-geometry mechanical model from compiled joins.

The assembly compiler (:mod:`r3_model.assembly`) places objects from their
interfaces. The CAD fit checks (``r3_onshape.fit``), the motion sweep and the
simulator still work on robot-frame ``mechanical.fasteners`` and
``mechanical.interfaces``. This module writes those from the joins, so no
robot-frame coordinate is authored twice:

* every matched hole pair of a ``fasten`` join with ``fasteners`` becomes one
  screw (head on the far face of the clearance side, threading into the tapped
  side) or one through bolt with a nut;
* a ``thread`` hole on an object (a ball stud, kingpin screw or stacking
  standoff modeled as an object) paired with a tapped hole becomes a fastener
  whose body is that object; a nut object on a ``thread`` stud likewise;
* shaft/bore, ball, spline, header, adhesive and strap joins become the fit
  interfaces of the same id (kinds ``revolute_fit``, ``press_fit``,
  ``drive_flat``, ``spline``, ``ball_joint``, ``connector_retention``,
  ``adhesive``, ``strap``) with the join's ``verify`` limits;
* derived joints get their interface, damping and friction from the join.

Authored ``mechanical.interfaces`` with ``frame: <component>`` (e.g. a
magnetic encoder target) are moved into the robot frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from .diagnostics import Diagnostic
from .spec import Fastener, Interface, Join, RobotSpec
from .units import to_si

if TYPE_CHECKING:
    from r3_catalog.schema import Catalog

    from .assembly import AssemblyResult, ResolvedInterface

PLANAR = {"face_mount", "hole_pattern", "adhesive_pad"}


def _world(result: "AssemblyResult", iface: "ResolvedInterface") -> np.ndarray:
    return result.poses[iface.obj] @ iface.frame


def _point(frame: np.ndarray, x: float, y: float, z: float = 0.0) -> list[float]:
    return (frame[:3, 3] + frame[:3, 0] * x + frame[:3, 1] * y + frame[:3, 2] * z).tolist()


def _screw_type(catalog: "Catalog", join: Join, nominal: float) -> str:
    if join.fasteners and join.fasteners.part:
        return join.fasteners.part
    candidate = f"shcs-m{nominal * 1e3:g}"
    if candidate not in catalog.fasteners:
        raise KeyError(f"join {join.join_id}: no catalog screw for M{nominal * 1e3:g}; set fasteners.part")
    return candidate


def _fasteners_for_join(spec: RobotSpec, catalog: "Catalog", result: "AssemblyResult", join: Join,
                        a: "ResolvedInterface", b: "ResolvedInterface") -> list[Fastener]:
    from .assembly import parse_fastener

    match = result.matches.get(join.join_id)
    if match is None:
        return []
    holes_a, holes_b = a.holes, b.holes
    wa, wb = _world(result, a), _world(result, b)
    spec_f = join.fasteners
    out: list[Fastener] = []
    for i, j, _ in match.pairs:
        ha, hb = holes_a[i], holes_b[j]
        sides = {"a": (ha, a, wa), "b": (hb, b, wb)}
        kinds = {ha.kind, hb.kind}
        extra = list(spec_f.clamps) if spec_f else []
        if kinds == {"clearance", "tapped"} and spec_f is not None:
            (hc, ic, wc), (ht, it, _) = (sides["a"], sides["b"]) if ha.kind == "clearance" else (sides["b"], sides["a"])
            parsed = parse_fastener(spec_f.type)
            nominal, length = parsed if parsed else (ht.d, None)
            out.append(Fastener(id="", type=_screw_type(catalog, join, nominal), length=length,
                                position=_point(wc, hc.x, hc.y, -hc.depth), axis=wc[:3, 2].tolist(),
                                clamps=[ic.obj, *extra], thread_into=it.obj, preloaded=spec_f.preloaded, join=join.join_id))
        elif kinds == {"clearance"} and spec_f is not None:
            head, other = ("a", "b") if spec_f.head == "a" else ("b", "a")
            (hh, ih, wh), (ho, io, _) = sides[head], sides[other]
            parsed = parse_fastener(spec_f.type)
            nominal, length = parsed if parsed else (hh.d, None)
            out.append(Fastener(id="", type=_screw_type(catalog, join, nominal), length=length,
                                position=_point(wh, hh.x, hh.y, -hh.depth), axis=wh[:3, 2].tolist(),
                                clamps=[ih.obj, io.obj, *extra], nut=spec_f.nut, grip=hh.depth + ho.depth + join.offset,
                                preloaded=spec_f.preloaded, join=join.join_id))
        elif "thread" in kinds and kinds <= {"thread", "tapped"} and len(kinds) == 2:
            (hs, is_, ws), (hf, if_, wf) = (sides["a"], sides["b"]) if ha.kind == "thread" else (sides["b"], sides["a"])
            stud_type = is_.params.get("fastener")
            nut_type = if_.params.get("fastener")
            nut = nut_type is not None and catalog.fastener(nut_type).kind == "nut"
            preloaded = spec_f.preloaded if spec_f else bool((if_ if nut else is_).params.get("preloaded", True))
            if nut:
                # A nut object on a male thread: it bears on the stack along its seat's outward normal.
                out.append(Fastener(id="", type=nut_type, position=_point(wf, hf.x, hf.y), axis=wf[:3, 2].tolist(),
                                    clamps=extra, thread_into=is_.obj, body=if_.obj, preloaded=preloaded, join=join.join_id))
            elif stud_type is not None:
                # A stud on an object (ball stud, kingpin screw, stacking standoff): seat at the stud's shoulder plane,
                # inserted along the seat's outward normal (into the tapped part).
                out.append(Fastener(id="", type=stud_type, length=hs.depth, position=_point(ws, hs.x, hs.y),
                                    axis=ws[:3, 2].tolist(), clamps=extra, thread_into=if_.obj, body=is_.obj,
                                    preloaded=preloaded, join=join.join_id))
    for n, fastener in enumerate(out):
        fastener.id = join.join_id if len(out) == 1 else f"{join.join_id}_{n + 1}"
    for fastener in out:
        if spec_f:
            fastener.torque = spec_f.torque
            fastener.locking = spec_f.locking
    return out


def _shaft_bore_interface(join: Join, a: "ResolvedInterface", b: "ResolvedInterface", result: "AssemblyResult") -> Interface | None:
    shaft, bore = (a, b) if a.kind == "shaft" else (b, a)
    wb = _world(result, bore)
    params = dict(join.verify)
    override = params.pop("kind", None)
    splined = "teeth" in shaft.params or "teeth" in bore.params
    if override == "adhesive":
        params.setdefault("min_fraction", 0.8)
        return Interface(id=join.join_id, kind="adhesive", parts=[shaft.obj, bore.obj], params=params, join=join.join_id)
    if override:
        kind = override
    elif splined:
        kind = "spline"
    elif join.kind == "drive_flat":
        kind = "drive_flat"
    elif join.kind in ("revolute", "slider"):
        kind = "revolute_fit"
    elif join.fit == "interference" or (join.fit is None and (bore.quantity("d", 0.0) or 0.0) < (shaft.quantity("d", 0.0) or 0.0)):
        kind = "press_fit"
    else:
        return None  # a clearance slip fit retained by a clamp stack (e.g. a spacer tube): no attachment of its own
    if kind == "drive_flat" and "flat_offset" not in params and shaft.params.get("flat_offset") is not None:
        params["flat_offset"] = shaft.params["flat_offset"]
    if "min_engagement" not in params:
        lengths = [v for v in (shaft.quantity("length"), bore.quantity("depth")) if v]
        if lengths:
            params["min_engagement"] = 0.9 * min(lengths)
    return Interface(id=join.join_id, kind=kind, parts=[shaft.obj, bore.obj], axis_point=wb[:3, 3].tolist(),
                     axis_dir=(-wb[:3, 2]).tolist(), params=params, join=join.join_id, rated_internal=join.rated_internal)


def _interface_for_join(join: Join, a: "ResolvedInterface", b: "ResolvedInterface", result: "AssemblyResult") -> Interface | None:
    kinds = {a.kind, b.kind}
    params = dict(join.verify)
    if kinds == {"shaft", "bore"}:
        return _shaft_bore_interface(join, a, b, result)
    if kinds == {"ball_stud", "ball_socket"}:
        stud, socket = (a, b) if a.kind == "ball_stud" else (b, a)
        ws = _world(result, stud)
        return Interface(id=join.join_id, kind="ball_joint", parts=[stud.obj, socket.obj], axis_point=ws[:3, 3].tolist(),
                         axis_dir=ws[:3, 2].tolist(), params=params, join=join.join_id)
    if kinds == {"spline"}:
        male, female = (a, b) if a.params.get("gender") == "male" else (b, a)
        wf = _world(result, female)
        return Interface(id=join.join_id, kind="spline", parts=[male.obj, female.obj], axis_point=wf[:3, 3].tolist(),
                         axis_dir=(-wf[:3, 2]).tolist(), params=params, join=join.join_id)
    if kinds == {"header", "socket"}:
        header, socket = (a, b) if a.kind == "header" else (b, a)
        wh = _world(result, header)
        ws = _world(result, socket)
        # Insertion starts at the socket mouth, including a deliberate axial
        # seating gap; measuring from the header plastic overstates engagement.
        return Interface(id=join.join_id, kind="connector_retention", parts=[header.obj, socket.obj],
                         axis_point=ws[:3, 3].tolist(), axis_dir=wh[:3, 2].tolist(), params=params, join=join.join_id)
    if kinds == {"strap_seat", "strap_slot"}:
        if not params.get("strap"):
            return None  # only the join that names the strap carries the strap check
        return Interface(id=join.join_id, kind="strap", parts=[params.pop("strap"), params.pop("around")], params=params,
                         join=join.join_id)
    if "adhesive_pad" in kinds and kinds <= PLANAR:
        params.setdefault("min_fraction", 0.8)
        wb = _world(result, b)
        return Interface(id=join.join_id, kind="adhesive", parts=[b.obj, a.obj], params=params, join=join.join_id,
                         axis_point=wb[:3, 3].tolist(), axis_dir=wb[:3, 2].tolist())
    return None


def _resolve_frame(interface: Interface, result: "AssemblyResult") -> Interface:
    if interface.frame is None:
        return interface
    pose = result.poses[interface.frame]
    data = interface.model_dump()
    if interface.axis_point is not None:
        data["axis_point"] = (pose[:3, :3] @ np.asarray(interface.axis_point, dtype=float) + pose[:3, 3]).tolist()
    if interface.axis_dir is not None:
        data["axis_dir"] = (pose[:3, :3] @ np.asarray(interface.axis_dir, dtype=float)).tolist()
    data["frame"] = None
    return Interface.model_validate(data)


def derive_mechanical(spec: RobotSpec, catalog: "Catalog", result: "AssemblyResult") -> list[Diagnostic]:
    """Append join-derived fasteners/interfaces to ``spec.mechanical`` and
    complete the derived joints. Returns diagnostics (never raises)."""
    diagnostics: list[Diagnostic] = []
    mech = spec.mechanical
    joins = {join.join_id: join for join in spec.assembly.joins} if spec.assembly else {}
    mech.interfaces = [_resolve_frame(i, result) if i.frame else i for i in mech.interfaces]
    authored_fasteners = {f.id for f in mech.fasteners}
    authored_interfaces = {i.id for i in mech.interfaces}
    for join_id, (a, b) in result.valid.items():
        join = joins[join_id]
        if a.obj not in result.poses or b.obj not in result.poses:
            continue
        try:
            if {a.kind, b.kind} <= PLANAR:
                for fastener in _fasteners_for_join(spec, catalog, result, join, a, b):
                    if fastener.id not in authored_fasteners:
                        mech.fasteners.append(fastener)
            interface = _interface_for_join(join, a, b, result)
        except (KeyError, ValueError) as error:
            diagnostics.append(Diagnostic.error("mechanics_derivation_failed", f"join {join_id}: {error}", subjects=[join_id],
                                                data={"join": join_id}, stage="assembly"))
            continue
        if interface is not None and interface.id not in authored_interfaces:
            mech.interfaces.append(interface)
    interfaces = {i.id for i in mech.interfaces}
    for joint in mech.joints:
        if joint.type == "continuous" and joint.axis:
            # A spin axis has no preferred sense, but mirrored interfaces give mirrored ones (left +y, right -y):
            # point every continuous axis along its dominant positive robot axis so a differential's joints agree.
            dominant = max(range(3), key=lambda k: abs(joint.axis[k]))
            if joint.axis[dominant] < 0:
                joint.axis = [-v for v in joint.axis]
        join = joins.get(joint.interface or "") or next((j for j in joins.values() if j.joint == joint.id), None)
        if join is None:
            continue
        if joint.interface is None and join.join_id in interfaces:
            joint.interface = join.join_id
        if not joint.damping and join.damping:
            joint.damping = float(to_si(join.damping))
        if not joint.friction and join.friction:
            joint.friction = float(to_si(join.friction))
    try:
        spec.validate_mechanical()
    except ValueError as error:
        diagnostics.append(Diagnostic.error("mechanics_invalid", f"derived mechanical model: {error}", subjects=["mechanical"],
                                            data={"fasteners": len(mech.fasteners), "interfaces": len(mech.interfaces)},
                                            stage="assembly"))
    return diagnostics


def fastener_count(spec: RobotSpec) -> dict[str, int]:
    """Summary used by reports: derived fasteners per catalog type."""
    counts: dict[str, int] = {}
    for fastener in spec.mechanical.fasteners:
        counts[fastener.type] = counts.get(fastener.type, 0) + 1
    return counts


__all__ = ["derive_mechanical", "fastener_count"]
