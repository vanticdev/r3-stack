"""A branched loom can take different physical corridors without changing its connectivity."""
import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from r3_model.project import Project
from r3_model.spec import Harness
from r3_wiring.geometry import project_to_polyline
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import HarnessRouter, RouterOptions
from test_wiring_support import write_project
from native_wiring_fixture import publish_wiring_pull


def test_each_branch_uses_its_own_corridor_in_wire_direction(tmp_path):
    root = write_project(tmp_path, block=False)
    file = root / 'robot.yaml'
    spec = yaml.safe_load(file.read_text())
    spec['components']['dst2'] = {'type': 'test-box-rev', 'mount': 'plate',
                                  'pose': {'xyz': ['200 mm', '-50 mm', '3 mm']}}
    harness = spec['electrical']['harnesses'][0]
    harness['connectors']['C'] = {'type': 'servo-3-female', 'mates': 'dst2.J1'}
    harness['wires'] = [{'id': 'north', 'from': 'A.1', 'to': 'B.1', 'awg': 22},
                        {'id': 'south', 'from': 'C.2', 'to': 'A.2', 'awg': 22}]
    harness['route']['wire_via'] = {'north': [[.1, .06, .03]],
                                   'south': [[.15, -.06, .03], [.1, -.06, .03]]}
    file.write_text(yaml.safe_dump(spec, sort_keys=False))
    publish_wiring_pull(root)
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    routing = HarnessRouter(project, obstacles, RouterOptions(plan=False)).route()
    for wire_id, points in harness['route']['wire_via'].items():
        wire = routing.wires[wire_id]
        assert not wire.failed
        arcs = []
        for point in points:
            distance, arc = project_to_polyline(wire.path, np.asarray(point)[None])
            assert distance < .006
            arcs.append(arc)
        assert arcs == sorted(arcs)  # south is declared from C toward A, opposite branch storage


def test_branch_waypoint_typo_and_conflicting_shared_pair_are_rejected():
    spec = {'id': 'pair', 'wires': [{'id': 'a', 'from': 'A.1', 'to': 'B.1', 'awg': 22},
                                   {'id': 'b', 'from': 'B.2', 'to': 'A.2', 'awg': 22}]}
    with pytest.raises(ValidationError, match='unknown wire'):
        Harness.model_validate({**spec, 'route': {'wire_via': {'typo': [[0, 0, 0]]}}})
    with pytest.raises(ValidationError, match='conflicting waypoints'):
        Harness.model_validate({**spec, 'route': {'wire_via': {'a': [[0, 0, 0]], 'b': [[1, 0, 0]]}}})
    route = {'wire_via': {'a': [[0, 0, 0], [1, 0, 0]], 'b': [[1, 0, 0], [0, 0, 0]]}}
    assert Harness.model_validate({**spec, 'route': route}).route.wire_via == route['wire_via']


def test_waypoint_behind_a_connector_does_not_retrace_the_arrival_path(tmp_path):
    root = write_project(tmp_path, block=False)
    file = root / 'robot.yaml'
    spec = yaml.safe_load(file.read_text())
    # Go behind the destination body, then return to its west-facing connector.
    # The old second leg walked back over its first leg, creating a foldback
    # and duplicate graph edges instead of a loop around the body.
    spec['electrical']['harnesses'][0]['route']['via'] = [[.24, 0., .013]]
    file.write_text(yaml.safe_dump(spec, sort_keys=False))
    publish_wiring_pull(root)
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    visited = []
    astar = router._astar

    def record(*args, **kwargs):
        result = astar(*args, **kwargs)
        if result is not None:
            visited.append(result)
        return result

    router._astar = record
    routing = router.route()
    assert all(not wire.failed for wire in routing.wires.values())
    assert len(visited) == 2
    assert set(visited[0][:-1]).isdisjoint(visited[1])
    for wire in routing.wires.values():
        assert np.max(np.linalg.norm(wire.path, axis=1)) < .3
        assert project_to_polyline(wire.path, np.array([[.24, 0., .013]]))[0] < .006
