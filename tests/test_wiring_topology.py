import pytest

from r3_wiring.topology import route_topologies


def test_bundle_tradeoff_and_repeatability():
    # Two separate cheap routes vs a longer common trunk.
    edges = [dict(zip(('from', 'to', 'cost'), row)) for row in [
        ('a', 'b', 2.8), ('c', 'd', 2.8), ('a', 'x', 1), ('c', 'x', 1),
        ('x', 'y', 1), ('y', 'b', 1), ('y', 'd', 1)]]
    cables = [{'id': 'one', 'from': 'a', 'to': 'b'}, {'id': 'two', 'from': 'c', 'to': 'd'}]
    result = route_topologies(edges, cables)
    assert result == route_topologies(list(reversed(edges)), list(reversed(cables)))
    pairs = {(c['cable_cost'], c['bundle_cost']) for c in result['candidates']}
    assert (5.6, 5.6) in pairs
    assert (6, 5) in pairs
    assert any(c['shared_edges'] for c in result['candidates'])
    for candidate in result['candidates']:
        assert all(a > b for a, b in zip(candidate['objective_history'], candidate['objective_history'][1:]))
        for cid, path in candidate['paths'].items():
            demand = next(c for c in cables if c['id'] == cid)
            assert (path[0], path[-1]) == (demand['from'], demand['to'])


def test_disconnected_and_invalid_graphs():
    with pytest.raises(ValueError, match='no route'):
        route_topologies([{'from': 'a', 'to': 'b', 'cost': 1}, {'from': 'c', 'to': 'd', 'cost': 1}],
                         [{'id': 'wire', 'from': 'a', 'to': 'd'}])
    with pytest.raises(ValueError, match='positive finite'):
        route_topologies([{'from': 'a', 'to': 'b', 'cost': float('nan')}], [])


def test_joint_branch_move_escapes_single_cable_local_minimum():
    rows = [('a', 'u', 1), ('c', 'u', 1), ('u', 'v', 10), ('v', 'b', 1), ('v', 'd', 1),
            ('a', 'x', 2), ('c', 'x', 2), ('x', 'y', 1), ('y', 'b', 2), ('y', 'd', 2)]
    edges = [dict(zip(('from', 'to', 'cost'), row)) for row in rows]
    cables = [{'id': 'one', 'from': 'a', 'to': 'b'}, {'id': 'two', 'from': 'c', 'to': 'd'}]
    result = route_topologies(edges, cables, [0.95], dual_iterations=0,
                              initial_paths={'one': ['a', 'u', 'v', 'b'], 'two': ['c', 'u', 'v', 'd']})
    assert any(c['cable_cost'] == 10 and c['bundle_cost'] == 9 for c in result['candidates'])
    assert result['branch_moves'] > 0
    assert any(len(c['objective_history']) > 1 for c in result['searches'])


def test_dual_bounds_against_exhaustive_optima():
    import itertools

    rows = [('a', 'b', 2.8), ('c', 'd', 2.8), ('a', 'x', 1), ('c', 'x', 1),
            ('x', 'y', 1), ('y', 'b', 1), ('y', 'd', 1)]
    edges = [dict(zip(('from', 'to', 'cost'), row)) for row in rows]
    costs = {tuple(sorted((a, b))): cost for a, b, cost in rows}
    graph = {}
    for a, b, _ in rows:
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)

    def paths(start, end, prefix=()):
        if start == end:
            yield prefix + (start,)
        else:
            for nxt in graph[start]:
                if nxt not in prefix:
                    yield from paths(nxt, end, prefix + (start,))

    combinations = list(itertools.product(paths('a', 'b'), paths('c', 'd')))
    def objective(pair, weight):
        runs = [tuple(sorted(e)) for p in pair for e in zip(p, p[1:])]
        return (1 - weight) * sum(costs[e] for e in runs) + weight * sum(costs[e] for e in set(runs))

    cables = [{'id': 'one', 'from': 'a', 'to': 'b'}, {'id': 'two', 'from': 'c', 'to': 'd'}]
    result = route_topologies(edges, cables, [0, .3, .8, 1], dual_iterations=50)
    for bound in result['dual_bounds']:
        optimum = min(objective(pair, bound['bundle_weight']) for pair in combinations)
        assert bound['lower_bound'] <= optimum + 1e-8
    assert all(c['converged'] for c in result['candidates'])


def test_per_cable_geometry_constraints_are_preserved():
    rows = [('a', 'x', 1), ('x', 'b', 1), ('a', 'y', 1), ('y', 'b', 1)]
    result = route_topologies([dict(zip(('from', 'to', 'cost'), r)) for r in rows],
                             [{'id': 'one', 'from': 'a', 'to': 'b'}, {'id': 'two', 'from': 'a', 'to': 'b'}],
                             allowed_nodes={'one': {'a', 'x', 'b'}, 'two': {'a', 'y', 'b'}})
    for c in result['candidates']:
        assert c['paths'] == {'one': ['a', 'x', 'b'], 'two': ['a', 'y', 'b']}
