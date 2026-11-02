"""Search bounds include cavities and native destinations without bypassing solids."""
import numpy as np
import pytest
import yaml

from r3_model.project import Project
from r3_wiring.geometry import Box
from r3_wiring.grid import VoxelGrid, compute_fields
from r3_wiring.obstacles import Obstacle, ObstacleSet, build_obstacles
from r3_wiring.router import HarnessRouter, RouterOptions
from native_wiring_fixture import publish_wiring_pull
from test_wiring_support import write_project


def test_bounds_cover_all_geometry_not_only_the_structure_footprint():
    def obstacle(name, center, half, structure):
        shape = Box(np.asarray(center), np.eye(3), np.asarray(half))
        return Obstacle(name, 'structure' if structure else 'sensor', shape, shape, 0., False, 'base', structure)
    obstacles = ObstacleSet([
        obstacle('fixture', [0., 0., 0.], [.04, .03, .04], True),
        obstacle('outboard', [.12, 0., -.06], [.01, .01, .01], False),
    ], None)
    grid = VoxelGrid.around(obstacles, .003)
    for point in ([0., 0., -.02], [.12, 0., -.06]):
        assert np.linalg.norm(grid.center(grid.flat(np.array(point))) - point) <= np.sqrt(3) * grid.voxel / 2
    fields = compute_fields(grid, obstacles)
    assert fields.static_raw[grid.flat(np.zeros(3))] < 0  # expanding bounds never unblocks solids


def test_outside_points_fail_instead_of_being_silently_clipped():
    grid = VoxelGrid(np.zeros(3), (5, 5, 5), .003)
    with pytest.raises(ValueError, match='outside the search volume'):
        grid.flat(np.array([0., 0., -.02]))


def test_native_enclosure_routes_inside_its_walls_and_above_its_real_floor(tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    root = write_project(tmp_path, block=False)
    outer = BRepPrimAPI_MakeBox(gp_Pnt(-160, -80, 0), 320, 160, 80).Shape()
    cavity = BRepPrimAPI_MakeBox(gp_Pnt(-150, -70, 3), 300, 140, 80).Shape()
    tray = BRepAlgoAPI_Cut(outer, cavity).Shape()
    publish_wiring_pull(root, primary_shapes={'plate': tray})
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    for node in router._resolve_nodes()['lead'].values():
        assert np.linalg.norm(router.grid.center(router.grid.flat(node.lead_end)) - node.lead_end) < .003
    routing = router.route()
    assert len(routing.wires) == 3
    tray_obstacle = obstacles.by_component('plate')
    for wire in routing.wires.values():
        assert not wire.failed
        dense = np.vstack([np.linspace(a, b, 20) for a, b in zip(wire.path[:-1], wire.path[1:])])
        assert dense[:, 2].min() > .003  # measured floor, not wall-top AABB
        assert dense[:, 2].max() < .08
        assert (tray_obstacle.shape.sdf(dense) - wire.bundle_radius.max()).min() >= tray_obstacle.keepout - .0005
        for end, point in zip(wire.ends, (wire.path[0], wire.path[-1])):
            assert np.linalg.norm(point - end.position) < 1e-9


def test_declared_waypoints_outside_structure_are_actual_destinations(tmp_path):
    root = write_project(tmp_path, block=False)
    file = root / 'robot.yaml'
    data = yaml.safe_load(file.read_text())
    # A waypoint beyond the fixture footprint used to be silently moved to its edge.
    waypoint = [.1, .12, .03]
    data['electrical']['harnesses'][0]['route']['via'] = [waypoint]
    file.write_text(yaml.safe_dump(data))
    project = Project.load(root)
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    assert np.linalg.norm(router.grid.center(router.grid.flat(np.array(waypoint))) - waypoint) < .003
    routing = router.route()
    from r3_wiring.geometry import project_to_polyline
    assert routing.wires and all(not w.failed for w in routing.wires.values())
    for wire in routing.wires.values():
        assert project_to_polyline(wire.path, np.array([waypoint]))[0] < .006
