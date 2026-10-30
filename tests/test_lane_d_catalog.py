"""Lane D catalog and scene: off-the-shelf parts, camera + CSI cables, Pi 5 variants, `extends`,
camera scene visuals and stage readiness."""

from __future__ import annotations

import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import yaml

from test_camera_twin import printbot  # Explicit native optics fixture.

from r3_catalog import default_catalog, load_catalog
from r3_model.project import Project
from r3_model.units import to_si
from r3_orchestrator.readiness import precheck
from r3_sim.vehicle import build_vehicle_model
from r3_sim.vehicle.scene_visual import cone_positions, render_ground_texture, track_centerline

FIXTURE = Path(__file__).parent / "fixtures" / "printbot"
NEW_PARTS = ["arducam-b0445-imx296", "arducam-fpc-22-22-150mm", "rpi-camera-cable-standard-mini-200mm",
             "traxxas-3695-magnum-272", "traxxas-3785-titan-12t-550", "traxxas-rear-halfshaft-l", "traxxas-rear-halfshaft-r",
             "traxxas-5116-bearing-5x11x4", "traxxas-front-axle-5mm", "traxxas-1747-nut-m4-nyloc", "traxxas-5878-sct-wheel-rear",
             "traxxas-5879-sct-wheel-front", "savox-sc-0251mg", "hobbywing-quicrun-wp-1060", "pololu-d36v50f5",
             "sparkfun-max-m10s", "gensace-5000-2s-60c-xt60", "gy-521-mpu6050"]


@pytest.mark.parametrize("type_id", NEW_PARTS)
def test_off_the_shelf_parts_carry_provenance_and_interfaces(type_id: str) -> None:
    part = default_catalog().component(type_id)
    assert part.interfaces, f"{type_id} has no interfaces for the assembly compiler"
    statuses = {part.provenance.status, part.mechanical.provenance.status}
    if part.behavior is not None:
        statuses.add(part.behavior.provenance.status)
        assert part.behavior.provenance.source or part.behavior.provenance.notes or part.behavior.model == "passive"
    assert statuses <= {"datasheet", "vendor", "measured", "estimated", "derived"}
    assert part.mechanical.provenance.source, f"{type_id}: mechanical source missing"


def test_drivetrain_numbers() -> None:
    catalog = default_catalog()
    gearbox = catalog.component("traxxas-3695-magnum-272").behavior.params
    assert gearbox["ratio"] == pytest.approx(86 / 23 * gearbox["internal_ratio"], abs=0.01)   # 23T/86T x 2.72
    # 48P pitch diameters 23/48 in and 86/48 in -> centre distance.
    assert to_si(gearbox["center_distance"]) == pytest.approx((23 + 86) / 48 * 0.0254 / 2, abs=1e-5)
    hub = catalog.component("traxxas-5878-sct-wheel-rear").interfaces["hub"].params
    hexagon = catalog.component("traxxas-rear-halfshaft-l").interfaces["hex"].params
    assert to_si(hexagon["flat_offset"]) == pytest.approx(0.006) and to_si(hub["flat_offset"]) > to_si(hexagon["flat_offset"])
    assert to_si(hexagon["d"]) == pytest.approx(0.012 / math.cos(math.radians(30)), abs=5e-5)   # 12 mm hex across corners
    bec = catalog.component("pololu-d36v50f5")
    assert bec.behavior.params["v_out"] == 5.0 and bec.pins["VIN"].voltage_min == 5.5
    assert catalog.component("gy-521-mpu6050").software[0].address == 0x68


def test_camera_and_csi_cables() -> None:
    catalog = default_catalog()
    camera = catalog.component("arducam-b0445-imx296")
    assert camera.kind == "camera" and camera.behavior.model == "camera.csi"
    params = camera.behavior.params
    assert params["sensor"].startswith("Sony IMX296") and params["resolution"] == [1456, 1088]
    # Pinhole check of the datasheet FOV: 6 mm lens on 1440 x 3.45 um = 4.97 mm -> 45 deg horizontal.
    assert 2 * math.degrees(math.atan(1440 * 3.45e-3 / 2 / 6.0)) == pytest.approx(45, abs=0.5)
    assert camera.connectors["J1"].type in catalog.connector("fpc-22p-0.5mm-cable").mates_with
    for cable_id, length in (("arducam-fpc-22-22-150mm", 0.15), ("rpi-camera-cable-standard-mini-200mm", 0.2)):
        cable = catalog.component(cable_id)
        assert cable.kind == "cable" and to_si(cable.behavior.params["length"]) == pytest.approx(length)
        a, b = (cable.connectors[n].pins for n in ("A", "B"))
        assert set(a.values()) >= {"SDA", "SCL", "3V3", "D0_P"} and a["22" if len(a) == 22 else "15"] == "3V3" == b["22"]


def test_pi5_variants_extend_the_8gb_board() -> None:
    catalog = default_catalog()
    base = catalog.component("raspberry-pi-5-8gb")
    assert {"CAM0", "CAM1"} <= set(base.connectors) and base.connectors["CAM0"].type == "rpi5-fpc-22p-0.5mm-vertical"
    assert base.connectors["CAM0"].direction == [0, 0, 1]  # measured top-entry vendor housing
    assert base.connectors["CAM0"].pins["21"] == "CAM0_SDA" and base.connectors["CAM0"].pins["22"] == "3V3"
    for ram in (1, 2, 4, 16):
        variant = catalog.component(f"raspberry-pi-5-{ram}gb")
        assert variant.name == f"Raspberry Pi 5 ({ram} GB)"
        assert variant.connectors.keys() == base.connectors.keys() and variant.interfaces.keys() == base.interfaces.keys()
        assert variant.behavior == base.behavior and variant.mechanical == base.mechanical


def test_extends_errors(tmp_path: Path) -> None:
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "a.yaml").write_text(yaml.safe_dump({"components": [{"id": "x", "extends": "nope"}]}))
    with pytest.raises(ValueError, match="extends unknown"):
        load_catalog(tmp_path)
    (tmp_path / "components" / "a.yaml").write_text(yaml.safe_dump({"components": [
        {"id": "x", "extends": "y"}, {"id": "y", "extends": "x"}]}))
    with pytest.raises(ValueError, match="cycle"):
        load_catalog(tmp_path)
    # A project variant may extend a platform part and override one field (deep merge).
    (tmp_path / "components" / "a.yaml").write_text(yaml.safe_dump({"components": [
        {"id": "my-wheel", "extends": "traxxas-5878-sct-wheel-rear", "mechanical": {"mass": 0.13}}]}))
    wheel = load_catalog(tmp_path).component("my-wheel")
    assert wheel.mechanical.mass == 0.13 and wheel.mechanical.size == default_catalog().component("traxxas-5878-sct-wheel-rear").mechanical.size


def test_track_texture_and_cones(tmp_path: Path) -> None:
    visual = {"ground": "grass", "track": {"points": [[0, 0], [10, 0], [10, 6], [0, 6]], "width": 1.0}, "cone_spacing": 2.0,
              "pixels_per_meter": 20}
    centerline = track_centerline(visual, None)
    assert centerline is not None and centerline[1] is True
    cones = cone_positions(visual, centerline)
    assert len(cones) == 2 * 16    # 32 m of closed track, a cone pair every 2 m
    left = [p for p, color in cones if color == (255, 110, 20)]
    # Orange cones on the left of the direction of travel: the first edge runs along +x at y = 0.
    assert all(p[1] > 0.5 for p in left[:5])
    path = tmp_path / "ground.png"
    width, height = render_ground_texture(visual, centerline, np.array([5.0, 3.0]), np.array([10.0, 8.0]), path)
    from PIL import Image

    image = np.asarray(Image.open(path))
    assert image.shape == (height, width, 3) == (320, 400, 3)
    # Inside the lane beside the dashed centre line (x = 5 m, y = 0.25 m) is asphalt grey; 3 m inside the loop is grass.
    lane = image[int((8 - (0.25 - 3)) * 20), int((5 - 5 + 10) * 20)]
    grass = image[int((8 - (3 - 3)) * 20), int((5 - 5 + 10) * 20)]
    assert abs(int(lane[0]) - int(lane[2])) < 15 and grass[1] > grass[0] + 20


def test_vehicle_model_has_the_camera_and_scene(printbot: Path) -> None:
    project = Project.load(printbot)
    project.spec.simulation.environment.visual = {"ground": "asphalt", "cones": [{"position": [3, 0.5], "color": "orange"}]}
    vm = build_vehicle_model(project)
    xml = ET.fromstring(vm.xml)
    camera = next(c for c in xml.iter("camera") if c.get("name") == "cam_camera")
    assert float(camera.get("fovy")) == pytest.approx(33.0)
    ground = next(g for g in xml.iter("geom") if g.get("name") == "ground")
    assert ground.get("material") == "r3_ground"
    cone = next(g for g in xml.iter("geom") if g.get("name") == "cone_0")
    assert cone.get("contype") == "0"     # visual only by default
    import mujoco

    model = mujoco.MjModel.from_xml_string(vm.xml)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    cam = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "cam_camera")
    # The camera looks forward (+x world): MuJoCo cameras look along their local -z.
    forward = -data.cam_xmat[cam].reshape(3, 3)[:, 2]
    up = data.cam_xmat[cam].reshape(3, 3)[:, 1]
    np.testing.assert_allclose(forward, [1, 0, 0], atol=1e-6)
    np.testing.assert_allclose(up, [0, 0, 1], atol=1e-6)




def test_readiness(tmp_path: Path) -> None:
    root = tmp_path / "printbot"
    shutil.copytree(FIXTURE, root)
    project = Project.load(root)
    assert precheck(project, "pcb").diagnostics[0].code == "not_applicable"
    assert precheck(project, "sim") is None  # passive robots inherit mandatory mechanical scenarios
    assert precheck(project, "electrical") is None and precheck(project, "wiring") is None
