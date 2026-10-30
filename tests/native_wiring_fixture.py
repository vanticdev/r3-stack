"""Explicit synthetic native CAD for routing tests, independent of catalog shapes.

Only fixture dimensions below define solids. Catalog connector frames locate test
hardware; no catalog body geometry or production generation path is used.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import yaml

HARDWARE_TYPE = "native-wiring-hardware"
# Test assets in mm: centred in x/y, bottom at z=0.
PART_SIZES = {"test-plate": (320, 160, 3), "test-block": (40, 80, 50),
              "test-arm": (20, 20, 20), "test-box": (20, 20, 20),
              "test-box-rev": (20, 20, 20), "test-pack": (60, 40, 20), "test-sink": (20, 20, 20)}
HOUSINGS = {"servo-3-female": (8.2, 3, 14), "xt30-male": (16, 8, 13.6),
            "xt30-female": (16, 8, 8), "xt60-male": (16, 9, 20.5), "xt60-female": (16, 9, 12.5)}


def _box(size: tuple[float, float, float], bottom: float = 0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    x, y, z = size
    return BRepPrimAPI_MakeBox(gp_Pnt(-x / 2, -y / 2, bottom), x, y, z).Shape()


def _pose(component: Any) -> np.ndarray:
    from r3_model.graph import rpy_matrix

    frame = np.eye(4)
    frame[:3, :3] = rpy_matrix(component.pose.rpy)
    frame[:3, 3] = component.pose.xyz
    return frame


def _topology(spec: Any, poses: dict[str, np.ndarray], owners: dict[str, str] | None = None):
    """Write explicit native mate frames and a tree preserving fixture joints."""
    from r3_connectors.frames import Frame, any_perpendicular

    connectors = {name: {"origin": np.eye(4)} for name in poses}
    mates = []
    moving = {joint.child: joint for joint in spec.mechanical.joints if joint.type != "fixed"}
    for child in poses:
        component = spec.components.get(child)
        parent = (owners or {}).get(child, component.mount if component else "world")
        joint = moving.get(child)
        if joint:
            parent = joint.parent
        if parent == "world" or parent not in poses:
            continue
        world = poses[child].copy()
        if joint:
            axis = np.asarray(joint.axis, float)
            world = Frame.from_axes(joint.origin, any_perpendicular(axis), axis).matrix()
        key = f"mount_{len(mates)}"
        connectors[parent][key] = np.linalg.inv(poses[parent]) @ world
        connectors[child][key] = np.linalg.inv(poses[child]) @ world
        mates.append({"id": joint.id if joint else key, "kind": (
            "slider" if joint and joint.type == "prismatic" else "revolute" if joint else "fastened"),
            "a": [parent, key], "b": [child, key], "axis": [0, 0, 1],
            "limits": list(joint.limits) if joint and joint.limits else None})
    return connectors, mates


def _hardware(node: Any):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from r3_connectors.frames import Frame, any_perpendicular

    if node.kind == "splice":
        axis = node.direction if node.direction is not None else np.array([1., 0., 0.])
        frame = Frame.from_axes(node.exit, any_perpendicular(axis), axis).matrix()
        radius = max(float(node.od) / 2, .0005) * 1000
        shape = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -3), gp_Dir(0, 0, 1)), radius, 6).Shape()
    else:
        size = HOUSINGS[node.connector_type]
        shape = _box(size, -size[2])
        if node.frame is not None:
            frame = node.frame.matrix()
        else:
            direction = node.direction if node.direction is not None else np.array([0., 0., 1.])
            z = -np.asarray(direction, float)
            frame = Frame.from_axes(node.exit + z * size[2] / 1000, any_perpendicular(z), z).matrix()
    return shape, frame


def publish_wiring_pull(root: Path, nodes: dict[str, dict[str, Any]] | None = None,
                        *, primary_shapes: dict[str, Any] | None = None) -> None:
    """Publish all fixture components and every nonintegral cable-side part.

    Pass hand-made nodes for collision unit tests. Otherwise resolve fixture
    connectors; the floating-pair fixture is placed once before publishing its
    native hardware, using the same primary routing field as the test.
    """
    from pulled_fixture import write_pull
    from r3_model.project import Project
    from r3_model.spec import RobotSpec
    from r3_wiring.bodies import Kinematics
    from r3_wiring.obstacles import build_obstacles
    from r3_wiring.router import HarnessRouter, RouterOptions

    root = Path(root)
    path = root / "robot.yaml"
    data = yaml.safe_load(path.read_text())
    data["components"] = {name: value for name, value in data["components"].items() if value.get("type") != HARDWARE_TYPE}
    for value in data["components"].values():
        value.pop("pulled", None)
    data.pop("attachments", None)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    spec = RobotSpec.model_validate(data)
    shapes = {name: _box(PART_SIZES[component.type]) for name, component in spec.components.items()}
    shapes.update(primary_shapes or {})
    poses = {name: _pose(component) for name, component in spec.components.items()}
    connectors, mates = _topology(spec, poses)
    write_pull(root, shapes, poses=poses, connectors=connectors, mates=mates)
    project = Project.load(root)
    if nodes is None:
        router = object.__new__(HarnessRouter)
        router.project, router.spec, router.catalog = project, project.spec, project.catalog
        router.graph, router.kin, router.diagnostics = project.graph, Kinematics(project.spec), []
        router._lead = lambda node, awgs: setattr(node, "lead_end", node.exit.copy())
        nodes = router._resolve_nodes()
        if any(node.kind == "floating" for group in nodes.values() for node in group.values()):
            obstacles, _ = build_obstacles(project)
            router = HarnessRouter(project, obstacles, RouterOptions(plan=False))
            nodes = router._resolve_nodes()
            router._place_floating(nodes)
    owners = {}
    for group in nodes.values():
        for node in group.values():
            if node.exit is None or (node.kind != "splice" and (not node.connector_type or node.connector_type == "integral")):
                continue
            shape, frame = _hardware(node)
            shapes[node.ref], poses[node.ref] = shape, frame
            owner = node.component if node.component in poses else next(iter(spec.components))
            data["components"][node.ref] = {"type": HARDWARE_TYPE, "mount": owner}
            owners[node.ref] = owner
    catalog = root / "catalog/components/native-wiring-hardware.yaml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(yaml.safe_dump({"components": [{"id": HARDWARE_TYPE, "kind": "cable",
        "name": "Synthetic cable hardware", "mechanical": {"mass": .002, "size": ["16 mm", "8 mm", "12.5 mm"]}}]}))
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    connectors, mates = _topology(spec, poses, owners)
    write_pull(root, shapes, poses=poses, connectors=connectors, mates=mates)
