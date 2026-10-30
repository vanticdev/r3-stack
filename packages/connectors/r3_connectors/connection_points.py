"""Read-only robot-frame inventory of all mating options, used and unused.

Access is a conservative centre-ray query against existing collision envelopes:
CAD AABBs where available, posed catalog envelopes otherwise. The owning part
is excluded; a null reach means no other envelope intersects the infinite ray.
It does not claim insertion/swept-tool clearance or collision-free cable routing.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import trimesh

from r3_model.project import Project

from .frames import Frame


def _envelopes(project: Project) -> dict[str, tuple[np.ndarray, np.ndarray, str]]:
    from r3_model.pulled import load_pulled
    from r3_model.pulled_view import world_mesh
    pulled = load_pulled(project.root)
    return {name: (*world_mesh(pulled, name).bounds, "pulled_aabb") for name in pulled.parts}


def _access(points: list[dict[str, Any]], envelopes: dict[str, tuple[np.ndarray, np.ndarray, str]]) -> None:
    groups: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for point in points:
        groups[point["component"]].append(point)
    for owner, group in groups.items():
        others = [(cid, lo, hi, source) for cid, (lo, hi, source) in envelopes.items() if cid != owner]
        origins = np.asarray([p["origin"] for p in group])
        directions = np.asarray([p["exit_direction"] for p in group])
        best = np.full(len(group), np.inf)
        blockers: list[str | None] = [None] * len(group)
        # trimesh performs the ray/triangle queries; use envelope meshes rather
        # than tessellating bought parts or adding a new geometric solver.
        for cid, lo, hi, source in others:
            inside = np.all((origins >= lo) & (origins <= hi), axis=1)
            for index in np.flatnonzero(inside & (best > 0)):
                best[index], blockers[index] = 0., cid
            if np.any(hi <= lo):
                continue
            box = trimesh.creation.box(extents=hi - lo)
            box.apply_translation((lo + hi) / 2)
            locations, indices, _ = box.ray.intersects_location(origins, directions, multiple_hits=True)
            if not len(indices):
                continue
            distances = np.linalg.norm(locations - origins[indices], axis=1)
            for index, distance in zip(indices, distances):
                if distance < best[index]:
                    best[index], blockers[index] = distance, cid
        for index, point in enumerate(group):
            blocker = blockers[index]
            point["free_access_reach"] = None if not np.isfinite(best[index]) else float(best[index])
            point["access_blocker"] = blocker
            point["access_geometry"] = envelopes[blocker][2] if blocker else None


def connection_points(project: Project) -> dict[str, Any]:
    """Recompute from the current spec/catalog; never build, route, or save files."""
    graph, catalog = project.graph, project.catalog
    points: list[dict[str, Any]] = []
    for cid, comp in project.spec.components.items():
        if comp.type not in catalog.components:
            continue
        ctype = catalog.component(comp.type)
        position, rotation = graph.component_pose(cid)
        component_frame = Frame(rotation, position)
        for name, inst in ctype.connectors.items():
            ref = f"{cid}.{name}"
            sites = graph.connection_sites(ref)
            occupied = graph.occupied_sites.get(ref, {})
            for site in sites:
                frame = component_frame @ Frame.from_placement(site.frame)
                occupant = occupied.get(site.id)
                blocked = [{"site": f"{ref}/{sid}", "by": by} for sid, by in occupied.items() if sid in site.blocks]
                pins = {}
                for number in site.pins:
                    signal = inst.pins.get(number)
                    node = f"C:{cid}.{signal}" if signal is not None else None
                    pins[number] = {"signal": signal, "net": graph.net_of(node) if node in graph.nodes else None}
                points.append({"id": f"{ref}/{site.id}", "component": cid, "connector": name, "site": site.id,
                               "start_pin": site.start_pin, "pins": pins, "pin_map": site.pin_map,
                               "origin": frame.origin.tolist(), "exit_direction": frame.z.tolist(), "x": frame.x.tolist(),
                               "envelope_min": site.envelope_min, "envelope_max": site.envelope_max,
                               "status": "occupied" if occupant else "blocked" if blocked else "free",
                               "occupied_by": occupant, "blocked_by": blocked,
                               "blocks": [f"{ref}/{sid}" for sid in site.blocks],
                               "compatible_types": site.compatible_types,
                               "provenance": inst.provenance.model_dump(mode="json") if inst.provenance else {"source": inst.source}})

    # Floating pairs are a single physical site. Authored positions are mating
    # face centres, never cable roots. Routing can supply a solved pair frame.
    halves = {f"{h.id}.{name}": conn for h in project.spec.electrical.harnesses for name, conn in h.connectors.items()}
    detail = project.read_build_json("wiring/routing_detail.json") or {}
    seen: set[tuple[str, str]] = set()
    for ref, conn in halves.items():
        if not conn.mates or conn.mates not in halves:
            continue
        pair = tuple(sorted((ref, conn.mates)))
        if pair in seen:
            continue
        seen.add(pair)
        partner = halves[conn.mates]
        placed = detail.get("nodes", {}).get(ref, {}).get("frame")
        centre = conn.position if conn.position is not None else partner.position
        if placed:
            frame = Frame.from_axes(placed["origin"], placed["x"], placed["z"])
        else:
            # The spec authors a centre, not an orientation. Until routing
            # supplies the axis, do not invent an exit direction/access reach.
            frame = None
        geometry = catalog.connector(conn.type).geometry
        pins = {n: {"signal": None, "net": graph.net_of(f"H:{ref}.{n}")}
                for n in (geometry.pins if geometry else [])}
        points.append({"id": f"{'|'.join(pair)}/full", "component": None, "connector": ref, "site": "full",
                       "pair": list(pair), "pins": pins,
                       "origin": frame.origin.tolist() if frame else list(centre) if centre is not None else None,
                       "exit_direction": frame.z.tolist() if frame else None, "x": frame.x.tolist() if frame else None,
                       "status": "occupied", "occupied_by": conn.mates, "blocked_by": [], "blocks": [],
                       "compatible_types": catalog.connector(conn.type).mates_with,
                       "free_access_reach": None, "access_blocker": None, "access_geometry": None,
                       "placement": "routed" if frame else "authored" if centre is not None else "unplaced"})
    _access([p for p in points if p["origin"] is not None and p["exit_direction"] is not None], _envelopes(project))
    return {"schema": "r3/connection-points@1", "units": "m, robot frame", "connection_points": points,
            "access_method": "centre ray against other parts' CAD AABBs or posed catalog AABBs; null = unbounded or unplaced",
            "diagnostics": [d.model_dump(mode="json") for d in graph.diagnostics]}
