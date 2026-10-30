"""Finite-width branches use independent lanes on an unrelated sensor fixture."""
import numpy as np
import yaml

from r3_model.project import Project
from r3_wiring.checks import HarnessChecker
from r3_wiring.obstacles import build_obstacles
from r3_wiring.router import HarnessRouter, RouterOptions
from test_wiring_support import write_project


def test_independent_branches_have_no_artificial_shared_core(tmp_path):
    root = write_project(tmp_path, block=False)
    path = root / 'robot.yaml'
    spec = yaml.safe_load(path.read_text())
    spec['components']['dst']['pose']['xyz'][1] = '45 mm'
    spec['components']['other'] = {'type': 'test-box-rev', 'mount': 'plate',
                                    'pose': {'xyz': ['200 mm', '-45 mm', '3 mm']}}
    harness = spec['electrical']['harnesses'][0]
    harness['connectors']['C'] = {'type': 'servo-3-female', 'mates': 'other.J1'}
    harness['wires'] = [{'id': 'north', 'from': 'A.1', 'to': 'B.1', 'awg': 22},
                        {'id': 'south', 'from': 'A.3', 'to': 'C.3', 'awg': 22}]
    harness['route'] = {'independent_branches': True, 'slack': '10 mm',
                        'wire_via': {'north': [[.1, .035, .015]], 'south': [[.1, -.035, .015]]}}
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=True))
    routing = router.route()
    assert len(routing.wires) == 2
    assert all(not wire.failed for wire in routing.wires.values())
    assert all(len(segment['wires']) == 1 for segment in routing.harnesses['lead'].segments)
    for wire in routing.wires.values():
        for endpoint, point in zip(wire.ends, [wire.path[0], wire.path[-1]]):
            np.testing.assert_allclose(endpoint.position, point, atol=1e-12)
    checker = HarnessChecker(project, obstacles, routing)
    checker._wire_intersections()
    assert not checker.diagnostics
    assert routing.planning.plans[0].stats['independent_lanes'] == 2


def test_integral_pin_positions_do_not_change_when_a_branch_is_selected(tmp_path):
    from r3_connectors.frames import Frame
    from r3_wiring.router import NodePoint
    root = write_project(tmp_path, block=False)
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    node = NodePoint('lead', 'A', 'connector', np.zeros(3), np.array([0., 0., 1.]),
                     connector_type='integral', frame=Frame.identity())
    widths = {'1': .0016, '2': .0016, '3': .0016}
    for pin in widths:
        complete = router._pin_end(node, f'A.{pin}', widths)
        isolated = router._pin_end(node, f'A.{pin}', {pin: widths[pin]})
        np.testing.assert_array_equal(complete.position, isolated.position)
    assert np.linalg.norm(router._pin_end(node, 'A.1', {}).position -
                          router._pin_end(node, 'A.3', {}).position) == .0032


def test_single_pin_lane_reserves_only_its_own_fanout(tmp_path):
    from r3_connectors.frames import Frame
    from r3_wiring.router import NodePoint
    root = write_project(tmp_path, block=False)
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    node = NodePoint('lead', 'A', 'connector', np.zeros(3), np.array([0., 0., 1.]),
                     connector_type='integral', frame=Frame.identity(), od=.0016)
    node.exit = router._pin_end(node, 'A.1', {}).position.copy()
    router._lead(node, [22], pin_widths={'1': .0016})
    assert node.fan_length == .006
    router._lead(node, [22])
    assert node.fan_length > .006


def test_stiff_conductors_fit_between_nearby_facing_exits(tmp_path):
    root = write_project(tmp_path, block=False)
    path = root / 'robot.yaml'
    spec = yaml.safe_load(path.read_text())
    spec['components']['dst']['pose']['xyz'] = ['75 mm', '12 mm', '3 mm']
    harness = spec['electrical']['harnesses'][0]
    for connector in harness['connectors'].values():
        connector['type'] = 'integral'
    harness['wires'] = [{'id': 'positive', 'from': 'A.1', 'to': 'B.1', 'awg': 12},
                        {'id': 'negative', 'from': 'A.2', 'to': 'B.2', 'awg': 12}]
    harness['route'] = {'independent_branches': True}
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=True))
    routing = router.route()
    checker = HarnessChecker(project, obstacles, routing)
    checker._wire_intersections()
    checker._bend_radius()
    assert not [d for d in checker.diagnostics if d.severity.value == 'error']
    assert all(plan.feasible for plan in routing.planning.plans)
