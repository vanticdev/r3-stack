"""Deterministic graph harness topology search (Karlsson et al., algorithms 2–3).

Costs are environmental edge costs, not necessarily physical lengths. Obstacles
must already have been removed by the caller. This is topology only: it does
not certify bundle capacity, curvature, installation, or dynamic clearance.
"""
from __future__ import annotations

import heapq
import math
from collections import Counter
from typing import Any


def route_topologies(edges: list[dict[str, Any]], cables: list[dict[str, str]],
                     bundle_weights: list[float] | None = None, max_sweeps: int = 30) -> dict[str, Any]:
    """Return distinct nondominated candidates under cable/union-edge objectives.

    Remove one cable, price edges occupied by the others at w_L*c and new
    edges at (w_L+w_B)*c, then accept only strict global improvement. Stable
    sorted traversal and two fixed initial orders avoid random/LLM decisions.
    No dual bound or global-optimality claim is made.
    """
    weights = [0.0, 0.25, 0.5, 0.75, 0.95] if bundle_weights is None else bundle_weights
    if not weights or any(not math.isfinite(w) or not 0 <= w <= 1 for w in weights):
        raise ValueError("bundle_weights must contain finite values between 0 and 1")
    if not 1 <= max_sweeps <= 100:
        raise ValueError("max_sweeps must be between 1 and 100")
    costs: dict[tuple[str, str], float] = {}
    graph: dict[str, list[str]] = {}
    for edge in edges:
        a, b, cost = str(edge['from']), str(edge['to']), float(edge['cost'])
        if not a or not b or a == b or not math.isfinite(cost) or cost <= 0:
            raise ValueError("edges require distinct nonempty endpoints and positive finite cost")
        key = tuple(sorted((a, b)))
        if key in costs:
            raise ValueError(f"duplicate undirected edge {key}")
        costs[key] = cost
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)
    demands = {c['id']: (c['from'], c['to']) for c in cables}
    if len(demands) != len(cables) or any(not cid for cid in demands):
        raise ValueError("cable IDs must be nonempty and unique")
    for cid, (a, b) in demands.items():
        if a not in graph or b not in graph:
            raise ValueError(f"unknown terminal for cable {cid}")

    def path_edges(path: list[str]) -> list[tuple[str, str]]:
        return [tuple(sorted((a, b))) for a, b in zip(path, path[1:])]

    def shortest(a: str, b: str, used: Counter, weight: float,
                 excluded: tuple[str, str] | None = None) -> list[str]:
        queue = [(0.0, a)]
        distances, parent = {a: 0.0}, {}
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if node == b:
                path = [b]
                while path[-1] != a:
                    path.append(parent[path[-1]])
                return path[::-1]
            for other in sorted(graph[node]):
                edge = tuple(sorted((node, other)))
                if edge == excluded:
                    continue
                step = costs[edge] * (1 - weight if used[edge] else 1)
                candidate = distance + step
                if candidate < distances.get(other, math.inf):
                    distances[other], parent[other] = candidate, node
                    heapq.heappush(queue, (candidate, other))
        raise ValueError(f"no route between {a} and {b}")

    def metrics(paths: dict[str, list[str]]) -> tuple[float, float, Counter]:
        occupied = Counter(e for path in paths.values() for e in path_edges(path))
        return sum(costs[e] * n for e, n in occupied.items()), sum(costs[e] for e in occupied), occupied

    # Bounded edge-exclusion seeds escape independent-shortest-path local minima.
    # This is our initialization heuristic, not the paper's alpha-SP routine.
    seeds = [None]
    if demands:
        first = sorted(demands)[0]
        seeds += path_edges(shortest(*demands[first], Counter(), 0))[:16]
    candidates, signatures = [], set()
    for weight in sorted(set(weights)):
        for order, excluded in ((o, seed) for o in (sorted(demands), sorted(demands, reverse=True)) for seed in seeds):
            paths, used = {}, Counter()
            for cid in order:
                try:
                    paths[cid] = shortest(*demands[cid], used, weight, excluded if cid == order[0] else None)
                except ValueError:
                    paths[cid] = shortest(*demands[cid], used, weight)
                used.update(path_edges(paths[cid]))
            length, union, used = metrics(paths)
            history = [(1 - weight) * length + weight * union]
            converged = False
            for _ in range(max_sweeps):
                changed = False
                for cid in sorted(demands):
                    fixed = used.copy()
                    fixed.subtract(path_edges(paths[cid]))
                    proposed = {**paths, cid: shortest(*demands[cid], fixed, weight)}
                    new_length, new_union, new_used = metrics(proposed)
                    objective = (1 - weight) * new_length + weight * new_union
                    if objective < history[-1] - 1e-12:
                        paths, length, union, used = proposed, new_length, new_union, new_used
                        history.append(objective)
                        changed = True
                if not changed:
                    converged = True
                    break
            signature = tuple((cid, tuple(paths[cid])) for cid in sorted(paths))
            if signature in signatures:
                continue
            signatures.add(signature)
            candidates.append({'paths': paths, 'bundle_weight': weight,
                               'cable_cost': length, 'bundle_cost': union,
                               'objective_history': history, 'converged': converged,
                               'shared_edges': [{'from': e[0], 'to': e[1], 'cables': n}
                                                for e, n in sorted(used.items()) if n > 1]})
    front = [a for a in candidates if not any(
        b['cable_cost'] <= a['cable_cost'] and b['bundle_cost'] <= a['bundle_cost']
        and (b['cable_cost'] < a['cable_cost'] or b['bundle_cost'] < a['bundle_cost'])
        for b in candidates)]
    return {'algorithm': 'deterministic_cable_rerouting', 'candidates': front,
            'generated_candidates': len(candidates), 'optimality_certified': False,
            'physical_validation_required': True}
