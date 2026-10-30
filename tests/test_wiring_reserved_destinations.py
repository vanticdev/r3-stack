"""Plan short fan-outs and future ties without erasing physical occupancy."""

import numpy as np
import pytest

from r3_model.project import Project
from r3_wiring.grid import VoxelGrid
from r3_wiring.obstacles import build_obstacles
from r3_wiring.planner import occupancy_field
from r3_wiring.router import HarnessRouter, RouterOptions
from test_wiring_support import write_project


def test_terminal_fanout_preserves_physical_gap_and_remote_emc_margin():
    grid = VoxelGrid(np.array([-.01, -.01, -.01]), (81, 31, 21), .001)
    points = np.column_stack([np.linspace(0, .06, 61), np.zeros((61, 2))])
    occupied = [(points, .001, {"power"})]
    ordinary = occupancy_field(grid, occupied, {"signal"})
    terminal = occupancy_field(grid, occupied, {"signal"}, termination_exits=[np.zeros(3)])
    queries = np.array([[0, .005, 0], [.04, .005, 0], [0, .001, 0]])
    # The short fan-out retains the occupied radius plus the 0.5 mm crossing
    # gap; a long parallel run still reserves the full 5 mm EMC separation.
    assert grid.sample(terminal, queries) == pytest.approx([.0035, -.0015, -.0005])
    assert grid.sample(ordinary, queries) == pytest.approx([-.0015, -.0015, -.0055])


def test_required_tie_destination_is_reserved_before_its_harness_is_planned(tmp_path):
    project = Project.load(write_project(tmp_path, block=False))
    project.spec.electrical.harnesses[0].route.tie_points = [[.1, 0, .04]]
    obstacles, _ = build_obstacles(project)
    router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
    own = router.cable_field({}, {"lead"})
    earlier = router.cable_field({}, {"earlier_power"})
    query = np.array([[.1, 0, .04], [.2, 0, .04]])
    assert router.grid.sample(own, query) == pytest.approx([.05, .05])
    assert router.grid.sample(earlier, query)[0] < -.003
    assert router.grid.sample(earlier, query)[1] == pytest.approx(.05)
