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


def _fixture_nodes(project: Any) -> dict[str, dict[str, Any]]:
    """Explicit installed fixture frames; never call the production auto-placer."""
    from r3_connectors.frames import Frame, any_perpendicular
    from r3_connectors.pins import mating_site_frame
    from r3_wiring.geometry import bundle_diameter
    from r3_wiring.router import NodePoint
    from r3_wiring.terminations import exit_offset

    harnesses = {h.id: h for h in project.spec.electrical.harnesses}
    reciprocal = {connector.mates: f"{h.id}.{name}" for h in harnesses.values()
                  for name, connector in h.connectors.items() if connector.mates}
    pairs: dict[tuple[str, str], tuple[str, Frame]] = {}
    out = {}
    for harness in harnesses.values():
        nodes = {}
        for name, connector in harness.connectors.items():
            if connector.type == "integral":
                continue
            ref = f"{harness.id}.{name}"
            mate_ref = connector.mates or reciprocal.get(ref, "")
            owner, _, _ = mate_ref.partition(".")
            component = None
            if owner in project.spec.components:
                frame = mating_site_frame(project, ref, connector).mated()
                component, kind = owner, "connector"
            elif owner in harnesses:
                key = tuple(sorted((ref, mate_ref)))
                if key not in pairs:
                    # The synthetic inline pair is installed 100 mm from the
                    # datum, 25 mm above it, along X. Further test pairs have
                    # explicitly separated Y stations; no space search occurs.
                    face = [.1, .03 * len(pairs), .025]
                    wire_axis = np.array([-1., 0., 0.])
                    if connector.position is not None:
                        face = connector.position
                    if connector.direction is not None:
                        wire_axis = np.asarray(connector.direction, float)
                    pairs[key] = ref, Frame.from_axes(face, any_perpendicular(wire_axis), -wire_axis)
                first, base = pairs[key]
                frame, kind = (base if first == ref else base.mated()), "floating"
            else:
                wire_axis = np.array([0., 0., 1.])
                if connector.direction is not None:
                    wire_axis = np.asarray(connector.direction, float)
                exit_point = np.asarray(connector.position, float)
                face = exit_point - wire_axis * exit_offset(project.catalog, connector.type)
                frame, kind = Frame.from_axes(face, any_perpendicular(wire_axis), -wire_axis), "free"
            awgs = [wire.awg for wire in harness.wires for end in (wire.from_, wire.to)
                    if end.partition(".")[0] == name]
            od = bundle_diameter(float(project.catalog.wire(awg).outer_diameter) for awg in awgs) if awgs else 0.
            nodes[name] = NodePoint(harness.id, name, kind,
                exit=frame.origin - frame.z * exit_offset(project.catalog, connector.type),
                direction=-frame.z, connector_type=connector.type, mates=connector.mates,
                component=component, face=frame.origin, frame=frame, od=od)
        for name, splice in harness.splices.items():
            awgs = [wire.awg for wire in harness.wires for end in (wire.from_, wire.to) if end == name]
            od = bundle_diameter(float(project.catalog.wire(awg).outer_diameter) for awg in awgs) if awgs else .002
            axis = np.asarray(splice.direction if splice.direction is not None else [1., 0., 0.], float)
            axis /= np.linalg.norm(axis)
            nodes[name] = NodePoint(harness.id, name, "splice", exit=np.asarray(splice.position, float), direction=axis, od=od)
        out[harness.id] = nodes
    return out


def publish_wiring_pull(root: Path, nodes: dict[str, dict[str, Any]] | None = None,
                        *, primary_shapes: dict[str, Any] | None = None) -> None:
    """Publish all fixture components and every nonintegral cable-side part.

    Pass hand-made nodes for collision unit tests. Otherwise use explicitly
    installed fixture mating frames, including the inline pair. Publishing
    does not resolve nodes or place hardware through the production router.
    """
    from pulled_fixture import write_pull
    from r3_model.project import Project
    from r3_model.spec import RobotSpec

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
    project = Project(root, spec)
    if nodes is None:
        nodes = _fixture_nodes(project)
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
    write_pull(root, shapes, poses=poses, connectors=connectors, mates=mates, mass=.02)
