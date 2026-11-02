"""Build a bounded CAD-derived routing graph and feed topology into the planner.

The graph is a corridor around collision-checked initial voxel routes, rather
than an unbounded materialization of the complete 26-neighbor voxel lattice.
This limits the search domain, not the downstream physical acceptance checks.
"""
from __future__ import annotations

import itertools
from typing import Any

import numpy as np

from .topology import route_topologies


def optimize_voxel_routes(router: Any, jobs: dict, *, margin: int = 2,
                          max_nodes: int = 6000) -> tuple[dict, dict]:
    """Jobs contain path, context, blocked mask and fixed ordered waypoint voxels.

    Authored multi-leg waypoints remain fixed in the existing router. Only
    unconstrained branches are optimized; the returned metadata lists exclusions.
    Pin fan-outs are assembled after this call, so endpoints stay untouched.
    """
    eligible = {key: job for key, job in jobs.items() if len(job['stops']) == 2}
    if not isinstance(margin, int) or not 0 <= margin <= 10 or max_nodes < 1:
        raise ValueError('topology margin must be 0..10 and max_nodes must be positive')
    metadata = {'source': 'cad_voxel_corridor', 'margin_voxels': margin,
                'excluded_waypoint_branches': [list(k) for k in jobs if k not in eligible]}
    if len(eligible) < 2:
        return jobs, {**metadata, 'status': 'not_needed'}
    grid = router.grid
    sx, sy, _ = grid.strides
    nodes = set()
    offsets = list(itertools.product(range(-margin, margin + 1), repeat=3))
    for job in eligible.values():
        for flat in job['path']:
            i, rest = divmod(flat, sx)
            j, k = divmod(rest, sy)
            for di, dj, dk in offsets:
                q = (i + di, j + dj, k + dk)
                if all(0 < q[a] < grid.shape[a] - 1 for a in range(3)):
                    nodes.add(q[0] * sx + q[1] * sy + q[2])
        if len(nodes) > max_nodes:
            return jobs, {**metadata, 'status': 'budget_exceeded', 'vertices': len(nodes), 'limit': max_nodes}
    # All candidates use the same environmental cost field. Conservatively use
    # the largest branch radius, while preserving per-branch terminal exclusions.
    cost = np.maximum.reduce([np.asarray(job['context'].cost) for job in eligible.values()])
    common = set(n for n in nodes if any(not job['blocked'][n] for job in eligible.values()))
    names = {key: str(i) for i, key in enumerate(sorted(eligible))}
    allowed = {names[key]: {str(n) for n in common if not job['blocked'][n]}
               for key, job in eligible.items()}
    edges = []
    moves = list(itertools.product((-1, 0, 1), repeat=3))
    # Supercover rule: every corner of a diagonal cell must be unblocked in
    # every context. This prevents diagonal shortcuts through obstacle corners.
    for node in sorted(common):
        i, rest = divmod(node, sx)
        j, k = divmod(rest, sy)
        for di, dj, dk in moves:
            if (di, dj, dk) == (0, 0, 0):
                continue
            other = node + di * sx + dj * sy + dk
            if other <= node or other not in common:
                continue
            axis = next((a for a, d in enumerate((di, dj, dk)) if d), -1)
            pure = sum(abs(v) for v in (di, dj, dk)) == 1
            if any((job['context'].zone[node] or job['context'].zone[other]) and
                   (not pure or any(z and z - 1 != axis for z in
                                    (job['context'].zone[node], job['context'].zone[other])))
                   for job in eligible.values()):
                continue
            if sum(abs(v) for v in (di, dj, dk)) > 1:
                corners = [node + x * sx + y * sy + z
                           for x in ({0, di}) for y in ({0, dj}) for z in ({0, dk})]
                if any(job['blocked'][p] for job in eligible.values() for p in corners):
                    continue
            plate_cost = max((job['context'].zone_cost[node] + job['context'].zone_cost[other]) / 2
                             for job in eligible.values())
            edges.append({'from': str(node), 'to': str(other),
                          'cost': grid.voxel * (di * di + dj * dj + dk * dk) ** 0.5 *
                          float(cost[node] + cost[other]) / 2 + plate_cost})
    cables = [{'id': names[key], 'from': str(job['path'][0]), 'to': str(job['path'][-1]),
               'count': job.get('count', 1)}
              for key, job in eligible.items()]
    try:
        result = route_topologies(edges, cables, bundle_weights=[0.25, 0.65], max_sweeps=5,
                                  alternative_paths=3, dual_iterations=5, allowed_nodes=allowed)
    except ValueError as error:
        return jobs, {**metadata, 'status': 'no_candidate', 'reason': str(error)}
    if not result['candidates']:
        return jobs, {**metadata, 'status': 'no_candidate'}
    # Fixed policy chooses a balanced candidate; the model does not pick points.
    chosen = min(result['candidates'], key=lambda c: (.5 * c['cable_cost'] + .5 * c['bundle_cost'],
                                                    c['cable_cost'], sorted(c['paths'].items())))
    updated = dict(jobs)
    for key, job in eligible.items():
        path = [int(n) for n in chosen['paths'][names[key]]]
        if any(job['blocked'][n] for n in path):
            raise RuntimeError('topology solver returned a blocked voxel')
        updated[key] = {**job, 'path': path}
    return updated, {**metadata, 'status': 'optimized', 'vertices': len(common), 'edges': len(edges),
                     **result, 'selected_cost': .5 * chosen['cable_cost'] + .5 * chosen['bundle_cost']}
