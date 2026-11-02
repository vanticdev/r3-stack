from types import SimpleNamespace

import numpy as np

from r3_wiring.grid import VoxelGrid
from r3_wiring.topology_geometry import optimize_voxel_routes


def fixture_jobs():
    grid = VoxelGrid(np.zeros(3), (7, 7, 4), .01)
    mask = bytearray(grid.border().astype(np.uint8).tobytes())
    # A solid vertical obstacle; the solver cannot use its interior voxels.
    for y in (2, 3, 4):
        for z in (1, 2):
            mask[grid.flat(np.array([.03, y * .01, z * .01]))] = 1
    def path(points):
        return [grid.flat(np.array(p) * .01) for p in points]
    north = path([(1, 2, 1), (2, 1, 1), (3, 1, 1), (4, 1, 1), (5, 2, 1)])
    south = path([(1, 4, 1), (2, 5, 1), (3, 5, 1), (4, 5, 1), (5, 4, 1)])
    context = SimpleNamespace(cost=np.ones(grid.size), zone=np.zeros(grid.size), zone_cost=np.zeros(grid.size))
    jobs = {('A', 'B'): {'path': north, 'blocked': mask, 'context': context, 'stops': [north[0], north[-1]]},
            ('C', 'D'): {'path': south, 'blocked': mask, 'context': context, 'stops': [south[0], south[-1]]}}
    return SimpleNamespace(grid=grid), jobs


def test_geometry_generates_topology_and_keeps_terminal_and_obstacle_constraints():
    router, jobs = fixture_jobs()
    routed, stats = optimize_voxel_routes(router, jobs)
    assert stats['status'] == 'optimized'
    assert stats['candidates']
    for key, job in routed.items():
        assert (job['path'][0], job['path'][-1]) == tuple(jobs[key]['stops'])
        assert not any(job['blocked'][node] for node in job['path'])


def test_budget_and_authored_waypoints_are_explicit():
    router, jobs = fixture_jobs()
    result, stats = optimize_voxel_routes(router, jobs, max_nodes=1)
    assert result is jobs
    assert stats['status'] == 'budget_exceeded'
    for job in jobs.values():
        job['stops'].insert(1, job['path'][2])
    result, stats = optimize_voxel_routes(router, jobs)
    assert result is jobs
    assert stats['status'] == 'not_needed'
    assert len(stats['excluded_waypoint_branches']) == 2


def test_robot_router_feeds_generated_topology_into_bundle_geometry(tmp_path):
    import yaml
    from r3_model.project import Project
    from r3_wiring.obstacles import build_obstacles
    from r3_wiring.router import HarnessRouter, RouterOptions
    from test_wiring_support import write_project

    root = write_project(tmp_path, block=False)
    file = root / 'robot.yaml'
    spec = yaml.safe_load(file.read_text())
    spec['components']['dst2'] = {'type': 'test-box-rev', 'mount': 'plate',
                                  'pose': {'xyz': ['200 mm', '-50 mm', '3 mm']}}
    harness = spec['electrical']['harnesses'][0]
    harness['connectors']['C'] = {'type': 'servo-3-female', 'mates': 'dst2.J1'}
    harness['wires'] = [{'id': 'north', 'from': 'A.1', 'to': 'B.1', 'awg': 22},
                        {'id': 'south', 'from': 'A.2', 'to': 'C.2', 'awg': 22}]
    file.write_text(yaml.safe_dump(spec, sort_keys=False))
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(voxel=.006, plan=False, relax_iterations=0))
    result = router.route()
    assert result.stats['topology']['lead']['status'] == 'optimized'
    assert set(result.wires) == {'north', 'south'}
    assert all(not wire.failed for wire in result.wires.values())
    assert all(np.isfinite(wire.path).all() for wire in result.wires.values())
