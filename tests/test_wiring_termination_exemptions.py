"""A board-mounted connector must not hide collisions with its supporting computer."""

from types import SimpleNamespace

import numpy as np

from r3_model.project import Project
from r3_wiring.bodies import Kinematics
from r3_wiring.checks import HarnessChecker
from r3_wiring.geometry import Box
from r3_wiring.mujoco_cables import _end_info
from r3_wiring.obstacles import Obstacle, ObstacleSet
from r3_wiring.router import HarnessRouter
from test_wiring_support import write_project


def test_mount_parent_collision_inside_connector_termination_zone(tmp_path, monkeypatch) -> None:
    project = Project.load(write_project(tmp_path, block=True))
    project.spec.components["src"].mount = "block"
    kin = Kinematics(project.spec)
    # Resolve the real connector ownership without computing a routing grid.
    router = object.__new__(HarnessRouter)
    router.project, router.spec, router.catalog = project, project.spec, project.catalog
    router.graph, router.kin, router.diagnostics = project.graph, kin, []
    monkeypatch.setattr(router, "_lead", lambda node, awgs: None)
    nodes = router._resolve_nodes()
    start = nodes["lead"]["A"]
    start.lead_length = 0.030
    path = np.array([start.exit, start.exit + [0.060, 0, 0]])
    # Parent USB-style block occupies 20 mm immediately after the cable exit.
    shape = Box(start.exit + [0.010, 0, 0], np.eye(3), np.array([0.010, 0.010, 0.010]))
    obstacle = Obstacle("block", "computer", shape, shape, 0.0, False, "robot", False)
    obstacles = ObstacleSet([obstacle], kin)
    wire = SimpleNamespace(wire_id="W1", harness="lead", from_node="A", to_node="B",
                           path=path, bundle_radius=np.array([0.001, 0.001]))
    routing = SimpleNamespace(nodes=nodes, wires={"W1": wire}, holes=[])
    checker = HarnessChecker(project, obstacles, routing)
    checker._clearance_and_intersection()
    collisions = [d for d in checker.diagnostics if d.code == "wire_intersects_component"]
    assert len(collisions) == 1
    assert collisions[0].data["component"] == "block"
    assert collisions[0].data["penetration"] > 0.009
    # The physical cable simulation must use the same ownership rule.
    _, exempt, _ = _end_info(project, kin, project.spec.electrical.harnesses[0], "A.1")
    assert exempt == {"src"}
