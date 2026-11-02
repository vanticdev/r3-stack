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


def route_topologies(edges: list[dict[str, Any]], cables: list[dict[str, Any]],
                     bundle_weights: list[float] | None = None, max_sweeps: int = 30,
                     *, alpha: float = 1.2, alternative_paths: int = 7,
                     dual_iterations: int = 40,
                     allowed_nodes: dict[str, set[str]] | None = None,
                     initial_paths: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Return distinct nondominated candidates under cable/union-edge objectives.

    Remove one cable, price edges occupied by the others at w_L*c and new
    edges at (w_L+w_B)*c, then accept only strict global improvement. Stable
    sorted traversal and two fixed initial orders avoid random/LLM decisions.
    Projected Lagrangian search supplies graph-scoped lower bounds. Bounds do
    not certify the physical harness, nor an environment outside this graph.
    """
    weights = [0.0, 0.25, 0.5, 0.75, 0.95] if bundle_weights is None else bundle_weights
    if not weights or any(not math.isfinite(w) or not 0 <= w <= 1 for w in weights):
        raise ValueError("bundle_weights must contain finite values between 0 and 1")
    if not 1 <= max_sweeps <= 100:
        raise ValueError("max_sweeps must be between 1 and 100")
    if not math.isfinite(alpha) or alpha < 1 or not 1 <= alternative_paths <= 20 or not 0 <= dual_iterations <= 500:
        raise ValueError("require alpha >= 1, 1..20 alternative_paths, and 0..500 dual_iterations")
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
    multiplicity = {c['id']: c.get('count', 1) for c in cables}
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in multiplicity.values()):
        raise ValueError('cable count must be a positive integer')
    if len(demands) != len(cables) or any(not cid for cid in demands):
        raise ValueError("cable IDs must be nonempty and unique")
    for cid, (a, b) in demands.items():
        if a not in graph or b not in graph:
            raise ValueError(f"unknown terminal for cable {cid}")
        if allowed_nodes is not None and (cid not in allowed_nodes or a not in allowed_nodes[cid] or b not in allowed_nodes[cid]):
            raise ValueError(f"terminal outside permitted graph for cable {cid}")

    def path_edges(path: list[str]) -> list[tuple[str, str]]:
        return [tuple(sorted((a, b))) for a, b in zip(path, path[1:])]

    def shortest(a: str, b: str, used: Counter, weight: float,
                 excluded: tuple[str, str] | None = None, *, cid: str | None = None,
                 banned_edges: set | None = None, banned_nodes: set | None = None,
                 prices: dict | None = None) -> list[str]:
        queue = [(0.0, a)]
        distances, parent = {a: 0.0}, {}
        settled = set()
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if node in settled:
                continue
            settled.add(node)
            if node == b:
                path = [b]
                while path[-1] != a:
                    path.append(parent[path[-1]])
                return path[::-1]
            for other in sorted(graph[node]):
                edge = tuple(sorted((node, other)))
                if (edge == excluded or other in settled or (banned_edges and edge in banned_edges)
                        or (banned_nodes and other in banned_nodes)
                        or (allowed_nodes is not None and cid is not None
                            and other not in allowed_nodes[cid])):
                    continue
                step = prices[edge] if prices is not None else costs[edge] * (
                    (1 - weight) * multiplicity.get(cid, 1) + (0 if used[edge] else weight))
                candidate = distance + step
                if candidate < distances.get(other, math.inf):
                    distances[other], parent[other] = candidate, node
                    heapq.heappush(queue, (candidate, other))
        raise ValueError(f"no route between {a} and {b}")

    def metrics(paths: dict[str, list[str]]) -> tuple[float, float, Counter]:
        occupied = Counter(e for path in paths.values() for e in path_edges(path))
        cable_cost = sum(multiplicity[cid] * sum(costs[e] for e in path_edges(path)) for cid, path in paths.items())
        return cable_cost, sum(costs[e] for e in occupied), occupied

    def move_branches(paths: dict[str, list[str]], weight: float) -> dict[str, list[str]]:
        """Jointly move both ends of a bundle and its incident branching arms.

        Sum shortest-distance fields from fixed arm ends, then multi-source
        Dijkstra with the first sum as source cost and the second as target
        cost. This implements the fixed-topology branch search in section 3.1.
        Reconstructed paths must be simple and improve the actual union cost;
        overlapping arms therefore cannot produce a falsely improved result.
        """
        terminals = {n for pair in demands.values() for n in pair}
        adjacency: dict[str, set[str]] = {}
        for path in paths.values():
            for a, b in zip(path, path[1:]):
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)
        junctions = terminals | {n for n, neighbors in adjacency.items() if len(neighbors) != 2}
        segments: dict[tuple[str, ...], set[str]] = {}
        cable_segments = {}
        for cid, path in paths.items():
            parts, start = [], 0
            for i in range(1, len(path)):
                if path[i] in junctions:
                    part = tuple(path[start:i + 1])
                    key = min(part, part[::-1])
                    segments.setdefault(key, set()).add(cid)
                    parts.append((key, part == key))
                    start = i
            cable_segments[cid] = parts
        for trunk in sorted(segments):
            a, b = trunk[0], trunk[-1]
            if a in terminals or b in terminals or a == b:
                continue
            arms_a = [s for s in segments if s != trunk and a in (s[0], s[-1])]
            arms_b = [s for s in segments if s != trunk and b in (s[0], s[-1])]
            if not arms_a or not arms_b or set(arms_a) & set(arms_b):
                continue
            moving = set(arms_a + arms_b + [trunk])
            fixed_used = Counter(e for s, users in segments.items() if s not in moving for e in path_edges(list(s)))

            def field(s: tuple, anchor: str) -> tuple[dict, dict]:
                users = segments[s]
                permitted = set(graph) if allowed_nodes is None else set.intersection(*(allowed_nodes[c] for c in users))
                distance, parent = {anchor: 0.0}, {}
                heap, settled = [(0.0, anchor)], set()
                while heap:
                    value, node = heapq.heappop(heap)
                    if node in settled:
                        continue
                    settled.add(node)
                    for other in sorted(graph[node]):
                        if other not in permitted or other in settled:
                            continue
                        edge = tuple(sorted((node, other)))
                        cost = costs[edge] * ((1 - weight) * sum(multiplicity[c] for c in users) + (0 if fixed_used[edge] else weight))
                        trial = value + cost
                        if trial < distance.get(other, math.inf):
                            distance[other], parent[other] = trial, node
                            heapq.heappush(heap, (trial, other))
                return distance, parent

            fields = {}
            for s in arms_a + arms_b:
                endpoint = a if s in arms_a else b
                anchor = s[-1] if s[0] == endpoint else s[0]
                fields[s] = (anchor, *field(s, anchor))
            sources = {n: sum(fields[s][1].get(n, math.inf) for s in arms_a) for n in graph}
            targets = {n: sum(fields[s][1].get(n, math.inf) for s in arms_b) for n in graph}
            users = segments[trunk]
            permitted = set(graph) if allowed_nodes is None else set.intersection(*(allowed_nodes[c] for c in users))
            distance = {n: cost for n, cost in sources.items() if math.isfinite(cost) and n in permitted}
            heap = [(v, n) for n, v in distance.items()]
            heapq.heapify(heap)
            parent, settled = {}, set()
            while heap:
                value, node = heapq.heappop(heap)
                if node in settled:
                    continue
                settled.add(node)
                for other in sorted(graph[node]):
                    if other not in permitted or other in settled:
                        continue
                    edge = tuple(sorted((node, other)))
                    cost = costs[edge] * ((1 - weight) * sum(multiplicity[c] for c in users) + (0 if fixed_used[edge] else weight))
                    trial = value + cost
                    if trial < distance.get(other, math.inf):
                        distance[other], parent[other] = trial, node
                        heapq.heappush(heap, (trial, other))
            viable = [n for n in distance if math.isfinite(targets[n])]
            if not viable:
                continue
            end = min(viable, key=lambda n: (distance[n] + targets[n], n))
            core = [end]
            while core[-1] in parent:
                core.append(parent[core[-1]])
            core.reverse()
            new_a, new_b = core[0], core[-1]
            replacement = {trunk: core}
            for s in arms_a + arms_b:
                old = a if s in arms_a else b
                new = new_a if s in arms_a else new_b
                anchor, _, parents = fields[s]
                path = [new]
                while path[-1] != anchor:
                    path.append(parents[path[-1]])
                replacement[s] = path if s[0] == old else path[::-1]
            proposed = {}
            for cid, parts in cable_segments.items():
                path = []
                for s, forward in parts:
                    points = replacement.get(s, list(s))
                    points = points if forward else points[::-1]
                    path.extend(points if not path else points[1:])
                proposed[cid] = path
            if any(len(p) != len(set(p)) for p in proposed.values()):
                continue
            old_l, old_b, _ = metrics(paths)
            new_l, new_b, _ = metrics(proposed)
            if (1 - weight) * new_l + weight * new_b < (1 - weight) * old_l + weight * old_b - 1e-12:
                return proposed
        return paths

    # Yen's loopless k-shortest paths, cut off at alpha times shortest cost.
    # The paper cites a spatially-distinct alpha-path generator; Yen is our
    # deterministic graph-only substitute (no geometric diversity claim).
    alternatives = {}
    for cid, (a, b) in sorted(demands.items()):
        accepted = [shortest(a, b, Counter(), 0, cid=cid)]
        limit = alpha * sum(costs[e] for e in path_edges(accepted[0]))
        pending, seen = [], {tuple(accepted[0])}
        while len(accepted) < alternative_paths:
            previous = accepted[-1]
            for i in range(len(previous) - 1):
                root = previous[:i + 1]
                banned = {tuple(sorted((p[i], p[i + 1]))) for p in accepted
                          if len(p) > i + 1 and p[:i + 1] == root}
                try:
                    tail = shortest(root[-1], b, Counter(), 0, cid=cid,
                                    banned_edges=banned, banned_nodes=set(root[:-1]))
                except ValueError:
                    continue
                path = tuple(root[:-1] + tail)
                cost = sum(costs[e] for e in path_edges(list(path)))
                if cost <= limit + 1e-12 and path not in seen:
                    seen.add(path)
                    heapq.heappush(pending, (cost, path))
            if not pending:
                break
            accepted.append(list(heapq.heappop(pending)[1]))
        alternatives[cid] = accepted
    candidates, signatures = [], set()
    searches = []
    branch_moves = 0
    bounds = []
    for weight in sorted(set(weights)):
        starts = []
        for order in (sorted(demands), sorted(demands, reverse=True)):
            paths, used = {}, Counter()
            for paths_for_cable in alternatives.values():
                for path in paths_for_cable:
                    used.update(path_edges(path))
            for cid in order:
                for path in alternatives[cid]:
                    used.subtract(path_edges(path))
                paths[cid] = shortest(*demands[cid], used, weight, cid=cid)
                used.update(path_edges(paths[cid]))
            starts.append(paths)
        if initial_paths is not None:
            if set(initial_paths) != set(demands):
                raise ValueError("initial_paths must cover every cable")
            for cid, path in initial_paths.items():
                if (not path or (path[0], path[-1]) != demands[cid] or len(set(path)) != len(path)
                        or any(e not in costs for e in path_edges(path))
                        or (allowed_nodes is not None and any(n not in allowed_nodes[cid] for n in path))):
                    raise ValueError(f"invalid initial path for {cid}")
            starts.append(initial_paths)
        # Projected Lagrangian subgradient search, equations 6–8 / algorithm 4.
        import numpy as np

        ids, edge_ids = sorted(demands), sorted(costs)
        edge_index = {e: i for i, e in enumerate(edge_ids)}
        edge_cost = np.array([costs[e] for e in edge_ids])
        budget = weight * edge_cost
        lambdas = np.tile(budget / max(1, len(ids)), (len(ids), 1))
        best_lower, delta, stagnant = 0.0, 1.5, 0
        upper = min(((1 - weight) * metrics(p)[0] + weight * metrics(p)[1] for p in starts), default=0.0)
        for iteration in range(dual_iterations if ids else 0):
            incidence = np.zeros_like(lambdas)
            dual_paths, lower = {}, 0.0
            for k, cid in enumerate(ids):
                prices = {e: (1 - weight) * multiplicity[cid] * costs[e] + lambdas[k, j] for j, e in enumerate(edge_ids)}
                path = shortest(*demands[cid], Counter(), 0, cid=cid, prices=prices)
                dual_paths[cid] = path
                for edge in path_edges(path):
                    incidence[k, edge_index[edge]] = 1
                    lower += prices[edge]
            if lower > best_lower + 1e-12:
                best_lower, stagnant = lower, 0
            else:
                stagnant += 1
            if iteration % 10 == 0 or iteration == dual_iterations - 1:
                starts.append(dual_paths)
                l, u, _ = metrics(dual_paths)
                upper = min(upper, (1 - weight) * l + weight * u)
            # Orthogonal projection of each edge's multipliers onto its simplex.
            norm = float(np.sum(incidence * incidence))
            if norm == 0 or upper - best_lower <= 1e-10:
                break
            trial = lambdas + delta * max(0.0, upper - lower) / norm * incidence
            ordered = np.sort(trial, axis=0)[::-1]
            prefix = np.cumsum(ordered, axis=0) - budget
            ranks = np.arange(1, len(ids) + 1)[:, None]
            active = ordered - prefix / ranks > 0
            rho = np.maximum(1, active.sum(axis=0))
            theta = prefix[rho - 1, np.arange(len(edge_ids))] / rho
            lambdas = np.maximum(trial - theta, 0)
            lambdas[:, budget == 0] = 0
            if stagnant and stagnant % 10 == 0:
                delta *= 0.8
        bounds.append({'bundle_weight': weight, 'lower_bound': best_lower})
        for paths in starts:
            paths = {cid: list(path) for cid, path in paths.items()}
            length, union, used = metrics(paths)
            history = [(1 - weight) * length + weight * union]
            converged = False
            for _ in range(max_sweeps):
                changed = False
                for cid in sorted(demands):
                    fixed = used.copy()
                    fixed.subtract(path_edges(paths[cid]))
                    proposed = {**paths, cid: shortest(*demands[cid], fixed, weight, cid=cid)}
                    new_length, new_union, new_used = metrics(proposed)
                    objective = (1 - weight) * new_length + weight * new_union
                    if objective < history[-1] - 1e-12:
                        paths, length, union, used = proposed, new_length, new_union, new_used
                        history.append(objective)
                        changed = True
                proposed = move_branches(paths, weight)
                if proposed is not paths:
                    branch_moves += 1
                    paths = proposed
                    length, union, used = metrics(paths)
                    history.append((1 - weight) * length + weight * union)
                    changed = True
                if not changed:
                    converged = True
                    break
            searches.append({'bundle_weight': weight, 'objective_history': history, 'converged': converged})
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
    for bound in bounds:
        w = bound['bundle_weight']
        bound['upper_bound'] = min(((1 - w) * c['cable_cost'] + w * c['bundle_cost'] for c in front), default=0.0)
        bound['relative_gap'] = max(0.0, bound['upper_bound'] - bound['lower_bound']) / max(1e-12, bound['upper_bound'])
    return {'algorithm': 'deterministic_cable_rerouting', 'candidates': front,
            'generated_candidates': len(candidates), 'optimality_certified': False,
            'dual_bounds': bounds, 'alpha': alpha, 'alternative_paths': alternative_paths,
            'branch_moves': branch_moves, 'searches': searches,
            'bound_scope': 'supplied_graph_only',
            'physical_validation_required': True}
