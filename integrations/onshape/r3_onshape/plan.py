"""Turn ``robot.yaml`` + catalog into part plans (see :mod:`r3_onshape.geometry`).

Rules (fit-verification brief, section 3):

* Purchased parts are their catalog envelope plus ONLY their catalog
  ``features`` (holes, bores, shafts, splines...). Nothing is cut into them from
  the spec, so a misplaced fastener collides with them and the fit checks see it.
* Fabricated parts get their holes and bores from the fasteners and interfaces
  that use them: ISO 273 normal clearance holes through clamped parts, tapped
  holes (tap drill) in ``thread_into`` parts, the ``bore`` of interfaces whose
  bore side they are, and strap slots.
* Every fastener, nut, strap and magnet is its own named part.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from r3_model.graph import rpy_matrix
from r3_model.project import Project
from r3_model.spec import Fastener

from .geometry import Hole, Loop, PartPlan, Plane, Prism, Revolve, axis_base, unit

SCREW_KINDS = ("socket_head_screw", "button_head_screw", "ball_stud", "standoff_mf")
TIP_CLEARANCE = 1.5e-3  # drill depth beyond the screw tip in tapped holes


class PlanError(ValueError):
    pass


def _color(hex_color: str) -> str:
    return hex_color if hex_color.startswith("#") and len(hex_color) == 7 else "#888888"


def component_frame(project: Project, cid: str) -> tuple[np.ndarray, np.ndarray]:
    """Linear map (rotation x mirror) and translation of a component frame."""
    comp = project.spec.components[cid]
    mirror = np.diag([float(v) for v in comp.params.get("mirror", [1, 1, 1])])
    rotation = rpy_matrix(comp.pose.rpy)
    return rotation @ mirror, np.asarray(comp.pose.xyz, dtype=float)


class _Frame:
    """Maps component-frame geometry into robot-frame operations."""

    def __init__(self, linear: np.ndarray, origin: np.ndarray) -> None:
        self.linear = linear
        self.origin = origin

    def point(self, p: Any) -> np.ndarray:
        return self.linear @ np.asarray(p, dtype=float) + self.origin

    def vector(self, v: Any) -> np.ndarray:
        return self.linear @ np.asarray(v, dtype=float)

    def plane_loop(self, plane: Plane, local_points: list[np.ndarray]) -> Loop:
        return Loop.polygon([plane.to2d(self.point(p)) for p in local_points])


def _direction(plane: Plane, axis: np.ndarray) -> float:
    return float(np.sign(unit(axis) @ plane.normal))


def _in_plane_angle(plane: Plane, vector: np.ndarray) -> float:
    uv = plane.to2d(vector) - plane.to2d(np.zeros(3))
    return math.atan2(uv[1], uv[0])


def cylinder_op(base: np.ndarray, axis: np.ndarray, diameter: float, length: float | None, op: str, name: str) -> Prism:
    plane = Plane.through(base, axis)
    return Prism(plane, [Loop.circle(plane.to2d(base), diameter / 2)], length, _direction(plane, axis), op, name)  # type: ignore[arg-type]


def d_op(base: np.ndarray, axis: np.ndarray, diameter: float, flat_offset: float, flat_normal: np.ndarray, length: float,
         op: str, name: str) -> Prism:
    plane = Plane.through(base, axis)
    loop = Loop.d_shape(plane.to2d(base), diameter / 2, flat_offset, _in_plane_angle(plane, flat_normal))
    return Prism(plane, [loop], length, _direction(plane, axis), op, name)  # type: ignore[arg-type]


def sphere_op(center: np.ndarray, diameter: float, op: str, name: str) -> Revolve:
    return Revolve(point=center, axis=np.array([0.0, 0.0, 1.0]),
                   profile_loop=Loop.half_disc((0.0, 0.0), diameter / 2), op=op, name=name)  # type: ignore[arg-type]


# ------------------------------------------------------------------ components
def _solid_ops(frame: _Frame, solid: Any, params: dict[str, Any]) -> list:
    op = "remove" if solid.op == "remove" else "add"
    name = solid.name or solid.kind
    if solid.kind == "box":
        center = np.asarray(solid.position, dtype=float)
        size = np.asarray(solid.size, dtype=float)
        for k in (2, 0, 1):
            world_axis = frame.vector(np.eye(3)[k])
            try:
                axis_base(world_axis)
            except ValueError:
                continue
            i, j = [n for n in range(3) if n != k]
            e = np.eye(3)
            bottom = center - e[k] * size[k] / 2
            corners = [bottom + si * e[i] * size[i] / 2 + sj * e[j] * size[j] / 2 for si, sj in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            plane = Plane.through(frame.point(bottom), world_axis)
            return [Prism(plane, [frame.plane_loop(plane, corners)], float(size[k]), _direction(plane, world_axis), op, name)]
        raise PlanError(f"box {name!r} has no robot-axis-aligned edge")
    if solid.kind == "cylinder":
        return [cylinder_op(frame.point(solid.position), frame.vector(solid.axis), solid.diameter, solid.length, op, name)]
    if solid.kind == "sphere":
        return [sphere_op(frame.point(solid.position), solid.diameter, op, name)]
    if solid.kind == "prism":
        profile = solid.profile
        if isinstance(profile, str):
            profile = params[profile.lstrip("@")]
        from r3_model.units import to_si

        def pts(poly: list) -> list[np.ndarray]:
            a = unit(solid.axis)
            u = unit(solid.u_axis or [1.0, 0.0, 0.0])
            v = np.cross(a, u)
            p0 = np.asarray(solid.position, dtype=float)
            return [p0 + to_si(q[0]) * u + to_si(q[1]) * v for q in poly]

        world_axis = frame.vector(solid.axis)
        plane = Plane.through(frame.point(solid.position), world_axis)
        loops = [frame.plane_loop(plane, pts(profile))] + [frame.plane_loop(plane, pts(hole)) for hole in solid.inner]
        return [Prism(plane, loops, solid.depth, _direction(plane, world_axis), op, name)]
    if solid.kind == "revolve":
        profile = solid.profile
        if isinstance(profile, str):
            profile = params[profile.lstrip("@")]
        from r3_model.units import to_si

        rt = [(to_si(q[0]), to_si(q[1])) for q in profile]
        return [Revolve(point=frame.point(solid.position), axis=frame.vector(solid.axis), profile=rt, op=op, name=name)]  # type: ignore[arg-type]
    raise PlanError(f"unknown solid kind {solid.kind!r}")


def _envelope_solids(mech: Any, params: dict[str, Any]) -> list[Any]:
    from r3_catalog.schema import Solid

    from r3_model.units import to_si

    size = [to_si(v) for v in params["size"]] if "size" in params else [float(v) for v in mech.size]
    if mech.shape == "cylinder":
        diameter, length = size[0], size[1]
        axis = {"x": [1.0, 0, 0], "y": [0, 1.0, 0], "z": [0, 0, 1.0]}[mech.axis]
        base = [0.0, 0.0, 0.0] if mech.axis == "z" else [-length / 2 * a for a in axis]
        return [Solid(kind="cylinder", name="envelope", position=base, axis=axis, diameter=diameter, length=length)]
    return [Solid(kind="box", name="envelope", position=[float(v) + c for v, c in zip(mech.envelope_offset, [0.0, 0.0, size[2] / 2])], size=size)]


def _feature_ops(frame: _Frame, feature: Any, catalog: Any) -> list:
    pos, axis = frame.point(feature.position), frame.vector(feature.axis)
    name = feature.id
    kind = feature.kind
    if kind == "bore":
        return [cylinder_op(pos, axis, feature.diameter, feature.depth, "remove", name)]
    if kind == "d_bore":
        return [d_op(pos, axis, feature.diameter, feature.flat_offset, frame.vector(feature.flat_normal), feature.depth, "remove", name)]
    if kind in ("shaft", "spline"):
        return [cylinder_op(pos, axis, feature.diameter, feature.depth, "add", name)]
    if kind == "d_shaft":
        return [d_op(pos, axis, feature.diameter, feature.flat_offset, frame.vector(feature.flat_normal), feature.depth, "add", name)]
    if kind in ("sphere", "socket"):
        return [sphere_op(pos, feature.diameter, "add" if kind == "sphere" else "remove", name)]
    if kind == "tapped_hole":
        fastener = catalog.fastener(feature.thread)
        if not feature.depth:  # depth 0: threaded through
            return [Hole(point=pos, axis=axis, diameter=fastener.tap_drill, depth=None, style="tapped", thread=fastener.thread,
                         pitch=fastener.pitch, name=name)]
        return [Hole(point=pos, axis=axis, diameter=fastener.tap_drill, depth=feature.depth, style="tapped", thread=fastener.thread,
                     pitch=fastener.pitch, tapped_depth=max(feature.depth - 2 * fastener.pitch, fastener.d), name=name)]
    if kind == "clearance_hole":
        return [Hole(point=pos, axis=axis, diameter=feature.diameter, depth=None, style="drilled", name=name)]
    if kind == "slot":
        plane = Plane.through(pos, axis)
        loop = Loop.obround(plane.to2d(pos), _in_plane_angle(plane, frame.vector(feature.slot_dir)), feature.length, feature.diameter / 2)
        return [Prism(plane, [loop], feature.depth, _direction(plane, axis), "remove", name)]
    if kind == "pocket":
        plane = Plane.through(pos, axis)
        along = unit(frame.vector(feature.slot_dir or [1.0, 0.0, 0.0]))
        across = np.cross(unit(axis), along)
        length, width, depth = (float(v) for v in feature.size)
        corners = [pos + sa * along * length / 2 + sb * across * width / 2 for sa, sb in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        return [Prism(plane, [Loop.polygon([plane.to2d(c) for c in corners])], depth, _direction(plane, axis), "remove", name)]
    if kind == "face":
        return []
    raise PlanError(f"unknown feature kind {kind!r}")


def component_plan(project: Project, cid: str) -> PartPlan:
    from r3_catalog.schema import Solid

    comp = project.spec.components[cid]
    ctype = project.catalog.component(comp.type)
    mech = ctype.mechanical
    frame = _Frame(*component_frame(project, cid))
    params = comp.params
    extra = [Solid.model_validate(s) for s in params.get("solids", [])]
    solids = list(mech.solids) if mech.solids else ([] if extra else _envelope_solids(mech, params))
    solids += extra
    ops: list = []
    for solid in solids:
        ops.extend(_solid_ops(frame, solid, params))
    for feature in mech.features:
        ops.extend(_feature_ops(frame, feature, project.catalog))
    if ops and getattr(ops[0], "op", "add") == "remove":
        raise PlanError(f"{cid}: first solid removes material")
    density = float(mech.density) if mech.density else 0.0
    plan = PartPlan(name=cid, kind="fabricated" if mech.fabricated else "purchased", material=mech.material or ctype.name,
                    density=density, mass=None if mech.fabricated else float(mech.mass), color=_color(mech.color), ops=ops,
                    owner=cid, mass_source="geometry" if mech.fabricated else "catalog")
    if mech.fabricated and not density:
        raise PlanError(f"{cid}: fabricated part needs a material density")
    return plan


# ------------------------------------------------------------------- fasteners
def _hexagon(plane: Plane, center: np.ndarray, across_flats: float) -> Loop:
    c = plane.to2d(center)
    r = across_flats / math.sqrt(3.0)  # circumradius
    return Loop.polygon([(c[0] + r * math.cos(math.radians(60 * i)), c[1] + r * math.sin(math.radians(60 * i))) for i in range(6)])


def fastener_plans(project: Project, fastener: Fastener) -> list[PartPlan]:
    ftype = project.catalog.fastener(fastener.type)
    seat = np.asarray(fastener.position, dtype=float)
    axis = unit(fastener.axis)
    owner = next((p for p in [fastener.thread_into, *fastener.clamps] if p in project.spec.components), "")
    plans: list[PartPlan] = []
    base = dict(material=ftype.material, density=float(ftype.density), color=_color(ftype.color), owner=owner, mass_source="geometry")
    if ftype.kind in ("socket_head_screw", "button_head_screw"):
        length = _length(fastener, ftype)
        k, dk, d = ftype.head_height, ftype.head_diameter, ftype.d
        profile = [(0.0, -k), (dk / 2, -k), (dk / 2, 0.0), (d / 2, 0.0), (d / 2, length), (0.0, length)]
        plans.append(PartPlan(name=fastener.part, kind="fastener", ops=[Revolve(seat, axis, profile=profile, name="screw")], **base))
    elif ftype.kind == "ball_stud":
        length = _length(fastener, ftype)
        d, ch, cd = ftype.d, ftype.collar_height, ftype.collar_diameter
        nd, nl, rb = ftype.neck_diameter, ftype.neck_length, ftype.ball_diameter / 2
        tc = -(ch + nl + rb)  # ball center (negative t: above the seat, against the axis)
        rn = nd / 2
        s = math.sqrt(rb * rb - rn * rn)
        from .geometry import Segment

        pts = [(0.0, length), (d / 2, length), (d / 2, 0.0), (cd / 2, 0.0), (cd / 2, -ch), (rn, -ch), (rn, tc + s)]
        segments = [Segment("line", pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        start = math.atan2(s, rn)
        segments.append(Segment("arc", center=(0.0, tc), radius=rb, a0=start, a1=-math.pi / 2))
        segments.append(Segment("line", (0.0, tc - rb), pts[0]))
        plans.append(PartPlan(name=fastener.part, kind="fastener",
                              ops=[Revolve(seat, axis, profile_loop=Loop(segments=tuple(segments)), name="ball stud")], **base))
    elif ftype.kind == "nut":
        # A nut fastener presses along its axis onto the stack; its body lies behind the seat.
        plans.append(_nut_plan(fastener.part, ftype, seat, -axis, base))
    elif ftype.kind == "standoff_mf":
        plane = Plane.through(seat, axis)
        top = seat - axis * ftype.height
        body = Prism(plane, [_hexagon(plane, seat, ftype.across_flats)], ftype.height, -_direction(plane, axis), "add", "hex body")
        male = cylinder_op(seat, axis, ftype.d, ftype.thread_length, "add", "male thread")
        female = Hole(point=top, axis=axis, diameter=ftype.tap_drill, depth=ftype.female_depth, style="tapped", thread=ftype.thread,
                      pitch=ftype.pitch, tapped_depth=ftype.female_depth - 2 * ftype.pitch, name="female thread")
        plans.append(PartPlan(name=fastener.part, kind="fastener", ops=[body, male, female], **base))
    else:
        raise PlanError(f"fastener {fastener.id}: unsupported kind {ftype.kind}")
    if fastener.nut:
        nut_type = project.catalog.fastener(fastener.nut)
        nut_seat = seat + axis * float(fastener.grip)
        plans.append(_nut_plan(fastener.nut_part or "", nut_type, nut_seat, axis, {**base, "material": nut_type.material,
                                                                                    "density": float(nut_type.density)}))
    return plans


def _length(fastener: Fastener, ftype: Any) -> float:
    if fastener.length is None:
        raise PlanError(f"fastener {fastener.id}: length required")
    if ftype.lengths and not any(abs(fastener.length - L) < 1e-9 for L in ftype.lengths):
        raise PlanError(f"fastener {fastener.id}: {fastener.length * 1000:g} mm is not a standard {ftype.id} length")
    return float(fastener.length)


def _nut_plan(name: str, ftype: Any, seat: np.ndarray, body_dir: np.ndarray, base: dict) -> PartPlan:
    """Hex nut whose bearing face is at ``seat`` and whose body extends along ``body_dir``."""
    plane = Plane.through(seat, body_dir)
    loops = [_hexagon(plane, seat, ftype.across_flats), Loop.circle(plane.to2d(seat), ftype.tap_drill / 2)]
    return PartPlan(name=name, kind="fastener", ops=[Prism(plane, loops, ftype.height, _direction(plane, body_dir), "add", "nut")], **base)


# ------------------------------------------------------- derived (fabricated)
def derived_ops(project: Project, plans: dict[str, PartPlan]) -> None:
    """Append fastener holes, interface bores and strap slots to fabricated parts."""
    catalog = project.catalog

    def fabricated(part: str | None) -> PartPlan | None:
        plan = plans.get(part or "")
        return plan if plan is not None and plan.kind == "fabricated" else None

    for fastener in project.spec.mechanical.fasteners:
        ftype = catalog.fastener(fastener.type)
        if ftype.kind not in SCREW_KINDS:
            continue  # nuts on shafts: their stack parts carry their own bores
        seat = np.asarray(fastener.position, dtype=float)
        axis = unit(fastener.axis)
        clearance = ftype.clearance_normal or ftype.d * 1.12
        for part in fastener.clamps:
            plan = fabricated(part)
            if plan is not None:
                plan.ops.append(Hole(point=seat, axis=axis, diameter=clearance, depth=None, style="clearance", thread=ftype.thread,
                                     fit="Normal", name=f"{fastener.id} clearance"))
        plan = fabricated(fastener.thread_into)
        if plan is not None:
            length = float(fastener.length if fastener.length is not None else ftype.thread_length or 0.0)
            if ftype.kind == "standoff_mf":
                length = float(ftype.thread_length)
            plan.ops.append(Hole(point=seat, axis=axis, diameter=ftype.tap_drill, depth=length + TIP_CLEARANCE, style="tapped",
                                 thread=ftype.thread, pitch=ftype.pitch, tapped_depth=length, name=f"{fastener.id} tapped"))
    for interface in project.spec.mechanical.interfaces:
        bore = interface.params.get("bore")
        plan = fabricated(interface.parts[1])
        if not bore or plan is None or interface.axis_point is None or interface.axis_dir is None:
            continue
        from r3_model.units import to_si

        point = np.asarray(interface.axis_point, dtype=float)
        axis = unit(interface.axis_dir)
        diameter = to_si(bore["diameter"])
        kind = bore.get("kind", "round")
        if kind == "socket":
            plan.ops.append(sphere_op(point, diameter, "remove", f"{interface.id} socket"))
            plan.ops.append(cylinder_op(point, -axis, to_si(bore["opening"]), 0.02, "remove", f"{interface.id} neck opening"))
        elif kind == "round":
            depth = to_si(bore["depth"]) if bore.get("depth") is not None else None
            plan.ops.append(cylinder_op(point, axis, diameter, depth, "remove", f"{interface.id} bore"))
        else:
            raise PlanError(f"interface {interface.id}: unknown bore kind {kind!r}")
    for interface in project.spec.mechanical.interfaces:
        if interface.kind != "strap":
            continue
        from r3_model.units import to_si

        for part in interface.params.get("through", []):
            plan = fabricated(part)
            if plan is None:
                continue
            plane = Plane("TOP", 0.0)
            for index, rect in enumerate(interface.params.get("slots", [])):
                x0, y0, x1, y1 = (to_si(v) for v in rect)
                plan.ops.append(Prism(plane, [Loop.rectangle((x0, y0), (x1, y1))], None, 1.0, "remove",  # type: ignore[arg-type]
                                      f"{interface.id} slot {index + 1}"))


def build_plans(project: Project) -> dict[str, PartPlan]:
    """Every Onshape part of the robot, keyed by its immutable part name."""
    plans: dict[str, PartPlan] = {}
    for cid in project.spec.components:
        plans[cid] = component_plan(project, cid)
    for fastener in project.spec.mechanical.fasteners:
        for plan in fastener_plans(project, fastener):
            if plan.name in plans:
                raise PlanError(f"part name collision: {plan.name}")
            plans[plan.name] = plan
    derived_ops(project, plans)
    return plans
