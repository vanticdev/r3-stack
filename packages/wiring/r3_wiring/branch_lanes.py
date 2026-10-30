"""Independent terminal-pair lanes for loose branched harnesses.

A topological fork at one centreline cannot represent finite-width conductors.
Route each terminal pair separately, reserving earlier lanes in both the voxel
and curvature planners. Actual solder joins retain the checker's existing local
join region; no incidental shared-core exemption is added.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import numpy as np

from r3_model.spec import Harness

from .grid import FIELD_CUTOFF
from .planner import BundlePlan, ClearanceField, Junction, Segment, occupancy_field, plan_bundle


def _terminal(ref: str, harness: Harness) -> str:
    return ref if ref in harness.splices else ref.split('.', 1)[0]


def _join_reach(node: Any) -> float:
    return 0.003 + node.od if node.kind == 'splice' else max(node.fan_length, 2 * node.od)


def lane_field(router: Any, nodes: dict[str, dict[str, Any]], current: set[tuple[str, str]],
               previous: list[tuple[set[tuple[str, str]], list[tuple[np.ndarray, float]]]]) -> np.ndarray:
    """Reserve earlier lanes, opening only their common physical terminals."""
    field = np.full(router.grid.size, FIELD_CUTOFF)
    centers = router.grid.centers()
    for terminals, runs in previous:
        occupied = occupancy_field(router.grid, [(p, r, set()) for p, r in runs], set())
        for harness, name in current & terminals:
            node = nodes[harness][name]
            # This is exactly the existing common-terminal region. Other wires
            # never get an aperture at this splice/connector.
            inside = np.linalg.norm(centers - node.exit, axis=1) <= _join_reach(node)
            occupied[inside] = FIELD_CUTOFF
        np.minimum(field, occupied, out=field)
    return field


def route_lanes(router: Any, harness: Harness, nodes: dict[str, Any]) -> Any:
    """Use the normal router for each terminal pair, with occupied lanes present."""
    from .geometry import bundle_diameter
    from .router import HarnessRoute

    branches: dict[tuple[str, str], list[Any]] = {}
    for wire in harness.wires:
        key = tuple(sorted((wire.from_, wire.to)))
        branches.setdefault(key, []).append(wire)
    order = sorted(branches, key=lambda k: (-sum(router._wire_od(w.awg) ** 2 for w in branches[k]), k))
    original_fields = router.fields
    original_hardware = router._routing_hardware
    previous = []
    routes, wires = [], {}
    try:
        for index, pin_pair in enumerate(order):
            members = branches[pin_pair]
            key = tuple(dict.fromkeys(_terminal(ref, harness) for ref in pin_pair))
            hints = harness.route.model_copy(update={
                'independent_branches': False,
                'via': harness.route.via if index == 0 else [],
                'wire_via': {w.id: harness.route.wire_via[w.id] for w in members if w.id in harness.route.wire_via},
            })
            part = harness.model_copy(update={'wires': members, 'route': hints})
            local_nodes = copy.deepcopy(nodes)
            for name in key:
                node = local_nodes[name]
                refs = [(ref, w.awg) for w in members for ref in (w.from_, w.to)
                        if _terminal(ref, harness) == name]
                node.od = bundle_diameter(router._wire_od(awg) for _, awg in refs)
                if node.kind in ('connector', 'floating'):
                    ends = [router._pin_end(node, ref, {}) for ref, _ in refs]
                    points = [end.position for end in ends if end is not None]
                    if points:
                        # Separate lane axes already at the actual pin row. The
                        # original connector frame continues to locate each pin.
                        center = np.mean(points, axis=0)
                        offset = center - node.exit
                        offset -= (offset @ node.direction) * node.direction
                        node.exit += offset
                        router._lead(node, [awg for _, awg in refs])
            terminals = {(harness.id, name) for name in key}
            occupied = lane_field(router, {harness.id: nodes}, terminals, previous)
            hardware = np.minimum(original_hardware, occupied)
            router._routing_hardware = hardware
            router.fields = replace(original_fields,
                                    static_clear=np.minimum(original_fields.static_clear, occupied),
                                    static_raw=np.minimum(original_fields.static_raw, occupied))
            router._contexts.clear()
            router._graphs.clear()
            routed = router._route_harness(part, local_nodes)
            if routed is None:
                continue
            route, branch_wires = routed
            routes.append(route)
            wires.update(branch_wires)
            runs = [run for wire in branch_wires.values() for run in router._occupied_runs(wire)]
            previous.append((terminals, runs))
    finally:
        router.fields, router._routing_hardware = original_fields, original_hardware
        router._contexts.clear()
        router._graphs.clear()
    if not routes:
        return None
    trunk = max(routes, key=lambda route: route.length)
    return HarnessRoute(harness.id, trunk.bundle_path,
                        [point for route in routes for point in route.tie_points],
                        [segment for route in routes for segment in route.segments],
                        list(wires), sorted({hole for route in routes for hole in route.holes}), harness.jacket), wires


def plan_lanes(project: Any, router: Any, routing: Any, segments: list[Segment],
               junctions: dict[str, Junction], fields: ClearanceField, requirement: float,
               *, max_iterations: int, obstacles: Any) -> BundlePlan:
    """Plan connected conductor runs in separate lanes with full-radius occupancy."""
    remaining = list(segments)
    groups = []
    while remaining:
        group = [remaining.pop(0)]
        ids = set(group[0].wires)
        while True:
            matched = [s for s in remaining if ids.intersection(s.wires)]
            if not matched:
                break
            group.extend(matched)
            ids.update(w for s in matched for w in s.wires)
            matched_ids = {id(s) for s in matched}
            remaining = [s for s in remaining if id(s) not in matched_ids]
        groups.append(group)
    groups.sort(key=lambda group: (-max(s.od for s in group), tuple(sorted(w for s in group for w in s.wires))))
    previous, plans = [], []
    fixed = copy.deepcopy(junctions)
    # A common solder joint is one installed point and axis. Independently
    # solved branches cannot each move it to a different place.
    for j in fixed.values():
        if j.kind == 'splice':
            j.move, j.axis_free = 0.0, False
    for group in groups:
        wire_ids = {w for segment in group for w in segment.wires}
        terminals = {(wire.harness, name) for wid in wire_ids for wire in [routing.wires[wid]]
                     for name in (wire.from_node, wire.to_node)}
        occupied = lane_field(router, routing.nodes, terminals, previous)
        local_fields = replace(fields, static=np.minimum(fields.static, occupied),
                               hardware=np.minimum(fields.hardware, occupied) if fields.hardware is not None else occupied)
        keys = {key for s in group for key in (s.start, s.end)}
        plan = plan_bundle(group, {key: copy.deepcopy(fixed[key]) for key in keys}, local_fields,
                           requirement, max_iterations=max_iterations, obstacles=obstacles)
        plans.append(plan)
        previous.append((terminals, [(s.points, s.od / 2) for s in plan.segments]))
    return BundlePlan(sorted({s.harness for s in segments}), [s for p in plans for s in p.segments],
                      {key: j for p in plans for key, j in p.junctions.items()},
                      all(p.feasible for p in plans), [v for p in plans for v in p.violations],
                      {'independent_lanes': len(plans), 'lanes': [p.stats for p in plans]},
                      [points for p in plans for points in p.initial_points])
