"""Accuracy gates use the pinned downloaded surfaces, never catalog-made solids."""

import math
from decimal import Decimal

import numpy as np
import pytest
from scipy.spatial.distance import cdist
from shapely.geometry import Polygon

from r3_catalog import load_catalog
from r3_connectors.frames import Frame
from r3_connectors.mesh_mates import detect, scene_bodies
from r3_connectors.models import component_scene, ModelUnavailable
from r3_connectors.pins import instance_frame


@pytest.fixture(scope="module")
def parts():
    catalog = load_catalog()
    result = {}
    for identifier in ("raspberry-pi-5-4gb", "gy-521-mpu6050"):
        part = catalog.component(identifier)
        try:
            scene = component_scene(part.model)
        except ModelUnavailable as exc:
            pytest.skip(str(exc))
        result[identifier] = part, scene, scene_bodies(scene)
    return catalog, result


def test_j8_every_post_and_pin_one(parts):
    catalog, models = parts
    part, scene, bodies = models["raspberry-pi-5-4gb"]
    inst = part.connectors["J8"]
    g = catalog.connector(inst.type).geometry
    frame = instance_frame(inst)
    physical = detect(bodies, frame, g)
    assert len(physical.contacts) == 40
    expected = np.array([pin.position[:2] for pin in g.pins.values()])
    measured = np.array([pin.centre[:2] for pin in physical.contacts])
    assert cdist(expected, measured).min(axis=1).max() <= 0.00005
    # Drawing: header centre X=3.5+29 mm; 20 columns on 2.54 mm pitch.
    # Pin 1 is derived from that dimension chain, not explicitly dimensioned.
    pin1 = frame.point(g.pins["1"].position)
    bottom_left_hole = np.array([-39.0, -24.5, 0.0]) * 0.001
    np.testing.assert_allclose(
        (pin1 - bottom_left_hole)[:2],
        [29e-3 - 19 * 2.54e-3 / 2, 47.73e-3],
        atol=0.00005,
    )
    # Four bores independently recovered from PCB section, not from connectors.
    board = max(bodies, key=lambda m: np.prod(m.extents[:2]))
    mm = board.copy()
    mm.apply_scale(1000)
    section = mm.section(plane_origin=[0, 0, 0.5], plane_normal=[0, 0, 1])
    holes = []
    for loop in section.discrete:
        poly = Polygon(loop[:, :2])
        if np.all(np.abs(np.ptp(loop[:, :2], axis=0) - 2.7) < 0.05):
            holes.append([poly.centroid.x, poly.centroid.y])
    assert len(holes) == 4
    np.testing.assert_allclose(np.ptp(holes, axis=0), [58, 49], atol=0.00001)
    mount = part.mechanical.mount_holes
    assert [max(Decimal(str(p[i])) for p in mount) - min(Decimal(str(p[i])) for p in mount)
            for i in (0, 1)] == [Decimal("0.058"), Decimal("0.049")]


# Surface identifiers in the immutable RP-010083-CA-1 model. Each envelope
# extremum is an observed opening rim / header insulator top, not a catalog value.
PORTS = [
    ("J8", "OBJ3741", 2, 1, [0, 0, 1]),
    ("CAM0", "OBJ3525", 2, 1, [0, 0, 1]),
    ("CAM1", "OBJ3633", 2, 1, [0, 0, 1]),
    ("PCIE", "OBJ5005", 2, 1, [0, 0, 1]),
    ("FAN", "OBJ1911", 2, 1, [0, 0, 1]),
    ("UART", "OBJ3411", 2, 1, [0, 0, 1]),
    ("BAT", "OBJ1747", 2, 1, [0, 0, 1]),
    ("POE", "OBJ3831", 2, 1, [0, 0, 1]),
    ("USB2_0", "OBJ3733", 0, 1, [1, 0, 0]),
    ("USB2_1", "OBJ3733", 0, 1, [1, 0, 0]),
    ("USB3_0", "OBJ3725", 0, 1, [1, 0, 0]),
    ("USB3_1", "OBJ3725", 0, 1, [1, 0, 0]),
    ("ETH", "OBJ4357", 0, 1, [1, 0, 0]),
    ("HDMI0", "OBJ5085", 1, 0, [0, -1, 0]),
    ("HDMI1", "OBJ5053", 1, 0, [0, -1, 0]),
    ("USB_C", "OBJ5143", 1, 0, [0, -1, 0]),
]


@pytest.mark.parametrize("name,node_prefix,axis,end,opening", PORTS)
def test_every_pi_mating_plane(parts, name, node_prefix, axis, end, opening):
    _, models = parts
    part, scene, _ = models["raspberry-pi-5-4gb"]
    vertices = []
    for node in scene.graph.nodes_geometry:
        if node.split("_", 1)[-1].split("_", 1)[0] == node_prefix:
            pose, geometry = scene.graph[node]
            vertices.extend(
                Frame.from_matrix(pose).points(scene.geometry[geometry].vertices)
            )
    assert vertices, node_prefix
    coordinates = np.asarray(vertices)[:, axis]
    face = coordinates.max() if end else coordinates.min()
    frame = instance_frame(part.connectors[name])
    assert abs(frame.origin[axis] - face) <= 0.0001
    angle = math.acos(np.clip(frame.z @ opening, -1, 1))
    assert angle <= math.radians(1)


def test_gy521_eight_measured_pads(parts):
    catalog, models = parts
    part, _, bodies = models["gy-521-mpu6050"]
    inst = part.connectors["J1"]
    assert list(inst.pins.values()) == [
        "VCC",
        "GND",
        "SCL",
        "SDA",
        "XDA",
        "XCL",
        "AD0",
        "INT",
    ]
    assert "solids" not in part.mechanical.model_fields
    geometry = catalog.connector(inst.type).geometry
    physical = detect(bodies, instance_frame(inst), geometry)
    assert len(physical.contacts) == 8 and all(p.opening for p in physical.contacts)
    expected = np.array([p.position[:2] for p in geometry.pins.values()])
    found = np.array([p.centre[:2] for p in physical.contacts])
    assert cdist(expected, found).min(axis=1).max() <= 0.00005
    assert "features" not in part.mechanical.model_fields


@pytest.mark.parametrize("name", ["CAM0", "CAM1", "PCIE", "FAN", "UART", "BAT", "POE"])
def test_resolved_pi_contacts(parts, name):
    catalog, models = parts
    part, _, bodies = models["raspberry-pi-5-4gb"]
    inst = part.connectors[name]
    geometry = catalog.connector(inst.type).geometry
    physical = detect(bodies, instance_frame(inst), geometry)
    assert len(physical.contacts) == len(geometry.pins)
    expected = np.array([p.position[:2] for p in geometry.pins.values()])
    measured = np.array([p.centre[:2] for p in physical.contacts])
    assert cdist(expected, measured).min(axis=1).max() <= 0.00005
