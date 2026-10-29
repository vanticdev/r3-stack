"""Printability checks: wall thickness, threads, inserts, nut traps, counterbores, holes.

Design rules and their sources:

* Walls: below one extrusion width a wall does not print; two perimeters of a
  0.4 mm nozzle (~0.9 mm) are the practical minimum and structural walls want
  3 or more (Prusa KB "Modeling with 3D printing in mind",
  https://help.prusa3d.com/en/article/modeling-with-3d-printing-in-mind_164135/;
  Hubs: walls over 0.8 mm print on all printers).
* Brass heat-set inserts instead of threads in plastic: hole diameter, length and
  minimum wall per thread size from the CNC Kitchen standard insert table
  (https://www.3djake.com/cnc-kitchen/threaded-inserts-set-standard; Ruthex
  publishes the same M3 values), blind hole depth = insert length + 1 mm
  (DigiKey tutorial crediting CNC Kitchen,
  https://www.digikey.com/es/maker/tutorials/2025/intro-to-freecad-part-4-how-to-use-heat-set-inserts).
  Hubs: avoid FDM-printed threads below M5; self-tapping into a 75-80 %
  engagement pilot is acceptable for low loads and few assembly cycles
  (https://www.hubs.com/knowledge-base/how-assemble-3d-printed-parts-threaded-fasteners/).
* Nut traps: across-flats plus the FDM clearance of the print-fit table (Hubs:
  0.2 mm total for a loose captive nut), nut height + 0.2 mm deep.
* Counterbores: screw head diameter + 2 x the print-fit cap clearance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import trimesh

from r3_model.diagnostics import Diagnostic

#: CNC Kitchen standard heat-set inserts (mm): length L, hole diameter, minimum wall around the insert.
HEAT_SET_INSERTS: dict[str, dict[str, float]] = {
    "M2": {"length": 3.0, "hole": 3.2, "outer": 3.6, "wall": 1.3},
    "M2.5": {"length": 4.0, "hole": 4.0, "outer": 4.6, "wall": 1.6},
    "M3": {"length": 5.7, "hole": 4.0, "outer": 4.6, "wall": 1.6},
    "M4": {"length": 8.1, "hole": 5.6, "outer": 6.3, "wall": 2.1},
    "M5": {"length": 9.5, "hole": 6.4, "outer": 7.1, "wall": 2.6},
}
INSERT_SOURCE = "CNC Kitchen standard insert table (https://www.3djake.com/cnc-kitchen/threaded-inserts-set-standard)"
INSERT_DEPTH_EXTRA_MM = 1.0  # blind hole deeper than the insert for displaced plastic
NUT_TRAP_DEPTH_EXTRA_MM = 0.2
SCREW_KINDS = ("socket_head_screw", "button_head_screw")
WALL_TOLERANCE_MM = 0.01  # a wall drawn at exactly the limit passes (tessellation noise)


@dataclass
class WallResult:
    min_mm: float
    location_mm: list[float]
    typical_mm: float
    thin_fraction_error: float  # sampled surface fraction below the error limit
    thin_fraction_warning: float
    samples: int
    thickness: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    points: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 3)))


def wall_thickness(mesh: trimesh.Trimesh, samples: int = 3000, seed: int = 7) -> WallResult:
    """Local thickness by ray casting from surface samples along the inward normal (trimesh.ray):
    the distance to the opposite surface. Area-weighted samples, deterministic seed."""
    points, faces = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    inward = -mesh.face_normals[faces]
    origins = points + inward * 1e-4
    locations, rays, _ = mesh.ray.intersects_location(origins, inward, multiple_hits=True)
    thickness = np.full(len(points), np.inf)
    if len(rays):
        distance = np.einsum("ij,ij->i", locations - origins[rays], inward[rays])
        valid = distance > 1e-3  # ignore the start face and edge-grazing hits
        np.minimum.at(thickness, rays[valid], distance[valid])
    finite = np.isfinite(thickness)
    if not finite.any():
        return WallResult(math.inf, [], math.inf, 0.0, 0.0, len(points), thickness, points)
    k = int(np.argmin(np.where(finite, thickness, np.inf)))
    return WallResult(min_mm=float(thickness[k]), location_mm=points[k].tolist(), typical_mm=float(np.median(thickness[finite])),
                      thin_fraction_error=0.0, thin_fraction_warning=0.0, samples=len(points), thickness=thickness, points=points)


def check_walls(cid: str, walls: WallResult, nozzle_mm: float, min_wall_mm: float) -> list[Diagnostic]:
    """Error below two extrusion widths (2 x nozzle), warning below spec.fabrication.min_wall.
    A finding needs >= 0.2 % of the sampled surface (and 3 samples) so one grazing ray is not one."""
    out: list[Diagnostic] = []
    finite = np.isfinite(walls.thickness)
    if not finite.any():
        return out
    needed = max(3, int(0.002 * walls.samples))
    for limit, severity in ((2 * nozzle_mm, "error"), (min_wall_mm, "warning")):
        thin = finite & (walls.thickness < limit - WALL_TOLERANCE_MM)
        if thin.sum() < needed:
            continue
        k = np.nonzero(thin)[0][int(np.argmin(walls.thickness[thin]))]
        data = {"min_thickness": float(walls.thickness[k]) * 1e-3, "limit": limit * 1e-3, "location": (walls.points[k] * 1e-3).tolist(),
                "thin_area_fraction": float(thin.mean()), "nozzle": nozzle_mm * 1e-3, "frame": "component"}
        message = (f"{cid}: wall {walls.thickness[k]:.2f} mm thick at {np.round(walls.points[k], 1).tolist()} mm "
                   f"(< {limit:.2f} mm{', two extrusion widths' if severity == 'error' else ', fabrication.min_wall'}; "
                   f"{100 * thin.mean():.1f} % of the surface)")
        hint = ("Thicken the wall to a whole number of perimeters (0.45 mm each for a 0.4 mm nozzle, Prusa KB); "
                "below two widths the slicer drops or gap-fills it.")
        make = Diagnostic.error if severity == "error" else Diagnostic.warning
        out.append(make("print_wall_thin", message, subjects=[cid], data=data, hint=hint))
        break  # the error already covers the warning band
    return out


# ----------------------------------------------------------------------------- fasteners


def _thread_name(d: float) -> str:
    return f"M{d * 1e3:g}"


def local_thickness(mesh: trimesh.Trimesh, point_mm: np.ndarray, direction: np.ndarray) -> float | None:
    """Material length (mm) along ``direction`` from a point on the surface (first exit)."""
    direction = np.asarray(direction, dtype=float) / np.linalg.norm(direction)
    origin = np.asarray(point_mm, dtype=float) - direction * 0.05
    locations, _, _ = mesh.ray.intersects_location([origin], [direction], multiple_hits=True)
    if not len(locations):
        return None
    distance = np.sort((locations - origin) @ direction)
    distance = distance[distance > 0.1]
    return float(distance[0] - 0.05) if len(distance) else None


def to_component(project: Any, cid: str, point_m: list[float], axis: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Robot-frame point (m) and axis -> component frame (mm, unit vector)."""
    xyz, rotation = project.graph.component_pose(cid)
    local = rotation.T @ (np.asarray(point_m, dtype=float) - xyz) * 1e3
    return local, rotation.T @ np.asarray(axis, dtype=float)


def fastener_checks(project: Any, printed: dict[str, Any], meshes: dict[str, trimesh.Trimesh]) -> tuple[list[Diagnostic], dict[str, list[dict[str, Any]]]]:
    """Threads, inserts, nut traps and counterbores for every derived fastener touching a printed part.
    Returns diagnostics and per-part records for plan.json."""
    out: list[Diagnostic] = []
    records: dict[str, list[dict[str, Any]]] = {cid: [] for cid in printed}
    catalog = project.catalog
    for fastener in project.spec.mechanical.fasteners:
        if fastener.type not in catalog.fasteners:
            continue
        ftype = catalog.fastener(fastener.type)
        insert_used = ftype.kind == "heat_set_insert"
        host = fastener.thread_into
        if host in printed and ftype.kind != "nut":
            out.append(_tapped_hole(project, fastener, ftype, host, printed[host], insert_used, records))
        if host in printed and ftype.kind == "nut":
            out.append(Diagnostic.warning(
                "printed_male_thread", f"{host}: nut {fastener.id} ({ftype.thread}) runs on a thread printed in {printed[host].material.name}",
                subjects=[host, fastener.id], data={"thread": ftype.thread, "d": ftype.d},
                hint="FDM threads below M5 should be avoided (Hubs); use a metal stud or a heat-set insert with a screw."))
        if fastener.nut and len(fastener.clamps) >= 2 and fastener.clamps[1] in printed:
            out.extend(_nut_trap(project, fastener, catalog.fastener(fastener.nut), fastener.clamps[1], printed, meshes, records))
        if ftype.kind in SCREW_KINDS and fastener.clamps and fastener.clamps[0] in printed and ftype.head_diameter:
            out.append(_counterbore(project, fastener, ftype, fastener.clamps[0], printed, records))
    return out, records


def _tapped_hole(project: Any, fastener: Any, ftype: Any, host: str, part: Any, insert_used: bool,
                 records: dict[str, list[dict[str, Any]]]) -> Diagnostic:
    thread = _thread_name(ftype.d)
    insert = HEAT_SET_INSERTS.get(thread)
    local, _ = to_component(project, host, fastener.position, fastener.axis)
    record: dict[str, Any] = {"fastener": fastener.id, "type": fastener.type, "kind": "tapped_hole", "thread": thread,
                              "position_mm": np.round(local, 3).tolist()}
    if insert_used:
        record["insert"] = "catalog heat-set insert"
        records[host].append(record)
        return Diagnostic.info("heat_set_insert", f"{host}: {fastener.id} uses a heat-set insert ({fastener.type})",
                               subjects=[host, fastener.id], data={"thread": thread})
    data: dict[str, Any] = {"thread": thread, "d": ftype.d, "fastener_type": fastener.type, "join": fastener.join or "",
                            "position": (local * 1e-3).tolist(), "frame": "component"}
    if insert:
        boss = insert["hole"] + 2 * insert["wall"]
        data.update({"insert_hole_d": insert["hole"] * 1e-3, "insert_length": insert["length"] * 1e-3,
                     "insert_hole_depth": (insert["length"] + INSERT_DEPTH_EXTRA_MM) * 1e-3,
                     "insert_min_wall": insert["wall"] * 1e-3, "boss_min_d": boss * 1e-3, "source": INSERT_SOURCE})
        record.update({"recommendation": "heat-set insert", "insert_hole_d_mm": insert["hole"],
                       "insert_hole_depth_mm": insert["length"] + INSERT_DEPTH_EXTRA_MM, "boss_min_d_mm": boss})
        hint = (f"Use a brass heat-set insert: {insert['hole']:.1f} mm hole, {insert['length'] + INSERT_DEPTH_EXTRA_MM:.1f} mm deep "
                f"({insert['length']:.1f} mm insert + 1 mm), >= {insert['wall']:.1f} mm wall around it (boss >= {boss:.1f} mm); "
                f"insert at 10-20 deg C above the print temperature. Self-tapping {thread} into a {0.87 * ftype.d * 1e3:.1f}-"
                f"{0.97 * ftype.d * 1e3:.1f} mm printed pilot (75-80 % engagement, Hubs) is acceptable only for low loads and few "
                f"assembly cycles.")
    else:
        hint = f"No insert size is tabulated for {thread}; use a through bolt with a nut or a metal threaded part."
    records[host].append(record)
    return Diagnostic.warning(
        "printed_tapped_hole", f"{host}: {fastener.id} ({fastener.type}) threads directly into printed {part.material.name}",
        subjects=[host, fastener.id], data=data, hint=hint)


def _nut_trap(project: Any, fastener: Any, nut: Any, host: str, printed: dict[str, Any], meshes: dict[str, trimesh.Trimesh],
              records: dict[str, list[dict[str, Any]]]) -> list[Diagnostic]:
    """Captive-nut pocket for a through bolt's nut on a printed part, and whether the part can hold it."""
    part = printed[host]
    fit = part.print_fit
    clearance = fit.hole_clearance[0] if fit else 0.2e-3
    across = (nut.across_flats or 0.0) + clearance
    depth = (nut.height or 0.0) + NUT_TRAP_DEPTH_EXTRA_MM * 1e-3
    join_holes = _host_holes(project, fastener, host)
    thickness = join_holes[0] if join_holes else None
    if thickness is None and host in meshes:
        # Nut seat: the bolt axis leaves the nut-side part on its far face; measure back along the axis.
        local, axis = to_component(project, host, fastener.position, fastener.axis)
        measured = local_thickness(meshes[host], local + axis * 1e3, -axis)
        thickness = measured * 1e-3 if measured else None
    min_wall = float(project.spec.fabrication.min_wall)
    data = {"nut": nut.id, "trap_across_flats": across, "trap_depth": depth, "clearance": clearance,
            "part_thickness": thickness, "min_floor": min_wall}
    records[host].append({"fastener": fastener.id, "kind": "nut_trap", "nut": nut.id, "trap_across_flats_mm": round(across * 1e3, 3),
                          "trap_depth_mm": round(depth * 1e3, 3), "part_thickness_mm": round(thickness * 1e3, 3) if thickness else None})
    out = [Diagnostic.info(
        "nut_trap", f"{host}: nut trap for {fastener.id} ({nut.id}): hex {across * 1e3:.2f} mm across flats x {depth * 1e3:.2f} mm deep",
        subjects=[host, fastener.id], data=data,
        hint="Across flats + FDM hole clearance (print_fits.yaml), nut height + 0.2 mm; print the hex with a flat parallel to the bed.")]
    if thickness is not None and thickness < depth + min_wall:
        out.append(Diagnostic.warning(
            "nut_trap_too_shallow",
            f"{host}: {thickness * 1e3:.1f} mm of material at {fastener.id} cannot hold a {depth * 1e3:.1f} mm nut trap "
            f"plus a {min_wall * 1e3:.1f} mm floor",
            subjects=[host, fastener.id], data={**data, "required_thickness": depth + min_wall},
            hint=f"Add a local boss of >= {(depth + min_wall) * 1e3:.1f} mm around the hole, or seat the nut on the surface with a "
                 "washer (bearing on printed plastic: check creep), or use a heat-set insert."))
    return out


def _host_holes(project: Any, fastener: Any, host: str) -> list[float]:
    """Depths (m) of the host's holes in the fastener's join (its plate thickness at the bolt)."""
    result = project.assembly
    if result is None or not fastener.join or fastener.join not in result.valid:
        return []
    a, b = result.valid[fastener.join]
    mine = a if a.obj == host else b if b.obj == host else None
    if mine is None or mine.kind not in ("hole_pattern", "face_mount"):
        return []
    return [h.depth for h in mine.holes if h.depth]


def _counterbore(project: Any, fastener: Any, ftype: Any, host: str, printed: dict[str, Any],
                 records: dict[str, list[dict[str, Any]]]) -> Diagnostic:
    part = printed[host]
    cap = part.print_fit.cap_clearance if part.print_fit else 0.3e-3
    diameter = ftype.head_diameter + 2 * cap
    depth = (ftype.head_height or 0.0) + NUT_TRAP_DEPTH_EXTRA_MM * 1e-3
    local, _ = to_component(project, host, fastener.position, fastener.axis)
    records[host].append({"fastener": fastener.id, "kind": "screw_head", "type": fastener.type,
                          "counterbore_d_mm": round(diameter * 1e3, 3), "counterbore_depth_mm": round(depth * 1e3, 3),
                          "position_mm": np.round(local, 3).tolist()})
    return Diagnostic.info(
        "printed_counterbore",
        f"{host}: head of {fastener.id} ({fastener.type}) seats on printed {part.material.name}: counterbore "
        f"{diameter * 1e3:.2f} mm (head {ftype.head_diameter * 1e3:.2f} + 2 x {cap * 1e3:.2f} mm) x {depth * 1e3:.2f} mm if recessed",
        subjects=[host, fastener.id], data={"head_diameter": ftype.head_diameter, "counterbore_d": diameter, "counterbore_depth": depth,
                                            "cap_clearance": cap, "position": (local * 1e-3).tolist(), "frame": "component"},
        hint="A surface-seated head is fine; preload into plastic creeps, so prefer a washer and do not over-torque.")


def hole_clearance_checks(project: Any, printed: dict[str, Any]) -> tuple[list[Diagnostic], dict[str, list[dict[str, Any]]]]:
    """Printed clearance holes vs the FDM print-fit range (hole diameter - nominal screw diameter)."""
    from r3_model.assembly import parse_fastener

    out: list[Diagnostic] = []
    records: dict[str, list[dict[str, Any]]] = {cid: [] for cid in printed}
    result = project.assembly
    if result is None:
        return out, records
    joins = {j.join_id: j for j in project.spec.assembly.joins} if project.spec.assembly else {}
    for join_id, (a, b) in result.valid.items():
        join = joins.get(join_id)
        for mine, other in ((a, b), (b, a)):
            part = printed.get(mine.obj)
            if part is None or part.print_fit is None or mine.kind not in ("hole_pattern", "face_mount"):
                continue
            parsed = parse_fastener(join.fasteners.type) if join and join.fasteners else None
            others = other.holes if other.kind in ("hole_pattern", "face_mount") else []
            for index, hole in enumerate(mine.holes):
                if hole.kind != "clearance":
                    continue
                nominal = parsed[0] if parsed else next((h.d for h in others if h.kind in ("thread", "tapped")), None)
                if nominal is None:
                    continue
                clearance = hole.d - nominal
                low, high = part.print_fit.hole_clearance
                records[mine.obj].append({"interface": mine.name, "hole": index + 1, "d_mm": round(hole.d * 1e3, 3),
                                          "nominal_mm": round(nominal * 1e3, 3), "clearance_mm": round(clearance * 1e3, 3),
                                          "range_mm": [round(low * 1e3, 3), round(high * 1e3, 3)]})
                if low - 1e-9 <= clearance <= high + 1e-9:
                    continue
                out.append(Diagnostic.warning(
                    "printed_hole_clearance",
                    f"{mine.obj}.{mine.name} hole {index + 1}: {hole.d * 1e3:.2f} mm for {_thread_name(nominal)} is "
                    f"{clearance * 1e3:+.2f} mm clearance, outside the FDM range {low * 1e3:.1f}-{high * 1e3:.1f} mm",
                    subjects=[mine.obj, join_id], data={"d": hole.d, "nominal": nominal, "clearance": clearance, "range": [low, high]},
                    hint=f"Use a {(nominal + (low + high) / 2) * 1e3:.2f} mm hole (printed holes shrink; print_fits.yaml)."))
    return out, records
