"""Generate a MuJoCo MJCF vehicle model from the robot spec.

Body tree: every ``mechanical.joints`` child becomes its own body under the
joint parent's body; every other component is *lumped* into the body of the
nearest mount ancestor that is a body (fixed mounts carry no relative motion,
so their mass/inertia is merged with the parallel-axis theorem). The root
(the world-mounted component, e.g. the chassis) gets a free joint.

All body frames are aligned with the robot frame (+x forward, +y left, +z up);
a body's origin is its joint origin (root: the robot origin, which is the
ground point below the rear axle). Mass properties come from
``build/cad/cad_model.json`` when present (CAD mass, COM, inertia about the COM
in robot axes) and otherwise from catalog mass with solid box/cylinder inertia.

Wheels collide as MuJoCo *cylinders* (native plane-cylinder contact, which
returns up to four rim/edge contacts per wheel, so the tire contact patch has
width and resists yaw scrub). No capsule/ellipsoid approximation is used
because MuJoCo 3.x supports cylinder-plane and cylinder-box contact natively.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.graph import rpy_matrix
from r3_model.project import Project
from r3_model.units import to_si

from ..geometry import MassProperties, axis_rotation, box_inertia, combine, cylinder_inertia, rotation_to_quat

#: Contact bitmasks: robot geoms (1) collide only with the environment (2).
ROBOT_CONTYPE, ROBOT_CONAFFINITY = 1, 0
WORLD_CONTYPE, WORLD_CONAFFINITY = 0, 1


@dataclass
class WheelInfo:
    component: str
    joint: str
    radius: float
    width: float
    friction: float
    rolling_resistance: float
    driven: bool = False
    steered_by: str | None = None


@dataclass
class DriveInfo:
    """A differential (or direct) transmission from a motor to wheel joints."""

    id: str
    actuator_component: str
    joints: list[str]
    ratio: float
    efficiency: float
    tendon: str
    rotor_inertia: float
    output_inertia: float
    backlash: float


@dataclass
class SteeringInfo:
    id: str
    actuator_component: str
    joints: list[str]  # [left, right] ordering by +y position
    ratio: float  # wheel steer angle per servo horn angle
    wheelbase: float
    kingpin_track: float
    max_steer: float
    actuators: list[str]


@dataclass
class VehicleModel:
    xml: str
    root: str
    bodies: dict[str, list[str]]  # body component -> lumped component ids
    body_origin: dict[str, np.ndarray]
    mass_properties: dict[str, MassProperties]
    total_mass: float
    wheels: dict[str, WheelInfo]
    drives: list[DriveInfo]
    steering: list[SteeringInfo]
    imu_site: str | None
    imu_component: str | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    assumptions: list[tuple[str, str]] = field(default_factory=list)
    mass_source: dict[str, str] = field(default_factory=dict)
    geometry: dict[str, dict[str, Any]] = field(default_factory=dict)  # body -> primitive shape (for clearance)


def _fmt(values: Any) -> str:
    return " ".join(f"{float(v):.9g}" for v in np.ravel(values))


def _component_shape(project: Project, cid: str) -> tuple[str, list[float], str]:
    mech = project.catalog.component(project.spec.components[cid].type).mechanical
    shape = "cylinder" if mech.shape == "cylinder" else "box"
    return shape, [float(v) for v in mech.size], mech.axis


def _local_center(shape: str, size: list[float]) -> np.ndarray:
    """Catalog convention: box frames sit at the center of the bottom face;
    cylinder frames sit at the cylinder center."""
    if shape == "box":
        return np.array([0.0, 0.0, size[2] / 2.0])
    return np.zeros(3)


def catalog_mass_properties(project: Project, cid: str) -> MassProperties:
    mech = project.catalog.component(project.spec.components[cid].type).mechanical
    mass = float(project.spec.components[cid].params.get("mass", mech.mass))
    shape, size, axis = _component_shape(project, cid)
    if shape == "cylinder":
        local = cylinder_inertia(mass, size[0], size[1], axis)
    else:
        local = box_inertia(mass, size)
    position, rotation = project.graph.component_pose(cid)
    com = position + rotation @ _local_center(shape, size)
    return MassProperties(mass, com, rotation @ local @ rotation.T, "catalog")


def cad_mass_properties(part: dict[str, Any]) -> MassProperties:
    ixx, iyy, izz, ixy, ixz, iyz = (float(v) for v in part["inertia"])
    inertia = np.array([[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]])
    return MassProperties(float(part["mass"]), np.asarray(part["com"], dtype=float), inertia, "cad")


def _joint_children(project: Project) -> dict[str, Any]:
    return {joint.child: joint for joint in project.spec.mechanical.joints}


def _owner_body(project: Project, cid: str, joint_children: dict[str, Any], root: str) -> str:
    seen = set()
    while cid not in joint_children and cid != root:
        if cid in seen:
            raise ValueError(f"mount cycle at {cid!r}")
        seen.add(cid)
        mount = project.spec.components[cid].mount
        if mount == "world":
            return root
        cid = mount
    return cid


def _find_root(project: Project, joint_children: dict[str, Any]) -> str:
    roots = [cid for cid, comp in project.spec.components.items() if comp.mount == "world" and cid not in joint_children]
    if not roots:
        raise ValueError("no world-mounted root component for the vehicle body")
    # Prefer a structural component as the free-floating base.
    for cid in roots:
        if project.catalog.component(project.spec.components[cid].type).kind == "structure":
            return cid
    return roots[0]


def _behavior_params(project: Project, cid: str) -> dict[str, Any]:
    ctype = project.catalog.component(project.spec.components[cid].type)
    params = dict(ctype.behavior.params) if ctype.behavior else {}
    params.update(project.spec.components[cid].params)
    return params


def _si(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return to_si(value)
    except ValueError:
        return default


def _hex_rgba(color: str, alpha: float = 1.0) -> str:
    color = color.lstrip("#")
    if len(color) != 6:
        return f"0.55 0.55 0.55 {alpha}"
    r, g, b = (int(color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return f"{r:.3f} {g:.3f} {b:.3f} {alpha}"


def build_vehicle_model(project: Project, initial_pose: tuple[float, float, float] = (0.0, 0.0, 0.0),
                        obstacles: list[dict[str, Any]] | None = None,
                        course: list[list[float]] | None = None) -> VehicleModel:
    """Build the MJCF for the spec. ``initial_pose`` is (x, y, yaw) of the robot
    origin in the world; ``course`` waypoints are drawn as visual markers."""
    spec = project.spec
    catalog = project.catalog
    diagnostics: list[Diagnostic] = []
    assumptions: list[tuple[str, str]] = []
    joint_children = _joint_children(project)
    root = _find_root(project, joint_children)

    cad = project.read_build_json("cad/cad_model.json") or {}
    cad_parts: dict[str, Any] = cad.get("parts", {}) if isinstance(cad, dict) else {}
    mesh_dir = project.root / "build" / "cad"

    # --- body membership and mass properties
    bodies: dict[str, list[str]] = {root: []}
    for child in joint_children:
        bodies[child] = []
    for cid in spec.components:
        bodies[_owner_body(project, cid, joint_children, root)].append(cid)

    estimated: list[str] = []
    part_props: dict[str, MassProperties] = {}
    mass_source: dict[str, str] = {}
    for cid in spec.components:
        part = cad_parts.get(cid)
        if part and part.get("mass") and part.get("inertia") and part.get("com"):
            part_props[cid] = cad_mass_properties(part)
            mass_source[cid] = str(part.get("mass_status", "cad"))
        else:
            part_props[cid] = catalog_mass_properties(project, cid)
            mass_source[cid] = "catalog"
            estimated.append(cid)
    if estimated:
        diagnostics.append(Diagnostic.warning(
            "cad_mass_estimated",
            f"{len(estimated)} of {len(spec.components)} components use catalog mass with solid box/cylinder inertia "
            "(no CAD mass properties in build/cad/cad_model.json)",
            subjects=sorted(estimated),
            data={"estimated_count": len(estimated), "component_count": len(spec.components),
                  "estimated_mass": round(sum(part_props[c].mass for c in estimated), 6)},
            hint="Run the cad stage to replace estimates with Onshape mass properties."))
        assumptions.append(("mechanical", "Components without CAD mass properties are solid homogeneous boxes/cylinders "
                                          "with catalog mass; fixed-mounted parts are lumped into their parent body."))

    body_props = {body: combine([part_props[c] for c in members]) for body, members in bodies.items()}
    body_origin: dict[str, np.ndarray] = {root: np.zeros(3)}
    for child, joint in joint_children.items():
        body_origin[child] = np.asarray(joint.origin, dtype=float)
    total_mass = sum(p.mass for p in body_props.values())

    # --- wheels
    wheels: dict[str, WheelInfo] = {}
    env = spec.simulation.environment
    for cid, comp in spec.components.items():
        ctype = catalog.component(comp.type)
        if ctype.kind != "wheel" or cid not in joint_children:
            continue
        params = _behavior_params(project, cid)
        _, size, _ = _component_shape(project, cid)
        wheels[cid] = WheelInfo(
            component=cid, joint=joint_children[cid].id,
            radius=_si(params.get("radius"), size[0] / 2.0), width=_si(params.get("width"), size[1]),
            friction=float(params.get("friction", env.ground_friction)),
            rolling_resistance=float(env.ground_rolling_resistance if env.ground_rolling_resistance is not None
                                     else params.get("rolling_resistance", 0.015)))

    # --- transmissions
    joints_by_id = {j.id: j for j in spec.mechanical.joints}
    drives: list[DriveInfo] = []
    steering: list[SteeringInfo] = []
    for trans in spec.mechanical.transmissions:
        if trans.type in ("differential", "direct"):
            motor_params = _behavior_params(project, trans.actuator)
            gear_params: dict[str, Any] = {}
            for joint_id in trans.joints:
                if joint_id not in joints_by_id:
                    continue
                # Walk the mount chain from the driven part (wheel -> axle -> gearbox ...) to its gearbox.
                cid, seen = joints_by_id[joint_id].child, set()
                while cid in spec.components and cid not in seen:
                    seen.add(cid)
                    if catalog.component(spec.components[cid].type).kind == "gearbox":
                        gear_params = _behavior_params(project, cid)
                        break
                    cid = spec.components[cid].mount
            drives.append(DriveInfo(
                id=trans.id, actuator_component=trans.actuator, joints=list(trans.joints), ratio=float(trans.ratio),
                efficiency=float(trans.efficiency), tendon=f"tendon_{trans.id}",
                rotor_inertia=_si(motor_params.get("rotor_inertia"), 0.0),
                output_inertia=_si(gear_params.get("output_inertia"), 0.0),
                backlash=_si(gear_params.get("backlash"), 0.0)))
            for joint_id in trans.joints:
                for wheel in wheels.values():
                    if wheel.joint == joint_id:
                        wheel.driven = True
        elif trans.type == "ackermann_steering":
            ordered = sorted(trans.joints, key=lambda j: -joints_by_id[j].origin[1])
            params = trans.params
            left, right = (joints_by_id[j].origin for j in ordered[:2])
            track = _si(params.get("kingpin_track"), abs(left[1] - right[1]))
            limits = joints_by_id[ordered[0]].limits or [-0.6, 0.6]
            steering.append(SteeringInfo(
                id=trans.id, actuator_component=trans.actuator, joints=ordered, ratio=float(trans.ratio),
                wheelbase=_si(params.get("wheelbase"), 0.26), kingpin_track=track,
                max_steer=_si(params.get("max_steer"), float(max(abs(v) for v in limits))),
                actuators=[f"act_{j}" for j in ordered]))
    for steer in steering:
        for joint_id in steer.joints:
            for wheel in wheels.values():
                parent = joints_by_id[wheel.joint].parent
                if parent in joint_children and joint_children[parent].id == joint_id:
                    wheel.steered_by = joint_id

    # --- XML
    mj = ET.Element("mujoco", model=spec.robot.name)
    ET.SubElement(mj, "compiler", angle="radian", autolimits="true", meshdir=str(mesh_dir))
    ET.SubElement(mj, "option", timestep=f"{spec.simulation.timestep:.9g}", integrator="implicitfast", cone="elliptic",
                  impratio="10", gravity=f"0 0 {-env.gravity:.9g}")
    visual = ET.SubElement(mj, "visual")
    ET.SubElement(visual, "global", offwidth="1280", offheight="960", azimuth="135", elevation="-25")
    ET.SubElement(visual, "quality", shadowsize="4096", offsamples="8")
    ET.SubElement(visual, "map", znear="0.005", zfar="5000", haze="0.15")
    ET.SubElement(visual, "headlight", ambient="0.35 0.35 0.35", diffuse="0.6 0.6 0.6")
    asset = ET.SubElement(mj, "asset")
    ET.SubElement(asset, "texture", name="sky", type="skybox", builtin="gradient", rgb1="0.55 0.7 0.9", rgb2="0.95 0.97 1.0",
                  width="512", height="512")
    ET.SubElement(asset, "texture", name="grass", type="2d", builtin="checker", rgb1="0.33 0.5 0.26", rgb2="0.29 0.45 0.23",
                  width="512", height="512", mark="edge", markrgb="0.4 0.58 0.32")
    ET.SubElement(asset, "material", name="ground", texture="grass", texrepeat="1 1", texuniform="true", reflectance="0.0")

    meshes_used: dict[str, str] = {}
    for cid in spec.components:
        mesh_rel = (cad_parts.get(cid) or {}).get("mesh")
        if mesh_rel and (mesh_dir / mesh_rel).exists():
            meshes_used[cid] = f"mesh_{cid}"
            ET.SubElement(asset, "mesh", name=f"mesh_{cid}", file=str(mesh_dir / mesh_rel))
    if meshes_used:
        assumptions.append(("mechanical", "CAD STL meshes are authored in the robot frame (m) and are visual only; "
                                          "collision uses primitive geoms."))

    world = ET.SubElement(mj, "worldbody")
    ET.SubElement(world, "light", name="sun", pos="0 0 50", dir="-0.3 -0.2 -1", directional="true", castshadow="true",
                  diffuse="0.8 0.8 0.75")
    ET.SubElement(world, "geom", name="ground", type="plane", size="0 0 0.05", material="ground",
                  friction=f"{env.ground_friction:.6g} 0.005 0.0001", contype=str(WORLD_CONTYPE), conaffinity=str(WORLD_CONAFFINITY))
    for obstacle in obstacles or []:
        center = [to_si(v) for v in obstacle.get("center", [0, 0, 0])]
        size = [to_si(v) for v in obstacle.get("size", [0.3, 0.3, 0.3])]
        ET.SubElement(world, "geom", name=f"obstacle_{obstacle.get('id', len(world))}", type="box", pos=_fmt(center),
                      size=_fmt(np.asarray(size) / 2.0), rgba="0.95 0.45 0.1 1",
                      contype=str(WORLD_CONTYPE), conaffinity=str(WORLD_CONAFFINITY))
    for index, waypoint in enumerate(course or []):
        ET.SubElement(world, "geom", name=f"waypoint_{index}", type="cylinder", pos=f"{waypoint[0]:.6g} {waypoint[1]:.6g} 0.002",
                      size="0.25 0.002", rgba="1 1 1 0.8", contype="0", conaffinity="0", group="2")
    if env.radio_transmitter is not None:
        tx = env.radio_transmitter.position
        ET.SubElement(world, "geom", name="radio_tx_mast", type="cylinder", pos=f"{tx[0]:.6g} {tx[1]:.6g} {tx[2] / 2:.6g}",
                      size=f"0.02 {tx[2] / 2:.6g}", rgba="0.2 0.2 0.25 1", contype="0", conaffinity="0")
        ET.SubElement(world, "geom", name="radio_tx", type="box", pos=_fmt(tx), size="0.08 0.05 0.04", rgba="0.9 0.1 0.1 1",
                      contype="0", conaffinity="0")

    x0, y0, yaw0 = initial_pose
    elements: dict[str, ET.Element] = {}
    geometry: dict[str, dict[str, Any]] = {}

    def add_body(body: str, parent_el: ET.Element, parent_origin: np.ndarray) -> None:
        origin = body_origin[body]
        if body == root:
            el = ET.SubElement(parent_el, "body", name=body, pos=f"{x0:.9g} {y0:.9g} 0.0005",
                               quat=_fmt(rotation_to_quat(rpy_matrix([0, 0, yaw0]))))
            ET.SubElement(el, "freejoint", name=f"{body}_free")
        else:
            el = ET.SubElement(parent_el, "body", name=body, pos=_fmt(origin - parent_origin))
            joint = joint_children[body]
            jtype = {"revolute": "hinge", "continuous": "hinge", "prismatic": "slide"}.get(joint.type)
            if jtype:
                attrs = {"name": joint.id, "type": jtype, "axis": _fmt(joint.axis), "damping": f"{joint.damping:.9g}"}
                if joint.friction:
                    attrs["frictionloss"] = f"{joint.friction:.9g}"
                if joint.type != "continuous" and joint.limits:
                    attrs["range"] = _fmt(joint.limits)
                ET.SubElement(el, "joint", **attrs)
        elements[body] = el
        props = body_props[body]
        inertia = props.inertia
        ET.SubElement(el, "inertial", pos=_fmt(props.com - origin), mass=f"{max(props.mass, 1e-6):.9g}",
                      fullinertia=_fmt([inertia[0, 0], inertia[1, 1], inertia[2, 2], inertia[0, 1], inertia[0, 2], inertia[1, 2]]))
        for cid in bodies[body]:
            ctype = catalog.component(spec.components[cid].type)
            position, rotation = project.graph.component_pose(cid)
            shape, size, axis = _component_shape(project, cid)
            rgba = _hex_rgba(ctype.mechanical.color)
            common = {"contype": "0", "conaffinity": "0", "group": "1", "mass": "0", "rgba": rgba}
            if cid in meshes_used:
                ET.SubElement(el, "geom", name=f"vis_{cid}", type="mesh", mesh=meshes_used[cid], pos=_fmt(-origin), **common)
            else:
                center = position + rotation @ _local_center(shape, size) - origin
                if shape == "cylinder":
                    rot = rotation @ axis_rotation(axis)
                    ET.SubElement(el, "geom", name=f"vis_{cid}", type="cylinder", pos=_fmt(center), quat=_fmt(rotation_to_quat(rot)),
                                  size=f"{size[0] / 2:.9g} {size[1] / 2:.9g}", **common)
                else:
                    ET.SubElement(el, "geom", name=f"vis_{cid}", type="box", pos=_fmt(center), quat=_fmt(rotation_to_quat(rotation)),
                                  size=_fmt(np.asarray(size) / 2.0), **common)
            if cid == body and body != root:
                center_robot = position + rotation @ _local_center(shape, size)
                geometry[body] = {"shape": shape, "size": size, "axis_vector": (rotation @ axis_rotation(axis))[:, 2].tolist()
                                  if shape == "cylinder" else None, "center_offset": (center_robot - origin).tolist(),
                                  "rotation": rotation.tolist(), "keepout": float(ctype.mechanical.keepout_margin)}
        if body in wheels:
            wheel = wheels[body]
            joint = joint_children[body]
            axis_vec = np.asarray(joint.axis, dtype=float)
            # Cylinder local z -> wheel spin axis.
            z = axis_vec / np.linalg.norm(axis_vec)
            ref = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
            x = np.cross(ref, z)
            x /= np.linalg.norm(x)
            rot = np.column_stack([x, np.cross(z, x), z])
            wheel_center = project.graph.component_pose(body)[0] - origin
            ET.SubElement(el, "geom", name=f"tire_{body}", type="cylinder", pos=_fmt(wheel_center), quat=_fmt(rotation_to_quat(rot)),
                          size=f"{wheel.radius:.9g} {wheel.width / 2:.9g}", friction=f"{wheel.friction:.6g} 0.005 0.0001",
                          condim="3", solref="0.004 1", solimp="0.95 0.99 0.001", mass="0",
                          contype=str(ROBOT_CONTYPE), conaffinity=str(ROBOT_CONAFFINITY), rgba="0.12 0.12 0.12 0.0", group="3")
        if body == root:
            # Underbody collision box (bottoming out / rollover), from the root component's footprint.
            shape, size, _ = _component_shape(project, root)
            position, rotation = project.graph.component_pose(root)
            center = position + rotation @ _local_center("box", size)
            ET.SubElement(el, "geom", name="underbody", type="box", pos=_fmt(center), size=_fmt(np.asarray(size) / 2.0),
                          friction="0.4 0.005 0.0001", condim="3", mass="0", contype=str(ROBOT_CONTYPE),
                          conaffinity=str(ROBOT_CONAFFINITY), rgba="0 0 0 0", group="3")
        for child, joint in joint_children.items():
            parent_body = _owner_body(project, joint.parent, joint_children, root) if joint.parent != "world" else root
            if parent_body == body:
                add_body(child, el, origin)

    add_body(root, world, np.zeros(3))

    # IMU site at the component that exposes an mpu6050 software interface.
    imu_component = None
    for cid, comp in spec.components.items():
        for interface in catalog.component(comp.type).software:
            if interface.protocol == "mpu6050":
                imu_component = cid
    imu_site = None
    if imu_component is not None:
        body = _owner_body(project, imu_component, joint_children, root)
        position, rotation = project.graph.component_pose(imu_component)
        ET.SubElement(elements[body], "site", name="imu", pos=_fmt(position - body_origin[body]),
                      quat=_fmt(rotation_to_quat(rotation)), size="0.004", rgba="1 0 0 1", group="4")
        imu_site = "imu"
        assumptions.append(("sensors", f"IMU axes are aligned with the {imu_component} component frame at its origin."))
    ET.SubElement(elements[root], "site", name="chassis_ref", pos="0 0 0", size="0.003", group="4")
    camera_target = body_props[root].com
    ET.SubElement(elements[root], "camera", name="chase", mode="trackcom", pos=_fmt(camera_target + np.array([-0.9, -0.55, 0.45])),
                  xyaxes="0.52 -0.85 0 0.3 0.18 0.94")

    tendon = ET.SubElement(mj, "tendon")
    actuator = ET.SubElement(mj, "actuator")
    for drive in drives:
        # Fixed tendon length = mean wheel angle = differential carrier angle.
        # Armature = J_rotor N^2 + J_output: kinetic energy 1/2 J_m (N w_c)^2 reflected onto the carrier.
        fixed = ET.SubElement(tendon, "fixed", name=drive.tendon,
                              armature=f"{drive.rotor_inertia * drive.ratio ** 2 + drive.output_inertia:.9g}")
        for joint_id in drive.joints:
            ET.SubElement(fixed, "joint", joint=joint_id, coef=f"{1.0 / len(drive.joints):.9g}")
        ET.SubElement(actuator, "motor", name=f"act_{drive.id}", tendon=drive.tendon, gear="1")
    for steer in steering:
        for joint_id, act in zip(steer.joints, steer.actuators):
            # Stiff tie-rod linkage: knuckle tracks its Ackermann target angle through a PD
            # element (kp = linkage stiffness N*m/rad, kv = damping N*m*s/rad, estimated).
            ET.SubElement(actuator, "general", name=act, joint=joint_id, gainprm="40", biastype="affine", biasprm="0 -40 -0.4")
    if drives:
        assumptions.append(("mechanical", "Open differential splits carrier torque equally (fixed tendon, coef 1/n); motor "
                                          "rotor inertia is reflected through ratio^2 onto the carrier; gear backlash is not modeled."))

    sensor = ET.SubElement(mj, "sensor")
    if imu_site:
        ET.SubElement(sensor, "accelerometer", name="imu_acc", site=imu_site)
        ET.SubElement(sensor, "gyro", name="imu_gyro", site=imu_site)
    ET.SubElement(sensor, "framequat", name="chassis_quat", objtype="site", objname="chassis_ref")

    xml = ET.tostring(mj, encoding="unicode")
    return VehicleModel(xml=xml, root=root, bodies=bodies, body_origin=body_origin, mass_properties=body_props,
                        total_mass=total_mass, wheels=wheels, drives=drives, steering=steering, imu_site=imu_site,
                        imu_component=imu_component, diagnostics=diagnostics, assumptions=assumptions,
                        mass_source=mass_source, geometry=geometry)


def write_mjcf(model: VehicleModel, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    import xml.dom.minidom

    path.write_text(xml.dom.minidom.parseString(model.xml).toprettyxml(indent="  "))
    return path


def mass_report(model: VehicleModel) -> dict[str, Any]:
    return {body: {"mass": round(p.mass, 6), "com": [round(v, 6) for v in p.com], "source": p.source,
                   "members": model.bodies[body]} for body, p in model.mass_properties.items()} | {
        "_total_mass": round(model.total_mass, 6)}


__all__ = ["VehicleModel", "WheelInfo", "DriveInfo", "SteeringInfo", "build_vehicle_model", "write_mjcf",
           "catalog_mass_properties", "cad_mass_properties", "mass_report"]
