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
