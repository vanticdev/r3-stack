"""Cable pass-throughs designed into the CAD: through ``slot`` features of structure parts.

A bundle that passes through such an opening is held there by the grommet edge
strip the opening carries, like a hole the router drills (``PassThrough``), but
nothing is drilled: the opening is already in the part. They are added to
``routing.holes`` after planning, so the span check counts them as held points
and the manufacturing package lists the grommet without a machining step.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from r3_model.assembly import pose_matrix
from r3_model.project import Project

from .geometry import resample_polyline
from .router import PassThrough

TOLERANCE = 0.001  # m beyond the slot faces that still counts as passing through


def cad_slots(project: Project) -> list[dict[str, Any]]:
    """Through slots of structure parts, robot frame: centre (mid-depth), axis, slot direction, sizes."""
    out = []
    for cid, comp in project.spec.components.items():
        ctype = project.catalog.components.get(comp.type)
        if ctype is None or ctype.kind != "structure":
            continue
        pose = pose_matrix(comp.pose.xyz, comp.pose.rpy)
        rotation, origin = pose[:3, :3], pose[:3, 3]
        for feature in ctype.mechanical.features:
            if feature.kind != "slot" or not feature.slot_dir or float(feature.depth) <= 0:
                continue
            axis = rotation @ np.asarray(feature.axis, float)
            axis /= np.linalg.norm(axis)
            along = rotation @ np.asarray(feature.slot_dir, float)
            along /= np.linalg.norm(along)
            entry = rotation @ np.asarray(feature.position, float) + origin
            out.append({"component": cid, "id": feature.id, "entry": entry, "axis": axis, "along": along,
                        "length": float(feature.length), "width": float(feature.diameter), "depth": float(feature.depth)})
    return out


def _inside(points: np.ndarray, slot: dict[str, Any]) -> np.ndarray:
    rel = points - slot["entry"]
    t = rel @ slot["axis"]
    lateral = rel - np.outer(t, slot["axis"])
    u = np.clip(lateral @ slot["along"], -slot["length"] / 2, slot["length"] / 2)
    distance = np.linalg.norm(lateral - np.outer(u, slot["along"]), axis=1)
    return (t >= -TOLERANCE) & (t <= slot["depth"] + TOLERANCE) & (distance <= slot["width"] / 2)


def add_cad_pass_throughs(project: Project, routing: Any) -> list[PassThrough]:
    """Register every CAD slot a routed wire passes through as a grommeted pass-through."""
    added: list[PassThrough] = []
    for slot in cad_slots(project):
        hole = None
        for wire in routing.wires.values():
            if wire.failed or len(wire.path) < 2:
                continue
            points, _ = resample_polyline(wire.path, 0.0005)
            if not _inside(points, slot).any():
                continue
            if hole is None:
                center = slot["entry"] + slot["axis"] * slot["depth"] / 2
                hole = PassThrough(len(routing.holes), slot["component"], int(np.argmax(np.abs(slot["axis"]))), center,
                                   diameter=slot["width"], kind="cad_slot",
                                   label=f"{slot['id']} ({(slot['length'] + slot['width']) * 1e3:.0f} x {slot['width'] * 1e3:.0f} mm)")
                routing.holes.append(hole)
                added.append(hole)
            hole.harnesses.add(wire.harness)
            hole.wires.add(wire.wire_id)
    return added


__all__ = ["add_cad_pass_throughs", "cad_slots"]
