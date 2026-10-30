"""Definitive mechanical fit verification on exact Onshape geometry.

A part counts as fitted only when a physical interface is verified: fastener
threads engaged, clamp faces in contact, shafts coaxial in bores with the right
fit, D-flats engaged, balls in sockets, straps through slots, adhesive area.
Contact alone counts for nothing. Inputs are analytic STEP faces and fine OCP
tessellations from the pinned pull; booleans use manifold3d.

Every finding is a structured diagnostic with subjects and numeric data (SI).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.units import to_si

from .model import Face, GeometryModel, PartGeometry

VALIDATOR_VERSION = "fit-2.0"
ANGLE_TOL = math.radians(0.2)  # coaxial: axis angle
OFFSET_TOL = 2e-5  # coaxial: axis offset (m)
PLANE_TOL = 1e-5  # coplanar faces (m)
VOLUME_TOL = 5e-11  # 0.05 mm^3: tessellation slivers of coincident surfaces stay below this
THREAD_TOL = 0.15  # measured vs geometric engagement
EDGE_FRACTION = 0.95
TOOL_LENGTH = 0.060
PLASTIC_WORDS = ("nylon", "pa12", "petg", "pla", "abs", "plastic", "polymer", "foam")


# -------------------------------------------------------------------- helpers
def unit(v: Any) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def line_distance(point: np.ndarray, line_point: np.ndarray, line_dir: np.ndarray) -> float:
    d = point - line_point
    return float(np.linalg.norm(d - (d @ line_dir) * line_dir))


def coaxial(face: Face, point: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    """Axis angle (rad) and axis offset (m) between a cylinder/cone face and a line."""
    angle = math.acos(min(1.0, abs(float(face.direction @ axis))))
    return angle, line_distance(face.origin, point, axis)


def axial_span(face: Face, point: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    pts = face.triangles.reshape(-1, 3) if face.triangles is not None else np.array([face.lo, face.hi])
    t = (pts - point) @ axis
    return float(t.min()), float(t.max())


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def is_plastic(material: str) -> bool:
    return any(word in material.lower() for word in PLASTIC_WORDS)


def _plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = unit(np.cross(normal, helper))
    return u, np.cross(normal, u)


def face_section(face: Face, u: np.ndarray, v: np.ndarray):
    """2D manifold CrossSection of a planar face's outline in basis (u, v)."""
    import manifold3d as m3d

    polys = []
    for tri in face.triangles if face.triangles is not None else []:
        p = np.column_stack([tri @ u, tri @ v])
        if (p[1, 0] - p[0, 0]) * (p[2, 1] - p[0, 1]) - (p[1, 1] - p[0, 1]) * (p[2, 0] - p[0, 0]) < 0:
            p = p[::-1]
        polys.append(p)
    if not polys:
        return m3d.CrossSection()
    return m3d.CrossSection(polys, m3d.FillRule.Positive)


@dataclass
class Contact:
    a: str
    b: str
    face_a: str
    face_b: str
    normal: np.ndarray  # outward normal of face_a
    point: np.ndarray
    area: float
    area_a: float
    area_b: float
    section: Any = None  # 2D overlap CrossSection in (u, v)
    basis: tuple[np.ndarray, np.ndarray] | None = None


def plane_contacts(pa: PartGeometry, pb: PartGeometry, min_area: float = 1e-9) -> list[Contact]:
    """Coplanar opposing planar faces of two parts and their overlap areas."""
    out: list[Contact] = []
    for fa in pa.faces_of("PLANE"):
        for fb in pb.faces_of("PLANE"):
            if float(fa.direction @ fb.direction) > -1 + 1e-6:
                continue
            if abs(float((fb.origin - fa.origin) @ fa.direction)) > PLANE_TOL:
                continue
            if np.any(fa.hi < fb.lo - 1e-6) or np.any(fb.hi < fa.lo - 1e-6):
                continue
            u, v = _plane_basis(fa.direction)
            section = face_section(fa, u, v) ^ face_section(fb, u, v)
            area = section.area()
            if area > min_area:
                out.append(Contact(pa.name, pb.name, fa.id, fb.id, fa.direction, fa.origin, area, fa.area, fb.area, section, (u, v)))
    return out


def disc_area(contact: Contact, center: np.ndarray, r_in: float, r_out: float) -> float:
    """Contact area inside the annulus r_in..r_out around ``center`` (on the plane)."""
    import manifold3d as m3d

    u, v = contact.basis
    c = np.array([center @ u, center @ v])
    ring = m3d.CrossSection.circle(r_out, 96).translate(tuple(c)) - m3d.CrossSection.circle(r_in, 96).translate(tuple(c))
    return (contact.section ^ ring).area()


@dataclass(eq=False)
class Link:
    """A verified physical attachment between parts (edge of the retention graph)."""

    a: str
    b: str
    via: str  # fastener or interface id
    kind: str  # fastener | thread | <interface kind>
    rigid: bool = True  # False across joints (revolute/ball/spline)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class FitReport:
    links: list[Link] = field(default_factory=list)
    verified_fasteners: set[str] = field(default_factory=set)
    verified_interfaces: set[str] = field(default_factory=set)
    interface_axes: dict[str, dict[str, Any]] = field(default_factory=dict)
    fasteners: dict[str, dict[str, Any]] = field(default_factory=dict)
    interfaces: dict[str, dict[str, Any]] = field(default_factory=dict)
    intersections: dict[tuple[str, str], float] = field(default_factory=dict)
    allowed: dict[tuple[str, str], str] = field(default_factory=dict)
    retained: set[str] = field(default_factory=set)
    rf: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"verified_fasteners": sorted(self.verified_fasteners), "verified_interfaces": sorted(self.verified_interfaces),
                "links": [{"a": l.a, "b": l.b, "via": l.via, "kind": l.kind, "rigid": l.rigid} for l in self.links],
                "fasteners": self.fasteners, "interfaces": self.interfaces,
                "allowed_intersections_mm3": {f"{a}|{b}": round(self.intersections.get((a, b), 0.0) * 1e9, 4)
                                              for (a, b) in self.allowed}}


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


# ===================================================================== checker
class FitChecker:
    def __init__(self, project: Project, model: GeometryModel, materials: dict[str, str], result: StageResult,
                 scoped: bool = False) -> None:
        self.scoped = scoped  # partial builds: skip items whose parts were not built
        self.project = project
        self.spec = project.spec
        self.catalog = project.catalog
        self.model = model
        self.materials = materials
        self.result = result
        self.report = FitReport()
        self._boxes = {n: (p.lo, p.hi) for n, p in model.parts.items()}

    # ------------------------------------------------------------ primitives
    def err(self, code: str, message: str, subjects: list[str], **data: Any) -> None:
        self.result.add(Diagnostic.error(code, message, subjects=subjects, data=data))

    def warn(self, code: str, message: str, subjects: list[str], **data: Any) -> None:
        self.result.add(Diagnostic.warning(code, message, subjects=subjects, data=data))

    def part(self, name: str) -> PartGeometry | None:
        return self.model.parts.get(name)

    def boxes_touch(self, a: str, b: str, margin: float = 1e-4) -> bool:
        (la, ha), (lb, hb) = self._boxes[a], self._boxes[b]
        return not (np.any(ha < lb - margin) or np.any(hb < la - margin))

    def intersection(self, a: str, b: str) -> float:
        key = _key(a, b)
        if key not in self.report.intersections:
            if not self.boxes_touch(a, b):
                self.report.intersections[key] = 0.0
            else:
                self.report.intersections[key] = (self.model[a].manifold() ^ self.model[b].manifold()).volume()
        return self.report.intersections[key]

    def coaxial_cylinders(self, part: str, point: np.ndarray, axis: np.ndarray, convex: bool | None = None,
                          radius: tuple[float, float] | None = None) -> list[Face]:
        geometry = self.part(part)
        if geometry is None:
            return []
        out = []
        for face in geometry.faces_of("CYLINDER"):
            angle, offset = coaxial(face, point, axis)
            if angle > ANGLE_TOL or offset > OFFSET_TOL:
                continue
            if convex is not None and face.convex is not convex:
                continue
            if radius is not None and not (radius[0] <= face.radius <= radius[1]):
                continue
            out.append(face)
        return out

    # ---------------------------------------------------------------- run
    def in_scope(self, parts: Iterable[str]) -> bool:
        return not self.scoped or all(p in self.model.parts for p in parts)

    def run(self) -> FitReport:
        for fastener in self.spec.mechanical.fasteners:
            if self.in_scope(self.spec.link_parts(fastener.id) or []):
                self.check_fastener(fastener)
        # Sensing checks last: a magnetic target counts only magnets whose bonds were verified before it.
        ordered = sorted(self.spec.mechanical.interfaces, key=lambda i: i.kind == "magnetic_target")
        for interface in ordered:
            extra = interface.params.get("magnets", []) if interface.kind == "magnetic_target" else interface.params.get("through", [])
            if self.in_scope([*interface.parts, *(extra or [])]):
                self.check_interface(interface)
        self.check_interference()
        self.check_joints()
        self.check_transmissions()
        self.check_retention()
        return self.report

    # ---------------------------------------------------------------- fasteners
    def check_fastener(self, fastener: Any) -> None:
        ftype = self.catalog.fastener(fastener.type)
        name = fastener.part
        seat = np.asarray(fastener.position, dtype=float)
        axis = unit(fastener.axis)
        subjects = [name, *fastener.clamps, *([fastener.thread_into] if fastener.thread_into else [])]
        ok = True
        info: dict[str, Any] = {}
        if self.part(name) is None or any(self.part(p) is None for p in subjects):
            self.err("fastener_part_missing", f"{fastener.id}: a part of the fastened stack was not built",
                     [p for p in subjects if self.part(p) is None])
            return
        is_nut = ftype.kind == "nut"
        press = axis  # direction the head/nut bearing face pushes onto the stack
        # ---- head / bearing face seated on the first stack part
        first = fastener.clamps[0] if fastener.clamps else fastener.thread_into
        if fastener.preloaded and first:
            contacts = [c for c in plane_contacts(self.model[name], self.model[first]) if float(c.normal @ press) > 0.999
                        and abs(float((c.point - seat) @ press)) < PLANE_TOL + 1e-6]
            head_r = (ftype.head_diameter or ftype.collar_diameter or ftype.across_flats or ftype.d * 1.8) / 2
            hole_r = ftype.d / 2
            area = sum(disc_area(c, seat, 0.0, head_r * 1.2) for c in contacts)
            nominal = math.pi * (head_r ** 2 - hole_r ** 2)
            info["head_contact_mm2"] = round(area * 1e6, 3)
            if area < 0.3 * nominal:
                ok = False
                self.err("fastener_head_not_seated", f"{fastener.id}: head/bearing face contact on {first} is {area * 1e6:.2f} mm^2 "
                         f"(< 30 % of {nominal * 1e6:.2f} mm^2)", [name, first], contact_area=area, nominal_area=nominal)
        # ---- each clamped part has a coaxial clearance hole; consecutive faces in contact
        stack = list(fastener.clamps) + ([fastener.thread_into] if fastener.thread_into and not is_nut else [])
        for part in fastener.clamps:
            holes = self.hole_faces(part, seat, axis, ftype.d)
            if not holes and not is_nut:
                ok = False
                self.err("fastener_hole_missing", f"{fastener.id}: no hole in {part} coaxial with the fastener axis",
                         [name, part], position=seat.tolist(), axis=axis.tolist())
        for a, b in zip(stack, stack[1:]):
            contacts = [c for c in plane_contacts(self.model[a], self.model[b]) if abs(float(c.normal @ press)) > 0.999]
            reach = max(ftype.head_diameter or 0.0, ftype.across_flats or 0.0, 3 * ftype.d)
            area = sum(disc_area(c, c.point + ((seat - c.point) - ((seat - c.point) @ c.normal) * c.normal), 0.0, reach) for c in contacts)
            info.setdefault("joint_contact_mm2", {})[f"{a}|{b}"] = round(area * 1e6, 3)
            if area < 1e-6:
                ok = False
                self.err("fastener_joint_gap", f"{fastener.id}: {a} and {b} are not in face contact around the fastener "
                         f"({area * 1e6:.3f} mm^2)", [name, a, b], contact_area=area)
        # ---- edge distance in fabricated clamped parts
        if ftype.kind != "nut":
            for part in fastener.clamps:
                if self.materials.get(part, "") == "" or part not in self.fabricated:
                    continue
                fraction = self.edge_fraction(part, seat, axis, ftype.d)
                info.setdefault("edge_material_fraction", {})[part] = round(fraction, 4)
                if fraction < EDGE_FRACTION:
                    ok = False
                    self.err("fastener_edge_distance", f"{fastener.id}: only {fraction:.0%} of the 1 d annulus around the hole is "
                             f"material in {part}", [name, part], fraction=fraction, required=EDGE_FRACTION)
        # ---- thread engagement
        male_part, female_part = (fastener.thread_into, name) if is_nut else (name, fastener.thread_into)
        if fastener.nut:
            male_part, female_part = name, fastener.nut_part
        if male_part and female_part and self.part(female_part) is not None:
            ok &= self.check_thread(fastener, ftype if not fastener.nut else self.catalog.fastener(fastener.nut), male_part,
                                    female_part, seat, axis, is_nut or bool(fastener.nut), info)
        # ---- tool access (warning only)
        self.check_tool_access(fastener, ftype, seat, axis, info)
        self.report.fasteners[fastener.id] = {"ok": ok, **info}
        if ok:
            self.report.verified_fasteners.add(fastener.id)
            if fastener.thread_into:
                self.report.links.append(Link(name, fastener.thread_into, fastener.id, "thread"))
            if fastener.nut_part:
                self.report.links.append(Link(name, fastener.nut_part, fastener.id, "thread"))
            if fastener.preloaded:
                for part in fastener.clamps:
                    self.report.links.append(Link(name, part, fastener.id, "fastener"))

    def hole_faces(self, part: str, seat: np.ndarray, axis: np.ndarray, d: float) -> list[Face]:
        """Faces that form a hole for the shank: a coaxial concave cylinder, or the
        two end arcs of a slot (equal radius >= d/2, parallel, with the fastener
        axis on the segment between their axes)."""
        geometry = self.part(part)
        if geometry is None:
            return []
        concave = [f for f in geometry.faces_of("CYLINDER") if f.convex is False and coaxial(f, seat, axis)[0] <= ANGLE_TOL
                   and f.radius >= d / 2 - 1e-5]
        round_holes = [f for f in concave if coaxial(f, seat, axis)[1] <= OFFSET_TOL]
        if round_holes:
            return round_holes
        for i, a in enumerate(concave):
            for b in concave[i + 1:]:
                if abs(a.radius - b.radius) > 1e-6:
                    continue
                # Project both arc axes and the fastener axis into the plane normal to the axis.
                pa = a.origin - seat - ((a.origin - seat) @ axis) * axis
                pb = b.origin - seat - ((b.origin - seat) @ axis) * axis
                seg = pb - pa
                length = float(np.linalg.norm(seg))
                if length < 1e-9:
                    continue
                t = float(np.clip(-pa @ seg / length ** 2, 0.0, 1.0))
                if np.linalg.norm(pa + t * seg) <= OFFSET_TOL and 0.0 < t < 1.0:
                    return [a, b]
        return []

    @property
    def fabricated(self) -> set[str]:
        return {cid for cid, c in self.spec.components.items() if self.catalog.component(c.type).mechanical.fabricated}

    def edge_fraction(self, part: str, seat: np.ndarray, axis: np.ndarray, d: float) -> float:
        """Fraction of the annulus (hole radius .. +1 d) through the part that is material."""
        import manifold3d as m3d

        holes = self.coaxial_cylinders(part, seat, axis, convex=False, radius=(d / 2 - 1e-5, d * 2))
        if not holes:
            return 0.0
        hole = min(holes, key=lambda f: f.radius)
        t0, t1 = axial_span(hole, seat, axis)
        r0, r1 = hole.radius, hole.radius + d
        ring = (m3d.Manifold.cylinder(t1 - t0, r1, r1, 96) - m3d.Manifold.cylinder(t1 - t0, r0, r0, 96))
        frame = _frame(axis, seat + axis * t0)
        ring = ring.transform(frame)
        inside = (ring ^ self.model[part].manifold()).volume()
        return inside / ring.volume() if ring.volume() > 0 else 0.0

    def check_thread(self, fastener: Any, thread_type: Any, male: str, female: str, seat: np.ndarray, axis: np.ndarray,
                     nut: bool, info: dict[str, Any]) -> bool:
        if self.model.source == "pulled_step":
            self.err("thread_definition_unavailable", f"{fastener.id}: STEP supplies bore/shank surfaces but the pull has no verified thread pitch, hand or threaded extent; thread qualification remains blocked",
                     [fastener.part, male, female])
        d = thread_type.d
        volume = self.intersection(male, female)
        self.report.allowed[_key(male, female)] = f"thread {fastener.id}"
        bores = self.coaxial_cylinders(female, seat, axis, convex=False, radius=(thread_type.tap_drill / 2 * 0.9, d / 2))
        shafts = self.coaxial_cylinders(male, seat, axis, convex=True, radius=(d / 2 * 0.98, d / 2 * 1.02))
        if not bores or not shafts:
            self.err("thread_geometry_missing", f"{fastener.id}: no coaxial thread bore in {female} or shank on {male}",
                     [fastener.part, male, female], bores=len(bores), shafts=len(shafts))
            return False
        bore_d = 2 * min(b.radius for b in bores)
        per_length = math.pi / 4 * (d * d - bore_d * bore_d)
        measured = volume / per_length
        geometric = 0.0
        for b in bores:
            for s in shafts:
                geometric = max(geometric, overlap(axial_span(b, seat, axis), axial_span(s, seat, axis)))
        info.update({"thread_volume_mm3": round(volume * 1e9, 4), "engagement_mm": round(measured * 1000, 3),
                     "geometric_engagement_mm": round(geometric * 1000, 3), "bore_diameter_mm": round(bore_d * 1000, 4)})
        ok = True
        if geometric <= 0 or abs(measured - geometric) > THREAD_TOL * geometric:
            ok = False
            self.err("thread_volume_mismatch", f"{fastener.id}: thread intersection {volume * 1e9:.2f} mm^3 implies {measured * 1000:.2f} mm "
                     f"engagement but the coaxial overlap is {geometric * 1000:.2f} mm (misaligned hole or tip collision)",
                     [fastener.part, male, female], volume=volume, measured=measured, geometric=geometric)
        if nut:
            height = thread_type.height or 0.0
            if measured < 0.9 * height:
                ok = False
                self.err("nut_not_fully_engaged", f"{fastener.id}: nut engaged {measured * 1000:.2f} mm of {height * 1000:.2f} mm",
                         [fastener.part, male, female], engagement=measured, height=height)
            protrusion = self._protrusion(male, female, seat, axis, fastener)
            info["protrusion_mm"] = round(protrusion * 1000, 3)
            if protrusion < thread_type.pitch:
                ok = False
                self.err("bolt_protrusion_short", f"{fastener.id}: thread protrudes {protrusion * 1000:.2f} mm past the nut (< 1 pitch "
                         f"{thread_type.pitch * 1000:.2f} mm)", [fastener.part, male, female], protrusion=protrusion, pitch=thread_type.pitch)
        else:
            plastic = is_plastic(self.materials.get(female, ""))
            factor = 2.0 if plastic else 1.5
            info["required_engagement_mm"] = round(factor * d * 1000, 3)
            if measured < factor * d * (1 - 1e-3):
                ok = False
                self.err("thread_engagement_short", f"{fastener.id}: {measured * 1000:.2f} mm thread engagement in {female} "
                         f"({'plastic' if plastic else 'metal'}) < {factor:g} d = {factor * d * 1000:.2f} mm",
                         [fastener.part, female], engagement=measured, required=factor * d, plastic=plastic)
        return ok and self.model.source != "pulled_step"

    def _protrusion(self, male: str, nut_part: str, seat: np.ndarray, axis: np.ndarray, fastener: Any) -> float:
        """Distance the male thread extends beyond the nut's outer face, measured
        along the direction pointing away from the clamped stack."""
        nut_seat = seat + unit(fastener.axis) * float(fastener.grip) if fastener.nut else seat
        away = unit(fastener.axis) if fastener.nut else -unit(fastener.axis)
        nut_far = float(((self.model[nut_part].vertices - nut_seat) @ away).max())
        near_axis = self.model[male].vertices
        mask = np.array([line_distance(v, nut_seat, away) < (self.catalog.fastener(fastener.type).d / 2 + 1e-4) for v in near_axis])
        male_far = float(((near_axis[mask] - nut_seat) @ away).max()) if mask.any() else -1.0
        return male_far - nut_far

    def check_tool_access(self, fastener: Any, ftype: Any, seat: np.ndarray, axis: np.ndarray, info: dict[str, Any]) -> None:
        import manifold3d as m3d

        inner = 0.0
        if ftype.kind == "nut":
            # Socket wrench: a hollow tube over the protruding thread.
            radius = ftype.across_flats / math.sqrt(3) + 1e-3
            inner = ftype.d / 2 + 5e-4
            start = seat - axis * (ftype.height or 0.0)
        elif ftype.kind == "ball_stud":
            return  # driven by the hex collar before the linkage is assembled
        else:
            radius = (ftype.head_diameter or ftype.across_flats or 2 * ftype.d) / 2 + 1e-3
            start = seat - axis * (ftype.head_height or ftype.height or 0.0)
        tool = m3d.Manifold.cylinder(TOOL_LENGTH, radius, radius, 64)
        if inner:
            tool = tool - m3d.Manifold.cylinder(TOOL_LENGTH, inner, inner, 64)
        tool = tool.transform(_frame(-axis, start - axis * 1e-4))
        lo, hi = np.array(tool.bounding_box()[:3]), np.array(tool.bounding_box()[3:])
        blocked = []
        for other, geometry in self.model.parts.items():
            if other == fastener.part:
                continue
            if np.any(geometry.hi < lo) or np.any(hi < geometry.lo):
                continue
            volume = (tool ^ geometry.manifold()).volume()
            if volume > VOLUME_TOL:
                blocked.append(other)
        if blocked:
            info["tool_access_blocked_by"] = blocked
            self.warn("fastener_tool_access_blocked", f"{fastener.id}: a {2 * radius * 1000:.1f} mm x {TOOL_LENGTH * 1000:.0f} mm tool path "
                      f"is blocked by {', '.join(blocked)}", [fastener.part, *blocked], tool_diameter=2 * radius)

    # --------------------------------------------------------------- interfaces
    def check_interface(self, interface: Any) -> None:
        handler = getattr(self, f"_if_{interface.kind}")
        a, b = interface.parts
        if self.part(a) is None or self.part(b) is None:
            self.err("interface_geometry_missing", f"{interface.id}: part not built", [a, b])
            return
        info: dict[str, Any] = {}
        ok = handler(interface, info)
        self.report.interfaces[interface.id] = {"ok": bool(ok), "kind": interface.kind, **info}
        if ok:
            self.report.verified_interfaces.add(interface.id)
            rigid = interface.kind not in ("revolute_fit", "ball_joint", "spline")
            if interface.kind == "drive_flat" and interface.params.get("rotating_bore"):
                rigid = False
            if interface.kind == "magnetic_target":
                return  # sensing only, not an attachment
            self.report.links.append(Link(a, b, interface.id, interface.kind, rigid))
            if interface.kind == "strap":
                for through in interface.params.get("through", []):
                    self.report.links.append(Link(a, through, interface.id, "strap"))

    def _axis(self, interface: Any) -> tuple[np.ndarray, np.ndarray]:
        return np.asarray(interface.axis_point, dtype=float), unit(interface.axis_dir)

    def _shaft_bore(self, interface: Any, info: dict[str, Any], label: str, fit_key: str = "clearance") -> tuple[bool, float]:
        """Coaxial convex cylinder on parts[0] inside a concave cylinder on parts[1];
        returns (ok, axial overlap) and checks the radial fit against params."""
        point, axis = self._axis(interface)
        a, b = interface.parts
        shafts = self.coaxial_cylinders(a, point, axis, convex=True)
        bores = self.coaxial_cylinders(b, point, axis, convex=False)
        best: tuple[float, Face, Face] | None = None
        for s in shafts:
            for h in bores:
                ov = overlap(axial_span(s, point, axis), axial_span(h, point, axis))
                if ov > 0 and (best is None or ov > best[0]):
                    best = (ov, s, h)
        if best is None:
            self.err("interface_geometry_missing", f"{interface.id}: no {label} shaft on {a} coaxial with a bore in {b}",
                     [a, b], axis_point=point.tolist(), axis_dir=axis.tolist(), shafts=len(shafts), bores=len(bores))
            return False, 0.0
        engaged, shaft, bore = best
        # Total engagement: union of coaxial shaft/bore overlaps with the same radii.
        engaged = sum(overlap(axial_span(s, point, axis), axial_span(h, point, axis)) for s in shafts for h in bores
                      if abs(s.radius - shaft.radius) < 1e-6 and abs(h.radius - bore.radius) < 1e-6)
        radial = bore.radius - shaft.radius
        measured_axis = shaft.direction * float(np.sign(shaft.direction @ axis) or 1.0)
        info.update({"shaft_face": shaft.id, "bore_face": bore.id, "shaft_d_mm": round(2e3 * shaft.radius, 4),
                     "bore_d_mm": round(2e3 * bore.radius, 4), "radial_clearance_mm": round(radial * 1e3, 4),
                     "engagement_mm": round(engaged * 1e3, 3)})
        self.report.interface_axes[interface.id] = {"point": shaft.origin.tolist(), "axis": measured_axis.tolist(),
                                                    "shaft_face": shaft.id, "bore_face": bore.id}
        ok = True
        limits = interface.params.get(fit_key)
        if limits is not None:
            lo, hi = (to_si(v) for v in limits)
            value = -radial if fit_key == "interference" else radial
            if not (lo - 1e-7 <= value <= hi + 1e-7):
                ok = False
                self.err("interface_fit_out_of_range", f"{interface.id}: radial {fit_key} {value * 1e3:.4f} mm outside "
                         f"[{lo * 1e3:.4f}, {hi * 1e3:.4f}] mm", [a, b], value=value, limits=[lo, hi])
        need = to_si(interface.params.get("min_engagement", 0.0))
        if engaged < need - 1e-7:
            ok = False
            self.err("interface_engagement_short", f"{interface.id}: axial engagement {engaged * 1e3:.2f} mm < {need * 1e3:.2f} mm",
                     [a, b], engagement=engaged, required=need)
        return ok, engaged

    def _if_revolute_fit(self, interface: Any, info: dict[str, Any]) -> bool:
        from .rated_units import verify_rated_internal
        rated = verify_rated_internal(self, interface, info)
        if rated is not None:
            return rated
        ok, _ = self._shaft_bore(interface, info, "running")
        a, b = interface.parts
        if interface.params.get("clearance") and to_si(interface.params["clearance"][0]) >= 0:
            # A running fit must not overlap anywhere.
            volume = self.intersection(a, b)
            if volume > VOLUME_TOL:
                ok = False
                self.err("interface_fit_out_of_range", f"{interface.id}: running fit intersects ({volume * 1e9:.3f} mm^3)", [a, b],
                         volume=volume)
        else:
            self.report.allowed[_key(a, b)] = f"line-to-line fit {interface.id}"
        for retainer in interface.params.get("retainers", []) or []:
            if retainer not in self.model.parts:
                ok = False
                self.err("interface_retainer_missing", f"{interface.id}: axial retainer {retainer} not built", [retainer])
        return ok

    def _if_press_fit(self, interface: Any, info: dict[str, Any]) -> bool:
        ok, engaged = self._shaft_bore(interface, info, "press", fit_key="interference")
        a, b = interface.parts
        volume = self.intersection(a, b)
        self.report.allowed[_key(a, b)] = f"press fit {interface.id}"
        if ok:
            r_s = info["shaft_d_mm"] / 2e3
            r_b = info["bore_d_mm"] / 2e3
            expected = math.pi * (r_s ** 2 - r_b ** 2) * engaged
            info["overlap_mm3"] = round(volume * 1e9, 4)
            info["expected_overlap_mm3"] = round(expected * 1e9, 4)
            # Only the fit annulus may overlap: anything beyond it is a collision.
            if volume > expected * 1.5 + VOLUME_TOL:
                ok = False
                self.err("press_fit_overlap_mismatch", f"{interface.id}: overlap {volume * 1e9:.3f} mm^3 exceeds the fit annulus "
                         f"{expected * 1e9:.3f} mm^3", [a, b], volume=volume, expected=expected)
        return ok

    def _if_spline(self, interface: Any, info: dict[str, Any]) -> bool:
        ok, _ = self._shaft_bore(interface, info, "spline")
        a, b = interface.parts
        volume = self.intersection(a, b)
        if volume > VOLUME_TOL:
            self.report.allowed[_key(a, b)] = f"spline {interface.id}"
        return ok

    def _flat_faces(self, part: str, point: np.ndarray, axis: np.ndarray, offset: float, tol: float = 2e-5) -> list[Face]:
        out = []
        for face in self.model[part].faces_of("PLANE"):
            if abs(float(face.direction @ axis)) > 1e-3:
                continue  # D-flats are parallel to the axis
            distance = abs(float((face.origin - point) @ face.direction))
            if abs(distance - offset) <= tol and line_distance(face.origin, point, axis) < 0.02:
                out.append(face)
        return out

    def _if_drive_flat(self, interface: Any, info: dict[str, Any]) -> bool:
        point, axis = self._axis(interface)
        a, b = interface.parts
        offset = to_si(interface.params.get("flat_offset", 0.0))
        internal = bool(interface.params.get("internal"))
        ok, engaged = self._shaft_bore(interface, info, "D")
        shaft_flats = self._flat_faces(a, point, axis, offset)
        flats_info = {"shaft_flats": [f.id for f in shaft_flats]}
        if not shaft_flats:
            ok = False
            self.err("drive_flat_missing", f"{interface.id}: no flat on {a} parallel to the axis at {offset * 1e3:.3f} mm", [a, b],
                     flat_offset=offset)
        if not internal:
            bore_flats = [f for f in self.model[b].faces_of("PLANE") if abs(float(f.direction @ axis)) < 1e-3
                          and line_distance(f.origin, point, axis) < 0.02]
            pairs = []
            for fs in shaft_flats:
                for fb in bore_flats:
                    if float(fs.direction @ fb.direction) < -0.9999:
                        gap = float((fb.origin - fs.origin) @ fs.direction)
                        span = overlap(axial_span(fs, point, axis), axial_span(fb, point, axis))
                        if -1e-6 <= gap <= 5e-5 and span > 0:
                            pairs.append((gap, span, fb.id))
            flats_info["flat_pairs"] = [{"gap_mm": round(g * 1e3, 4), "overlap_mm": round(s * 1e3, 3), "bore_face": f} for g, s, f in pairs]
            flat_overlap = max((s for _, s, _ in pairs), default=0.0)
            need = to_si(interface.params.get("min_engagement", 0.0))
            if flat_overlap < need - 1e-7:
                ok = False
                self.err("drive_flat_missing", f"{interface.id}: shaft and bore flats overlap {flat_overlap * 1e3:.2f} mm "
                         f"(< {need * 1e3:.2f} mm): no torque transfer", [a, b], overlap=flat_overlap, required=need)
            volume = self.intersection(a, b)
            if volume > VOLUME_TOL:
                ok = False
                self.err("interface_fit_out_of_range", f"{interface.id}: D-shaft intersects its D-bore ({volume * 1e9:.3f} mm^3)",
                         [a, b], volume=volume)
        info.update(flats_info)
        return ok

    def _if_ball_joint(self, interface: Any, info: dict[str, Any]) -> bool:
        point = np.asarray(interface.axis_point, dtype=float)
        a, b = interface.parts
        balls = [f for f in self.model[a].faces_of("SPHERE") if f.convex and np.linalg.norm(f.origin - point) < 0.002]
        sockets = [f for f in self.model[b].faces_of("SPHERE") if f.convex is False and np.linalg.norm(f.origin - point) < 0.002]
        if not balls or not sockets:
            self.err("ball_joint_mismatch", f"{interface.id}: no ball on {a} / socket on {b} near the joint center", [a, b],
                     balls=len(balls), sockets=len(sockets))
            return False
        ball, socket = balls[0], min(sockets, key=lambda f: np.linalg.norm(f.origin - balls[0].origin))
        offset = float(np.linalg.norm(ball.origin - socket.origin))
        radial = socket.radius - ball.radius
        lo, hi = (to_si(v) for v in interface.params.get("clearance", [0.0, 0.05e-3]))
        info.update({"center_offset_mm": round(offset * 1e3, 4), "radial_clearance_mm": round(radial * 1e3, 4),
                     "ball_face": ball.id, "socket_face": socket.id})
        self.report.interface_axes[interface.id] = {"point": ball.origin.tolist(), "ball_face": ball.id, "socket_face": socket.id}
        ok = True
        if offset > OFFSET_TOL:
            ok = False
            self.err("ball_joint_mismatch", f"{interface.id}: ball and socket centers {offset * 1e3:.3f} mm apart", [a, b], offset=offset)
        if not (lo - 1e-7 <= radial <= hi + 1e-7):
            ok = False
            self.err("interface_fit_out_of_range", f"{interface.id}: ball radial clearance {radial * 1e3:.4f} mm outside "
                     f"[{lo * 1e3:.3f}, {hi * 1e3:.3f}] mm", [a, b], value=radial, limits=[lo, hi])
        if self.intersection(a, b) > VOLUME_TOL:
            ok = False
            self.err("interface_fit_out_of_range", f"{interface.id}: ball intersects its socket", [a, b])
        return ok

    def _if_adhesive(self, interface: Any, info: dict[str, Any]) -> bool:
        a, b = interface.parts
        contacts = plane_contacts(self.model[a], self.model[b])
        need = float(interface.params.get("min_fraction", 0.5))
        if not contacts:
            self.err("adhesive_contact_insufficient", f"{interface.id}: {a} and {b} share no bonded face", [a, b], fraction=0.0, required=need)
            return False
        best = max(contacts, key=lambda c: c.area / min(c.area_a, c.area_b))
        fraction = best.area / min(best.area_a, best.area_b)
        info.update({"contact_mm2": round(best.area * 1e6, 3), "fraction": round(fraction, 4), "faces": [best.face_a, best.face_b]})
        self.report.interface_axes[interface.id] = {"point": best.point.tolist(), "normal": best.normal.tolist(),
                                                    "faces": [best.face_a, best.face_b]}
        if fraction < need:
            self.err("adhesive_contact_insufficient", f"{interface.id}: bonded area {best.area * 1e6:.2f} mm^2 is {fraction:.0%} of the "
                     f"smaller face (< {need:.0%})", [a, b], fraction=fraction, required=need, area=best.area)
            return False
        return True

    def _if_strap(self, interface: Any, info: dict[str, Any]) -> bool:
        a, b = interface.parts
        ok = True
        strap = self.model[a].manifold()
        if strap.genus() < 1:
            ok = False
            self.err("strap_not_closed", f"{interface.id}: {a} is not a closed loop (genus {strap.genus()})", [a], genus=strap.genus())
        contacts = plane_contacts(self.model[a], self.model[b])
        area = sum(c.area for c in contacts)
        info["contact_mm2"] = round(area * 1e6, 3)
        if area <= 1e-6:
            ok = False
            self.err("strap_not_touching", f"{interface.id}: {a} does not bear on {b}", [a, b], contact_area=area)
        for through in interface.params.get("through", []):
            geometry = self.model[through]
            volume = self.intersection(a, through)
            # The strap must cross the plate's thickness: intersect it with the slab spanned by the plate.
            import manifold3d as m3d

            lo, hi = geometry.lo, geometry.hi
            slab = m3d.Manifold.cube(tuple((hi - lo).tolist())).translate(tuple(lo.tolist()))
            crossing = (strap ^ slab).volume()
            info.setdefault("through", {})[through] = {"crossing_mm3": round(crossing * 1e9, 3), "interference_mm3": round(volume * 1e9, 4)}
            if crossing <= VOLUME_TOL:
                ok = False
                self.err("strap_not_through", f"{interface.id}: {a} does not pass through {through}", [a, through])
            if volume > VOLUME_TOL:
                ok = False
                self.err("strap_not_through", f"{interface.id}: {a} collides with {through} ({volume * 1e9:.2f} mm^3): slots missing "
                         "or misplaced", [a, through], volume=volume)
        return ok

    def _if_magnetic_target(self, interface: Any, info: dict[str, Any]) -> bool:
        ring, sensor = interface.parts
        point, axis = self._axis(interface)
        magnets = list(interface.params.get("magnets", []))
        behavior = self.catalog.component(self.spec.components[sensor].type).behavior
        params = dict(behavior.params if behavior else {})
        ok = True
        ppr = int(params.get("pulses_per_wheel_rev", 0))
        retained = [m for m in magnets if any(i.kind == "adhesive" and m in i.parts and ring in i.parts and i.id in self.report.verified_interfaces
                                              for i in self.spec.mechanical.interfaces)]
        info.update({"magnets": len(magnets), "retained_magnets": len(retained), "pulses_per_rev": ppr})
        if len(retained) != ppr or len(magnets) != ppr:
            ok = False
            self.err("encoder_ppr_mismatch", f"{interface.id}: {len(retained)} retained magnets but the encoder is configured for {ppr} "
                     "pulses per wheel revolution", [ring, sensor, *magnets], magnets=len(retained), pulses_per_rev=ppr)
        # Gap from the magnet facing the sensor to the sensor's top face.
        sensor_geo = self.model[sensor]
        top = float((sensor_geo.vertices @ axis).max())
        gaps = {}
        for magnet in magnets:
            geometry = self.model.parts.get(magnet)
            if geometry is None:
                continue
            gaps[magnet] = float((geometry.vertices @ axis).min()) - top
        facing = min(gaps, key=lambda m: gaps[m] if gaps[m] >= -1e-9 else 1e9) if gaps else None
        if facing is None:
            self.err("magnet_gap_out_of_range", f"{interface.id}: no magnet geometry", [ring, sensor])
            return False
        gap = gaps[facing]
        lo, hi = (to_si(v) for v in interface.params.get("gap", [0.5e-3, 2e-3]))
        die = to_si(params.get("die_depth", 0.5e-3))
        geometry = self.model[facing]
        radius = max(f.radius for f in geometry.faces_of("CYLINDER"))
        length = float(np.ptp(geometry.vertices @ axis))
        br = float(self.catalog.component(self.spec.components[facing].type).behavior.params.get("remanence", 1.44))
        field_t = axial_field(br, radius, length, gap + die)
        b_op = float(params.get("b_op_max", 0.0175))  # T
        # Field halfway between two magnets (rotated by pi/ppr), dipole approximation.
        orbit = float(np.linalg.norm(np.cross(geometry.vertices.mean(axis=0) - np.asarray(self.spec.components[ring].pose.xyz),
                                              unit(_ring_axis(self.spec, ring)))))
        between = dipole_field(br, radius, length, orbit, gap + die, math.pi / max(ppr, 1))
        b_rp = float(params.get("b_rp_min", 0.001))  # T
        info.update({"facing_magnet": facing, "air_gap_mm": round(gap * 1e3, 4), "field_at_die_mT": round(field_t * 1e3, 2),
                     "b_op_max_mT": round(b_op * 1e3, 2), "field_between_magnets_mT": round(between * 1e3, 3), "b_rp_min_mT": round(b_rp * 1e3, 3),
                     "magnet_radius_mm": round(radius * 1e3, 3), "magnet_length_mm": round(length * 1e3, 3), "orbit_radius_mm": round(orbit * 1e3, 3)})
        if not (lo - 1e-7 <= gap <= hi + 1e-7):
            ok = False
            self.err("magnet_gap_out_of_range", f"{interface.id}: air gap {gap * 1e3:.3f} mm outside [{lo * 1e3:.2f}, {hi * 1e3:.2f}] mm",
                     [facing, sensor], gap=gap, limits=[lo, hi])
        if field_t < b_op:
            ok = False
            self.err("magnet_field_insufficient", f"{interface.id}: {field_t * 1e3:.2f} mT at the Hall die < B_OP max {b_op * 1e3:.2f} mT",
                     [facing, sensor], field=field_t, b_op_max=b_op)
        if between > b_rp:
            self.warn("magnet_release_marginal", f"{interface.id}: {between * 1e3:.3f} mT between magnets may not drop below B_RP min "
                      f"{b_rp * 1e3:.3f} mT", [ring, sensor], field=between, b_rp_min=b_rp)
        return ok

    def _if_connector_retention(self, interface: Any, info: dict[str, Any]) -> bool:
        male, female = interface.parts
        point, axis = self._axis(interface)
        lateral_tol = to_si(interface.params.get("lateral_tolerance", 2e-4))
        need = to_si(interface.params.get("min_insertion", 5e-3))
        near = lambda f: line_distance(f.origin, point, axis) < 0.03  # noqa: E731
        pin_tops = [f for f in self.model[male].faces_of("PLANE") if float(f.direction @ axis) > 0.999 and near(f)
                    and float((f.origin - point) @ axis) > 0]
        mouth = [f for f in self.model[female].faces_of("PLANE") if float(f.direction @ axis) < -0.999 and near(f)
                 and abs(float((f.origin - point) @ axis)) < 1e-4]
        ceilings = [f for f in self.model[female].faces_of("PLANE") if float(f.direction @ axis) < -0.999 and near(f)
                    and float((f.origin - point) @ axis) > 1e-4]
        if not pin_tops or not mouth or not ceilings:
            self.err("connector_misaligned", f"{interface.id}: header pins / socket faces not found at the mating point", [male, female],
                     pin_tops=len(pin_tops), mouths=len(mouth), cavities=len(ceilings))
            return False
        pins = max(pin_tops, key=lambda f: float((f.origin - point) @ axis))
        cavity = min(ceilings, key=lambda f: float((f.origin - point) @ axis))
        insertion = float((pins.origin - point) @ axis)
        center_p = (pins.lo + pins.hi) / 2
        center_c = (cavity.lo + cavity.hi) / 2
        lateral = float(np.linalg.norm((center_p - center_c) - ((center_p - center_c) @ axis) * axis))
        inside = bool(np.all(pins.lo[:2] >= cavity.lo[:2] - 1e-7) and np.all(pins.hi[:2] <= cavity.hi[:2] + 1e-7))
        info.update({"insertion_mm": round(insertion * 1e3, 3), "lateral_offset_mm": round(lateral * 1e3, 4), "pins_inside_cavity": inside})
        ok = True
        if lateral > lateral_tol or not inside:
            ok = False
            self.err("connector_misaligned", f"{interface.id}: pin field offset {lateral * 1e3:.3f} mm from the socket cavity "
                     f"(tolerance {lateral_tol * 1e3:.2f} mm)", [male, female], lateral=lateral, tolerance=lateral_tol, inside=inside)
        if insertion < need:
            ok = False
            self.err("connector_insertion_short", f"{interface.id}: pins inserted {insertion * 1e3:.2f} mm < {need * 1e3:.2f} mm",
                     [male, female], insertion=insertion, required=need)
        if self.intersection(male, female) > VOLUME_TOL:
            ok = False
            self.err("connector_misaligned", f"{interface.id}: header collides with the socket", [male, female])
        return ok

    # -------------------------------------------------------------- interference
    def check_interference(self) -> None:
        names = sorted(self.model.parts)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if not self.boxes_touch(a, b):
                    continue
                volume = self.intersection(a, b)
                if volume <= VOLUME_TOL or _key(a, b) in self.report.allowed:
                    continue
                self.err("cad_interference", f"{a} and {b} intersect by {volume * 1e9:.3f} mm^3", [a, b], intersection_volume=volume)

    # ------------------------------------------------------------------- joints
    def check_joints(self) -> None:
        interfaces = {i.id: i for i in self.spec.mechanical.interfaces}
        for joint in self.spec.mechanical.joints:
            if joint.type == "fixed":
                continue
            if self.scoped and (joint.interface not in interfaces or not self.in_scope(interfaces[joint.interface].parts)):
                continue
            iid = joint.interface
            if iid is None or iid not in self.report.verified_interfaces:
                self.err("joint_interface_unverified", f"joint {joint.id}: interface {iid} is not physically verified",
                         [joint.id, joint.parent, joint.child], interface=iid)
                continue
            measured = self.report.interface_axes.get(iid, {})
            axis = unit(joint.axis)
            m_axis = unit(measured["axis"])
            angle = math.acos(min(1.0, abs(float(axis @ m_axis))))
            offset = line_distance(np.asarray(joint.origin, dtype=float), np.asarray(measured["point"]), m_axis)
            if angle > ANGLE_TOL or offset > 5e-5:
                self.err("joint_axis_mismatch", f"joint {joint.id}: axis differs from interface {iid} by {math.degrees(angle):.3f} deg, "
                         f"{offset * 1e3:.3f} mm", [joint.id, iid], angle=angle, offset=offset)

    # ------------------------------------------------------------- transmissions
    def check_transmissions(self) -> None:
        verified = self.report.verified_interfaces | self.report.verified_fasteners
        children = {j.id: j.child for j in self.spec.mechanical.joints}
        for transmission in self.spec.mechanical.transmissions:
            if self.scoped and not all(self.in_scope(self.spec.link_parts(link) or ["?"]) for link in transmission.path):
                continue
            reached = {transmission.actuator}
            broken = [link for link in transmission.path if link not in verified]
            for link in transmission.path:
                if link in verified:
                    members = set(self.spec.link_parts(link) or [])
                    if reached & members:
                        reached |= members
            # Parts rigidly attached to a reached part move with it (e.g. a wheel's nut).
            missing = [children[j] for j in transmission.joints if children[j] not in reached]
            if broken or missing:
                self.err("transmission_path_broken", f"transmission {transmission.id}: unverified links {broken}; driven parts not "
                         f"reached {missing}", [transmission.id, *broken, *missing], unverified=broken, unreached=missing)

    # ---------------------------------------------------------------- retention
    def check_retention(self, root: str | None = None) -> None:
        if root is None:
            root = self.project.spec.assembly.root if self.project.spec.assembly else None
            if not root:
                roots = [cid for cid, component in self.project.spec.components.items() if component.mount in (None, "world")]
                if len(roots) != 1:
                    self.warn("retention_root_ambiguous", "Retention needs one assembly root or one unmounted component",
                              roots, candidates=len(roots))
                    return
                root = roots[0]
        if root not in self.model.parts:
            return
        adjacency: dict[str, set[str]] = {name: set() for name in self.model.parts}
        for link in self.report.links:
            if link.a in adjacency and link.b in adjacency:
                adjacency[link.a].add(link.b)
                adjacency[link.b].add(link.a)
        seen, stack = {root}, [root]
        while stack:
            for neighbor in adjacency[stack.pop()]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        self.report.retained = seen
        missing = sorted(set(adjacency) - seen)
        if missing:
            self.err("part_not_retained", f"{len(missing)} parts are not held to {root} by verified attachments: {missing}",
                     missing, count=len(missing))


def rotor_crossing_links(spec: Any, links: list[Link]) -> set[int]:
    """Indices of fastener/thread links that would lock an internally rotating
    interface: a horn screw clamps the horn and threads into the servo's output
    shaft (which turns inside the case); a wheel nut clamps a bearing's inner
    race (the outer race is pressed into the carrier). The link from such a
    fastener to the case-side part (servo / bearing) carries no rigid joint."""
    rotating = [(i.parts[0], i.parts[1], i.parts[0]) for i in spec.mechanical.interfaces if i.kind == "spline"]
    rotating += [(i.parts[0], i.parts[1], i.parts[1]) for i in spec.mechanical.interfaces
                 if i.kind == "revolute_fit" and i.params.get("internal_rotation")]
    out: set[int] = set()
    fasteners = {f.id: set(spec.link_parts(f.id) or []) for f in spec.mechanical.fasteners}
    for index, link in enumerate(links):
        members = fasteners.get(link.via)
        if members is None or link.kind not in ("fastener", "thread"):
            continue
        for a, b, case in rotating:
            if a in members and b in members and case in (link.a, link.b):
                out.add(index)
    return out


# ------------------------------------------------------------------- physics
def axial_field(br: float, radius: float, length: float, z: float) -> float:
    """On-axis flux density of an axially magnetized cylinder at distance z from
    its face: B = Br/2 [ (z+L)/sqrt((z+L)^2+R^2) - z/sqrt(z^2+R^2) ]."""
    return br / 2 * ((z + length) / math.hypot(z + length, radius) - z / math.hypot(z, radius))


def dipole_field(br: float, radius: float, length: float, orbit: float, gap: float, angle: float) -> float:
    """|B| of a magnet treated as a point dipole (m = Br V / mu0) after the ring
    has turned ``angle`` away from the sensor: B = mu0 m / (4 pi r^3) sqrt(1 + 3 cos^2 t)."""
    mu0 = 4e-7 * math.pi
    moment = br * math.pi * radius ** 2 * length / mu0
    center_radius = orbit  # magnet center distance from the axle
    sensor_radius = orbit + length / 2 + gap  # die distance from the axle along the radial at angle 0
    dx = center_radius * math.sin(angle)
    dz = sensor_radius - center_radius * math.cos(angle)
    r = math.hypot(dx, dz)
    m_dir = np.array([math.sin(angle), math.cos(angle)])  # magnetization radial
    r_hat = np.array([dx, dz]) / r
    cos_t = float(m_dir @ r_hat)
    return mu0 * moment / (4 * math.pi * r ** 3) * math.sqrt(1 + 3 * cos_t ** 2)


def _ring_axis(spec: Any, ring: str) -> list[float]:
    for interface in spec.mechanical.interfaces:
        if interface.kind == "press_fit" and ring in interface.parts and interface.axis_dir is not None:
            return list(interface.axis_dir)
    return [0.0, 1.0, 0.0]


def _frame(z_axis: np.ndarray, origin: np.ndarray) -> np.ndarray:
    z = unit(z_axis)
    helper = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = unit(np.cross(helper, z))
    y = np.cross(z, x)
    matrix = np.zeros((3, 4))
    matrix[:, 0], matrix[:, 1], matrix[:, 2], matrix[:, 3] = x, y, z, origin
    return matrix


def verify_fit(project: Project, model: GeometryModel | None, materials: dict[str, str] | None, result: StageResult,
               scoped: bool = False) -> FitReport:
    from r3_model.pulled import load_pulled, PullError
    from r3_model.pulled_view import apply_pulled
    from .step_geometry import geometry_from_pulled

    try:
        pulled = load_pulled(project.root)
        apply_pulled(project, pulled)
        model = geometry_from_pulled(pulled)
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        return FitReport()
    materials = {name: part.material or "" for name, part in pulled.parts.items()}
    result.assume("mechanical", f"Fit checks read pulled STEP analytic surfaces and OCP tessellation at chord <= "
                  f"{model.chord_tolerance * 1e3:.2f} mm and angle <= {math.degrees(model.angle_tolerance):.1f} deg; "
                  f"volume tolerance remains {VOLUME_TOL * 1e9:.2f} mm^3.")
    return FitChecker(project, model, materials, result, scoped=False).run()
