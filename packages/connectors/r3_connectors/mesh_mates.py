"""Fresh, geometric mating checks. No component names or cached pass/fail answers.

Sections and ray queries use trimesh; assignment uses scipy and solid overlap
uses the same manifold3d engine as the CAD/wiring checks. A declared pin is never
used to create a detected contact. Small closed section loops are the evidence
for posts, bores, pad holes and contact-entry openings. Complex/simplified models
which do not expose those features are reported, never assumed to pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import trimesh
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
from shapely.geometry import Polygon, MultiPoint

from r3_catalog.schema import ConnectorGeometry
from r3_model.diagnostics import Diagnostic
from .frames import Frame

LATERAL_LIMIT = 1e-4
POSITION_LIMIT = 5e-5
VOLUME_LIMIT = 1e-12  # same 0.001 mm^3 solid tolerance as native CAD checks


@dataclass
class Contact:
    centre: np.ndarray  # local x/y, metres; z=0 is the mating reference plane
    low: float
    high: float
    size: np.ndarray
    shape: str
    opening: bool = False
    diameter_range: tuple[float, float] | None = None

    def json(self) -> dict[str, Any]:
        return {
            "centre": self.centre.tolist(),
            "axial_interval": [self.low, self.high],
            "size": self.size.tolist(),
            "shape": self.shape,
            "opening": self.opening,
            "diameter_range": self.diameter_range,
        }


@dataclass
class Detection:
    contacts: list[Contact] = field(default_factory=list)
    bodies: list[trimesh.Trimesh] = field(default_factory=list, repr=False)
    geometry_present: bool = False
    reason: str = ""
    source: str = ""
    supplier_unverifiable: bool = False

    def signature(self) -> dict[str, Any]:
        if not self.contacts:
            return {"count": 0, "type": "unresolved"}
        p = np.array([c.centre[:2] for c in self.contacts])
        distances = cdist(p, p)
        np.fill_diagonal(distances, np.inf)
        pitch = float(np.median(distances.min(axis=1))) if len(p) > 1 else 0.0
        rows = len(np.unique(np.round(p[:, 1] / POSITION_LIMIT)))
        shape = max(
            set(c.shape for c in self.contacts),
            key=lambda s: sum(c.shape == s for c in self.contacts),
        )
        return {
            "count": len(p),
            "pitch": pitch,
            "rows": rows,
            "shape": shape,
            "size": np.median([c.size for c in self.contacts], axis=0).tolist(),
            "type": f"{shape}:{rows}row:{pitch * 1000:.3f}mm:{len(p)}contacts",
        }

    def json(self) -> dict[str, Any]:
        return {
            **self.signature(),
            "contacts": [c.json() for c in self.contacts],
            "geometry_present": self.geometry_present,
            "reason": self.reason,
            "source": self.source,
            "supplier_unverifiable": self.supplier_unverifiable,
        }


def scene_bodies(
    scene: trimesh.Scene, transform: np.ndarray | None = None
) -> list[trimesh.Trimesh]:
    """Weld tessellation seams before connected-component splitting, preserving holes."""
    bodies = []
    for node in scene.graph.nodes_geometry:
        pose, name = scene.graph[node]
        mesh = scene.geometry[name].copy()
        mesh.apply_transform(pose if transform is None else transform @ pose)
        mesh.merge_vertices()
        bodies.extend(mesh.split(only_watertight=False))
    return bodies


def _loops(
    mesh: trimesh.Trimesh, height: float
) -> list[tuple[np.ndarray, np.ndarray, str, bool]]:
    # Work in mm: trimesh's path welding tolerance must not eat sub-mm fingers.
    section = mesh.section(plane_origin=[0, 0, height], plane_normal=[0, 0, 1])
    if section is None:
        return []
    polygons = []
    for loop in section.discrete:
        if len(loop) < 4 or np.linalg.norm(loop[0] - loop[-1]) > 1e-5:
            continue
        poly = Polygon(loop[:, :2])
        if poly.is_valid and poly.area > 1e-5:
            polygons.append(poly)
    result = []
    # Slotted bullet pins have several petals in a section but one contact
    # axis. Recover their circular envelope from this connected solid only;
    # never merge unrelated posts across bodies or across a connector pitch.
    if len(polygons) > 1 and not any(
        a.contains(b) for a in polygons for b in polygons if a is not b
    ):
        hull = MultiPoint(
            np.concatenate([np.asarray(p.exterior.coords) for p in polygons])
        ).convex_hull
        bounds = np.array(hull.bounds).reshape(2, 2)
        size = np.ptp(bounds, axis=0)
        if (
            min(size) > 0.5
            and max(size) < 5.5
            and max(size) / min(size) < 1.05
            and 0.72 < hull.area / np.prod(size) < 0.84
        ):
            return [
                (np.array([hull.centroid.x, hull.centroid.y]), size, "round", False)
            ]
    for poly in polygons:
        bounds = np.array(poly.bounds).reshape(2, 2)
        size = np.ptp(bounds, axis=0)
        if min(size) < 0.08 or max(size) > 5.5 or max(size) / min(size) > 4:
            continue
        centre = np.array([poly.centroid.x, poly.centroid.y])
        opening = any(
            other.area > poly.area and other.contains(poly) for other in polygons
        )
        ratio = poly.area / np.prod(size)
        shape = (
            "round"
            if 0.65 < ratio < 0.88 and max(size) / min(size) < 1.15
            else "rectangular"
        )
        result.append((centre, size, shape, opening))
    return result


def detect(
    bodies: list[trimesh.Trimesh],
    frame: Frame,
    geometry: ConnectorGeometry,
    source: str = "",
) -> Detection:
    """Find repeated physical contact sections inside the declared search volume.

    The declaration limits the *search*, not the candidate count, pitch or pin
    locations. Detection is deliberately conservative: a large uniform block is
    not evidence of pins. The algorithm also accepts a single circular terminal.
    """
    out = Detection(source=source)
    envelope = np.asarray(geometry.envelope or [0.03, 0.03, 0.02])
    xy_limit = envelope[:2] / 2 + 0.0005
    low, high = -max(geometry.depth, 0.0005), max(geometry.reach, 0.0005)
    selected = []
    for body in bodies:
        local = body.copy()
        local.apply_transform(frame.inverse().matrix())
        lo, hi = local.bounds
        if (
            np.any(lo[:2] > xy_limit)
            or np.any(hi[:2] < -xy_limit)
            or lo[2] > high + 0.0005
            or hi[2] < low - 0.0005
        ):
            continue
        selected.append(local)
    out.geometry_present = bool(selected)
    out.bodies = selected
    if not selected:
        out.reason = "No part surface intersects the connector search volume."
        return out
    # Section away from coplanar faces. Include individual model bounds so short
    # fingers and pad rings are found without assuming their declared positions.
    heights = {-0.05, -0.25, -0.5, -1.0, -2.0, 0.05, 0.25, 0.5, 1.0, 2.0}
    heights.update([low * 1000 / 2, high * 1000 / 2])
    for m in selected:
        lo, hi = m.bounds[:, 2] * 1000
        for z in (lo + 0.05, hi - 0.05, (lo + hi) / 2):
            if low * 1000 < z < high * 1000:
                heights.add(round(float(z), 4))
    # Bound work on large assembled boards; retain near-face and tip samples.
    heights = sorted(h for h in heights if low * 1000 - 0.1 <= h <= high * 1000 + 0.1)
    slices: list[tuple[float, list[tuple[np.ndarray, np.ndarray, str, bool]]]] = []
    for z in heights:
        loops = []
        for mesh in selected:
            if not mesh.bounds[0, 2] * 1000 < z < mesh.bounds[1, 2] * 1000:
                continue
            mm = mesh.copy()
            mm.apply_scale(1000)
            for centre, size, shape, opening in _loops(mm, z):
                if np.all(np.abs(centre) * 0.001 <= xy_limit):
                    # A component's rectangular body is not a contact. Circular
                    # bores and sockets may be wider than a header post.
                    if opening or max(size) <= 1.3 or shape == "round":
                        if not any(sh == shape and op == opening and
                                   np.linalg.norm(cc - centre) < 1e-5 and
                                   np.linalg.norm(ss - size) < 1e-5
                                   for cc, ss, sh, op in loops):
                            loops.append((centre, size, shape, opening))
        slices.append((z, loops))
    # Repeated cross-section sizes distinguish the contact array from latches,
    # shield tabs, board edges, and mounting holes.
    candidates = []
    for z, loops in slices:
        groups: dict[tuple, list] = {}
        for item in loops:
            _, size, shape, opening = item
            key = (shape, opening, *np.round(size / 0.1).astype(int))
            groups.setdefault(key, []).append(item)
        for group in groups.values():
            centres = np.array([x[0] for x in group])
            if len(group) > 1:
                distance = cdist(centres, centres)
                np.fill_diagonal(distance, np.inf)
                nearest = distance.min(axis=1)
                regular = np.max(np.abs(nearest - np.median(nearest))) < 0.08
                if not regular:
                    continue
            candidates.append((len(group), z, group))
    if not candidates:
        out.reason = "Part geometry is present but no contact posts, rings or entry openings can be resolved."
        return out
    _, z, group = max(candidates, key=lambda item: (item[0], -abs(item[1])))
    for centre, size, shape, opening in group:
        diameters = [
            float(np.mean(ss)) * 0.001
            for _, loops in slices
            for cc, ss, sh, op in loops
            if shape == sh == "round"
            and opening == op
            and np.linalg.norm(cc - centre) < 0.05
        ]
        levels = [
            zz
            for zz, loops in slices
            if any(
                np.linalg.norm(cc - centre) < 0.05
                and op == opening
                and (
                    shape == "round"
                    and sh == "round"
                    or np.max(np.abs(ss - size)) < 0.15
                )
                for cc, ss, sh, op in loops
            )
        ]
        lo, hi = min(levels), max(levels)
        # Use exact body bounds for separate prismatic posts; section sampling
        # alone would systematically understate their physical insertion length.
        if not opening:
            for m in selected:
                b = m.bounds * 1000
                if (
                    np.linalg.norm(b.mean(axis=0)[:2] - centre) < 0.05
                    and np.max(np.abs(np.ptp(b, axis=0)[:2] - size)) < 0.15
                ):
                    lo = min(lo, b[0, 2])
                    hi = max(hi, b[1, 2])
        else:
            # A bore continues to the first actual mesh intersection in each
            # axial direction. This measures a socket's ceiling / through-pad.
            for sign in (-1, 1):
                origin = np.array([*centre, z]) * 0.001
                hits = []
                for m in selected:
                    points, _, _ = m.ray.intersects_location([origin], [[0, 0, sign]])
                    hits.extend(
                        float(p[2] * 1000)
                        for p in points
                        if sign * (p[2] * 1000 - z) > 1e-4
                    )
                if hits:
                    hit = min(hits, key=lambda h: abs(h - z))
                    if sign > 0:
                        hi = hit
                    else:
                        lo = hit
                else:
                    # A through opening has no axial ray hit. Its rim still
                    # ends at the containing body's actual surface, not the
                    # offset section used to discover the opening.
                    containing = [
                        m
                        for m in selected
                        if np.all(m.bounds[0, :2] < centre * 0.001)
                        and np.all(m.bounds[1, :2] > centre * 0.001)
                    ]
                    if containing:
                        if sign > 0:
                            hi = max(m.bounds[1, 2] * 1000 for m in containing)
                        else:
                            lo = min(m.bounds[0, 2] * 1000 for m in containing)
        out.contacts.append(
            Contact(
                np.array([*centre, 0]) * 0.001,
                lo * 0.001,
                hi * 0.001,
                size * 0.001,
                shape,
                opening,
                (min(diameters), max(diameters)) if diameters else None,
            )
        )
    out.contacts.sort(key=lambda c: (round(c.centre[0], 7), round(c.centre[1], 7)))
    return out


def _declared_signature(geometry: ConnectorGeometry) -> tuple[np.ndarray, float]:
    p = np.asarray([pin.position[:2] for pin in geometry.pins.values()])
    distances = cdist(p, p)
    np.fill_diagonal(distances, np.inf)
    pitch = float(np.median(distances.min(axis=1))) if len(p) > 1 else 0.0
    return p, pitch


def classify(
    detection: Detection, geometries: dict[str, ConnectorGeometry]
) -> list[str]:
    """Geometrically compatible catalog labels; ambiguous types remain a list."""
    if not detection.contacts:
        return []
    signature = detection.signature()
    found = []
    measured = np.asarray([c.centre[:2] for c in detection.contacts])
    measured -= measured.mean(axis=0)
    for label, geometry in geometries.items():
        expected, pitch = _declared_signature(geometry)
        if (
            len(expected) != len(measured)
            or abs(pitch - signature["pitch"]) > POSITION_LIMIT
        ):
            continue
        expected = expected - expected.mean(axis=0)
        distances = cdist(measured, expected)
        i, j = linear_sum_assignment(distances)
        if distances[i, j].max() > POSITION_LIMIT:
            continue
        if geometry.pin_shape in ("bullet", "coax") and signature["shape"] != "round":
            continue
        if geometry.pin_shape == "bullet" and any(
            not contact.opening
            and contact.diameter_range is not None
            and not contact.diameter_range[0] - POSITION_LIMIT
            <= geometry.pin_size
            <= contact.diameter_range[1] + POSITION_LIMIT
            for contact in detection.contacts
        ):
            continue
        # Socket cavities are sized for the pin, not necessarily the same size
        # as the metal spring. Shape alone cannot identify an electrical signal.
        if geometry.pin_shape == "square_post" and signature["shape"] != "rectangular":
            continue
        if geometry.pin_shape == "square_post" and not any(
            c.opening for c in detection.contacts
        ):
            if abs(max(signature["size"]) - geometry.pin_size) > POSITION_LIMIT:
                continue
        found.append(label)
    return sorted(found)


def detect_supplied(
    owner_bodies: list[trimesh.Trimesh],
    connector_bodies: list[trimesh.Trimesh],
    frame: Frame,
    geometry: ConnectorGeometry,
    source: str = "",
    supplier_unverifiable: bool = False,
    proxy_bodies: list[trimesh.Trimesh] | None = None,
) -> Detection:
    """Search all sourced suppliers; a missing/proxy supplier cannot prove absence.

    Coincident contacts can be exported both in an assembly and a separate
    connector model. Deduplicate section loops in detect, not physical pins.
    """
    result = detect([*owner_bodies, *connector_bodies], frame, geometry, source)
    # Query the proxy at its actual placement for occupancy, but never use its
    # invented contacts or solids as physical verification evidence.
    if proxy_bodies:
        proxy = detect(proxy_bodies, frame, geometry)
        result.geometry_present |= proxy.geometry_present
        supplier_unverifiable = True
    result.supplier_unverifiable = supplier_unverifiable
    if not result.contacts and supplier_unverifiable:
        result.reason = "Separate connector model is missing or an unverified proxy; physical measurement required."
    return result


def check_side(
    ref: str,
    detection: Detection,
    geometry: ConnectorGeometry,
    declared: str,
    geometries: dict[str, ConnectorGeometry],
) -> tuple[dict[str, Any], list[Diagnostic]]:
    labels = classify(detection, geometries)
    record = {**detection.json(), "declared_type": declared, "compatible_types": labels}
    diagnostics = []
    if not detection.contacts:
        diagnostics.append(
            (Diagnostic.warning if detection.geometry_present or detection.supplier_unverifiable else Diagnostic.error)(
                "connector_unverifiable" if detection.geometry_present or detection.supplier_unverifiable else "connector_empty",
                f"{ref}: {detection.reason}", subjects=[ref]
            )
        )
    elif declared not in labels:
        diagnostics.append(
            Diagnostic.error(
                "connector_type_mismatch",
                f"{ref}: detected {detection.signature()['type']}; declared {declared}",
                subjects=[ref],
                data={
                    "detected_types": labels,
                    "declared_type": declared,
                    "detected": detection.signature(),
                },
            )
        )
    return record, diagnostics


def check_pair(
    a: str,
    b: str,
    fa: Frame,
    fb: Frame,
    da: Detection,
    db: Detection,
    ga: ConnectorGeometry,
    gb: ConnectorGeometry,
    types: tuple[str, str],
    geometries: dict[str, ConnectorGeometry],
    min_insertion: float = 0.0,
) -> tuple[dict[str, Any], list[Diagnostic]]:
    diagnostics = []
    record = {
        "a": a,
        "b": b,
        "sides": {},
        "max_lateral_error": None,
        "min_insertion": None,
        "required_insertion": min_insertion,
    }
    for ref, det, g, declared in ((a, da, ga, types[0]), (b, db, gb, types[1])):
        side, issues = check_side(ref, det, g, declared, geometries)
        record["sides"][ref] = side
        diagnostics.extend(issues)
    if not da.contacts or not db.contacts:
        return record, diagnostics
    pa = np.asarray([fa.point(c.centre) for c in da.contacts])
    pb = np.asarray([fb.point(c.centre) for c in db.contacts])
    # Compare in a's mating plane, with one-to-one assignment (nearest-neighbour
    # alone incorrectly lets two posts engage the same socket).
    aa = fa.inverse().points(pa)
    bb = fa.inverse().points(pb)
    distance = cdist(aa[:, :2], bb[:, :2])
    ii, jj = linear_sum_assignment(distance)
    worst = float(distance[ii, jj].max())
    record["max_lateral_error"] = worst
    missing = len(pa) != len(pb) or worst > LATERAL_LIMIT
    if missing:
        diagnostics.append(
            Diagnostic.error(
                "connector_pin_unmated",
                f"{a} / {b}: a contact has no partner within {LATERAL_LIMIT * 1000:g} mm",
                subjects=[a, b],
                data={
                    "counts": [len(pa), len(pb)],
                    "max_lateral_error": worst,
                    "limit": LATERAL_LIMIT,
                },
            )
        )
    insertion = []
    for i, j in zip(ii, jj):
        if distance[i, j] > LATERAL_LIMIT:
            continue
        ca, cb = da.contacts[i], db.contacts[j]
        post, socket = (ca, cb) if cb.opening else (cb, ca)
        if (
            socket.opening
            and not post.opening
            and np.any(post.size > socket.size + POSITION_LIMIT)
        ):
            diagnostics.append(
                Diagnostic.error(
                    "connector_type_mismatch",
                    f"{a} / {b}: physical pin exceeds partner entry opening",
                    subjects=[a, b],
                    data={
                        "pin_size": post.size.tolist(),
                        "opening_size": socket.size.tolist(),
                    },
                )
            )
        za = sorted(
            [
                float((fa.point(ca.centre + np.array([0, 0, t])) - fa.origin) @ fa.z)
                for t in (ca.low, ca.high)
            ]
        )
        zb = sorted(
            [
                float((fb.point(cb.centre + np.array([0, 0, t])) - fa.origin) @ fa.z)
                for t in (cb.low, cb.high)
            ]
        )
        insertion.append(min(za[1], zb[1]) - max(za[0], zb[0]))
    if insertion:
        record["min_insertion"] = min(insertion)
        if min(insertion) + 1e-7 < min_insertion:
            diagnostics.append(
                Diagnostic.error(
                    "connector_insertion_short",
                    f"{a} / {b}: measured insertion {min(insertion) * 1000:.3f} mm < {min_insertion * 1000:.3f} mm",
                    subjects=[a, b],
                    data={"insertion": min(insertion), "required": min_insertion},
                )
            )
    volume, complete = body_intersection(da, db, fa, fb)
    record.update(body_interference_volume=volume, body_interference_complete=complete)
    if volume > VOLUME_LIMIT:
        diagnostics.append(
            Diagnostic.error(
                "connector_body_interference",
                f"{a} / {b}: housings/part bodies intersect by {volume * 1e9:.3f} mm^3 outside contact channels",
                subjects=[a, b],
                data={"volume": volume, "limit": VOLUME_LIMIT},
            )
        )
    if not complete:
        diagnostics.append(
            Diagnostic.warning(
                "connector_unverifiable",
                f"{a} / {b}: open model surfaces prevent a complete housing volume check",
                subjects=[a, b],
            )
        )
    return record, diagnostics


def body_intersection(
    da: Detection, db: Detection, fa: Frame, fb: Frame
) -> tuple[float, bool]:
    """Solid overlap outside measured contact corridors, via manifold3d."""
    complete = True
    volume = 0.0
    masks = []
    for contact in da.contacts:
        size = np.array(
            [
                *np.maximum(contact.size, LATERAL_LIMIT * 2),
                max(contact.high - contact.low, 0.001),
            ]
        )
        size[:2] += LATERAL_LIMIT * 2
        mask = trimesh.creation.box(extents=size)
        mask.apply_translation(
            contact.centre + [0, 0, (contact.low + contact.high) / 2]
        )
        mask.apply_transform(fa.matrix())
        masks.append(mask)
    for left in da.bodies:
        x = left.copy()
        x.apply_transform(fa.matrix())
        for right in db.bodies:
            y = right.copy()
            y.apply_transform(fb.matrix())
            if np.any(
                np.minimum(x.bounds[1], y.bounds[1])
                - np.maximum(x.bounds[0], y.bounds[0])
                <= 0
            ):
                continue
            if not x.is_volume or not y.is_volume:
                complete = False
                continue
            try:
                hit = trimesh.boolean.intersection([x, y], engine="manifold")
                if hit.volume <= VOLUME_LIMIT:
                    continue
                for mask in masks:
                    hit = trimesh.boolean.difference([hit, mask], engine="manifold")
                    if hit.volume <= VOLUME_LIMIT:
                        break
                volume += max(0.0, float(hit.volume))
            except (ValueError, RuntimeError):
                complete = False
    return volume, complete


def check_solder(
    part: str, lead: str, frame: Frame, pads: Detection, routes: dict[str, Any] | None
) -> tuple[dict[str, Any], list[Diagnostic]]:
    """A soldered lead occupies only wired pads; unused holes need no partner.

    Check conductor ends projected onto the pad face. The short exposed end may
    finish inside the plated hole; its lateral location must still be measured
    from the routed conductor rather than manufactured from declared pad centres.
    """
    diagnostics: list[Diagnostic] = []
    record: dict[str, Any] = {
        "a": part,
        "b": lead,
        "mate": "solder",
        "sides": {part: pads.json()},
        "wires": {},
    }
    if not pads.contacts:
        diagnostics.append(
            (Diagnostic.warning if pads.geometry_present or pads.supplier_unverifiable else Diagnostic.error)(
                "connector_unverifiable" if pads.geometry_present or pads.supplier_unverifiable else "connector_empty",
                f"{part}: {pads.reason}", subjects=[part, lead]
            )
        )
    elif routes is None:
        diagnostics.append(
            Diagnostic.warning(
                "connector_unverifiable",
                f"{part} / {lead}: routed conductor ends unavailable",
                subjects=[part, lead],
            )
        )
    else:
        centres = np.array([contact.centre[:2] for contact in pads.contacts])
        occupied: set[int] = set()
        for wire_id, wire in routes.get("wires", {}).items():
            for index, end in enumerate(wire.get("ends") or []):
                if end is None or end.get("connector") != lead:
                    continue
                point = frame.inverse().point(wire["path"][0 if index == 0 else -1])
                distances = np.linalg.norm(centres - point[:2], axis=1)
                nearest = int(distances.argmin())
                error = float(distances[nearest])
                record["wires"][wire_id] = {
                    "pad": nearest,
                    "lateral_error": error,
                    "axial_offset": float(point[2]),
                }
                if error > LATERAL_LIMIT or nearest in occupied:
                    diagnostics.append(
                        Diagnostic.error(
                            "connector_pin_unmated",
                            f"{lead}: {wire_id} has no unique pad under its conductor end",
                            subjects=[part, lead, wire_id],
                            data={"lateral_error": error, "limit": LATERAL_LIMIT},
                        )
                    )
                occupied.add(nearest)
        if not record["wires"]:
            diagnostics.append(
                Diagnostic.warning(
                    "connector_unverifiable",
                    f"{part} / {lead}: no routed conductor ends found",
                    subjects=[part, lead],
                )
            )
    record["max_lateral_error"] = max(
        (w["lateral_error"] for w in record["wires"].values()), default=None
    )
    record["status"] = (
        "failed"
        if any(d.severity.value == "error" for d in diagnostics)
        else "unverifiable"
        if diagnostics
        else "passed"
    )
    record["diagnostics"] = [d.model_dump(mode="json") for d in diagnostics]
    return record, diagnostics
