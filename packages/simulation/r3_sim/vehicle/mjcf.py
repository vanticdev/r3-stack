"""Generate a MuJoCo MJCF vehicle model from the robot spec.

Every component and load-bearing fastener/nut has its own massive body.
Nominal mounts define placement only; real compiled connections supply
breakable equality constraints. Moving joints retain their actuator coordinates
inside free mount carriers, so releasing their mount frees the part. Collision
uses CAD convex pieces or catalog primitives, including after release.

Steering: when the cad stage solved the linkage (``kinematics.steering.table``:
kingpin angle per servo horn angle from the real tie-rod geometry), the knuckle
targets interpolate that table; otherwise the bicycle angle is
``ratio * theta_horn`` split by ideal Ackermann geometry.

All body frames are aligned with the robot frame (+x forward, +y left, +z up);
a body's origin is its joint origin (root: the project-defined robot design datum). Mass properties come from
``build/cad/cad_model.json`` when present (CAD mass, COM, inertia about the COM
in robot axes) and otherwise from catalog mass with solid box/cylinder inertia.

Wheels collide as MuJoCo *cylinders* (native plane-cylinder contact, which
returns up to four rim/edge contacts per wheel, so the tire contact patch has
width and resists yaw scrub). No capsule/ellipsoid approximation is used
because MuJoCo 3.x supports cylinder-plane and cylinder-box contact natively.

With ``routes`` (``build/wiring/routes.json``) every routed harness is added to
the same world as a MuJoCo elastic cable (``mujoco.elasticity.cable``),
anchored to the bodies that carry its connectors and ties, colliding with
massless part collision geoms (:mod:`r3_wiring.mujoco_cables`).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.graph import rpy_matrix
from r3_model.project import Project
from r3_model.units import to_si

from ..geometry import MassProperties, axis_rotation, combine, rotation_to_quat
from .scene_visual import add_cameras, add_scene
from .servo import ackermann_angles
from ..assembly import (AssemblyModel, TransmissionInfo, write_mjcf, mass_report, _add_cables, _behavior_params, _component_shape, _find_root, _fmt, _hex_rgba, _local_center, _si, cad_mass_properties, catalog_mass_properties)

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
    body: str = ""  # MuJoCo body carrying the tire: the wheel, or the axle the wheel is fixed to


# Compatibility name for the shared, locomotion-independent transmission.
DriveInfo = TransmissionInfo


@dataclass
class SteeringInfo:
    id: str
    actuator_component: str
    joints: list[str]  # [left, right] ordering by +y position
    ratio: float  # wheel steer angle per servo horn angle (used without a CAD steering table)
    wheelbase: float
    kingpin_track: float
    max_steer: float
    actuators: list[str]
    table_servo: list[float] | None = None  # servo horn angles (rad, ascending) of the CAD linkage solution
    table_angles: dict[str, list[float]] = field(default_factory=dict)  # joint -> kingpin angle (rad) per table row
    horn_joint: str | None = None  # servo output joint (the horn turns with the servo angle)
    horn_actuator: str | None = None

    @property
    def source(self) -> str:
        return "cad_linkage" if self.table_servo else "ackermann_ratio"

    def wheel_angles(self, horn_angle: float) -> tuple[float, ...]:
        """Kingpin target angle per joint (``joints`` order) for servo horn angle ``horn_angle`` (rad)."""
        if self.table_servo:
            # Linear interpolation of the solved linkage, held at the table ends (horn travel stops).
            return tuple(float(np.interp(horn_angle, self.table_servo, self.table_angles[j])) for j in self.joints)
        return ackermann_angles(self.ratio * horn_angle, self.wheelbase, self.kingpin_track, self.max_steer)


@dataclass
class VehicleModel(AssemblyModel):
    """Assembly physics plus metadata for declared tires and steering."""

    wheels: dict[str, WheelInfo] = field(default_factory=dict)
    steering: list[SteeringInfo] = field(default_factory=list)
    default_drag_area: float = 0.04  # vehicle adapter estimate


def _joint_children(project: Project) -> dict[str, Any]:
    children: dict[str, Any] = {joint.child: joint for joint in project.spec.mechanical.joints}
    children.update({rod.child: rod for rod in _linkage_rods(project, children)})
    return children


@dataclass
class _BallLink:
    """A tie rod as its own body: ball joint at the horn-side stud, closed to the
    knuckle-side stud by an equality connect (so the rod moves with the linkage)."""

    id: str
    child: str
    parent: str
    origin: list[float]
    closure: str  # component carrying the other ball stud
    closure_point: list[float]
    type: str = "ball"
    axis: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0])
    damping: float = 1e-4
    friction: float = 0.0
    limits: list[float] | None = None


def _linkage_rods(project: Project, children: dict[str, Any]) -> list[_BallLink]:
    """Rods joined by two ball joints whose horn side rides on the servo output joint."""
    balls = [i for i in project.spec.mechanical.interfaces if i.kind == "ball_joint"]
    servo_children = {j.child for j in project.spec.mechanical.joints if any(t.actuator == j.parent for t in project.spec.mechanical.transmissions
                                                                            if t.type == "ackermann_steering")}

    def on_horn(cid: str) -> bool:
        seen = set()
        while cid in project.spec.components and cid not in seen:
            if cid in servo_children:
                return True
            seen.add(cid)
            cid = project.spec.components[cid].mount
        return False

    rods = []
    for rod in sorted({b.parts[1] for b in balls}):
        ends = [b for b in balls if b.parts[1] == rod]
        if len(ends) != 2:
            continue
        horn = next((b for b in ends if on_horn(b.parts[0])), None)
        if horn is None:
            continue
        other = next(b for b in ends if b is not horn)
        rods.append(_BallLink(id=f"ball_{rod}", child=rod, parent=horn.parts[0], origin=list(horn.axis_point),
                              closure=other.parts[0], closure_point=list(other.axis_point)))
    return rods


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


def _mesh_name(part: str) -> str:
    return "mesh_" + part.replace(":", "__")


def _gearbox_for(project: Project, trans: Any, joints_by_id: dict[str, Any]) -> str | None:
    """The gearbox driving ``trans``: on the mount chain of a driven part (wheel -> axle -> gearbox ...),
    else a part of one of the transmission's ``path`` interfaces (e.g. the axle's drive flat in the gearbox output)."""
    spec, catalog = project.spec, project.catalog
    is_gearbox = lambda cid: cid in spec.components and catalog.component(spec.components[cid].type).kind == "gearbox"  # noqa: E731
    for joint_id in trans.joints:
        if joint_id not in joints_by_id:
            continue
        cid, seen = joints_by_id[joint_id].child, set()
        while cid in spec.components and cid not in seen:
            seen.add(cid)
            if is_gearbox(cid):
                return cid
            cid = spec.components[cid].mount
    interfaces = {i.id: i for i in spec.mechanical.interfaces}
    for link in trans.path:
        for part in interfaces[link].parts if link in interfaces else []:
            if is_gearbox(part):
                return part
    return None


def _steering_table(cad: dict[str, Any], trans_id: str, joints: list[str]) -> tuple[list[float] | None, dict[str, list[float]]]:
    """CAD-solved steering linkage rows for ``trans_id``: (servo angles, joint -> kingpin angles), or (None, {})."""
    steering = ((cad.get("kinematics") or {}).get("steering") or {}) if isinstance(cad, dict) else {}
    rows = steering.get("table") or []
    if steering.get("transmission", trans_id) != trans_id or len(rows) < 2:
        return None, {}
    if any(steering.get("sides", {}).get(j, {}).get("status", "ok") != "ok" for j in joints):
        return None, {}  # the linkage locks or fails inside the travel: no trustworthy table
    if any(row.get(j) is None for row in rows for j in joints):
        return None, {}
    rows = sorted(rows, key=lambda row: float(row["servo_rad"]))
    return [float(row["servo_rad"]) for row in rows], {j: [float(row[j]) for row in rows] for j in joints}


def build_vehicle_model(project: Project, initial_pose: tuple[float, float, float] = (0.0, 0.0, 0.0),
                        obstacles: list[dict[str, Any]] | None = None,
                        course: list[list[float]] | None = None, routes: dict[str, Any] | None = None) -> VehicleModel:
    """Build the MJCF for the spec. ``initial_pose`` is (x, y, yaw) of the robot
    origin in the world; ``course`` waypoints are drawn as visual markers;
    ``routes`` adds the routed harnesses as MuJoCo cables."""
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
    # CAD-only parts (fasteners, nuts) ride with the body of the component that owns them.
    cad_extras: dict[str, list[str]] = {body: [] for body in bodies}
    for name, part in cad_parts.items():
        owner = part.get("owner") if isinstance(part, dict) else None
        if name in spec.components or owner not in spec.components:
            continue
        if part.get("mass") and part.get("inertia") and part.get("com"):
            cad_extras[_owner_body(project, owner, joint_children, root)].append(name)

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
                                          "with catalog mass; every component retains its own body."))

    for members in cad_extras.values():
        for name in members:
            part_props[name] = cad_mass_properties(cad_parts[name])
    if any(cad_extras.values()):
        count = sum(len(m) for m in cad_extras.values())
        assumptions.append(("mechanical", f"{count} CAD fasteners/nuts are separate bodies, using "
                                          "with their Onshape mass properties."))
    body_props = {body: combine([part_props[c] for c in members + cad_extras[body]]) for body, members in bodies.items()}
    body_origin: dict[str, np.ndarray] = {root: np.zeros(3)}
    for child, joint in joint_children.items():
        body_origin[child] = np.asarray(joint.origin, dtype=float)
    total_mass = sum(p.mass for p in body_props.values())

    # --- wheels
    wheels: dict[str, WheelInfo] = {}
    env = spec.simulation.environment
    for cid, comp in spec.components.items():
        ctype = catalog.component(comp.type)
        if ctype.kind != "wheel":
            continue
        body = _owner_body(project, cid, joint_children, root)  # the wheel, or the axle it is fixed to
        if body not in joint_children or joint_children[body].type != "continuous":
            continue
        params = _behavior_params(project, cid)
        _, size, _ = _component_shape(project, cid)
        wheels[cid] = WheelInfo(
            component=cid, joint=joint_children[body].id, body=body,
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
            gearbox = _gearbox_for(project, trans, joints_by_id)
            gear_params = _behavior_params(project, gearbox) if gearbox else {}
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
            table_servo, table_angles = _steering_table(cad, trans.id, ordered)
            steering.append(SteeringInfo(
                id=trans.id, actuator_component=trans.actuator, joints=ordered, ratio=float(trans.ratio),
                wheelbase=_si(params.get("wheelbase"), 0.26), kingpin_track=track,
                max_steer=_si(params.get("max_steer"), float(max(abs(v) for v in limits))),
                actuators=[f"act_{j}" for j in ordered], table_servo=table_servo, table_angles=table_angles,
                horn_joint=next((j.id for j in spec.mechanical.joints if j.parent == trans.actuator), None)))
            if steering[-1].horn_joint:
                steering[-1].horn_actuator = f"act_{steering[-1].horn_joint}"
            if table_servo:
                assumptions.append(("mechanical", f"Steering {trans.id}: kingpin targets interpolate the CAD-solved linkage "
                                                  f"table ({len(table_servo)} rows over servo "
                                                  f"[{table_servo[0]:.3f}, {table_servo[-1]:.3f}] rad)."))
            else:
                assumptions.append(("mechanical", f"Steering {trans.id}: no CAD linkage table; bicycle angle = "
                                                  f"{float(trans.ratio):g} x horn angle with ideal Ackermann split."))
    for steer in steering:
        for joint_id in steer.joints:
            for wheel in wheels.values():
                # Walk the body chain from the wheel's spin joint toward the root (stub -> knuckle ...).
                body = _owner_body(project, joints_by_id[wheel.joint].parent, joint_children, root)
                while body != root:
                    if joint_children[body].id == joint_id:
                        wheel.steered_by = joint_id
                        break
                    body = _owner_body(project, joint_children[body].parent, joint_children, root)

    # --- XML
    mj = ET.Element("mujoco", model=spec.robot.name)
    ET.SubElement(mj, "compiler", angle="radian", autolimits="true", meshdir=str(mesh_dir))
    ET.SubElement(mj, "option", timestep=f"{spec.simulation.physics_timestep:.9g}", integrator="implicitfast", cone="pyramidal",
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
    for cid in [*spec.components, *(name for members in cad_extras.values() for name in members)]:
        mesh_rel = (cad_parts.get(cid) or {}).get("mesh")
        if mesh_rel and (mesh_dir / mesh_rel).exists():
            meshes_used[cid] = _mesh_name(cid)
            ET.SubElement(asset, "mesh", name=_mesh_name(cid), file=str(mesh_dir / mesh_rel))
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

    add_scene(project, mj, asset, world, course, initial_pose, assumptions)

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
            jtype = {"revolute": "hinge", "continuous": "hinge", "prismatic": "slide", "ball": "ball"}.get(joint.type)
            if jtype == "ball":
                ET.SubElement(el, "joint", name=joint.id, type="ball", damping=f"{joint.damping:.9g}")
            elif jtype:
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
                center = position + rotation @ (_local_center(shape, size) + np.asarray(ctype.mechanical.envelope_offset)) - origin
                if shape == "cylinder":
                    rot = rotation @ axis_rotation(axis)
                    ET.SubElement(el, "geom", name=f"vis_{cid}", type="cylinder", pos=_fmt(center), quat=_fmt(rotation_to_quat(rot)),
                                  size=f"{size[0] / 2:.9g} {size[1] / 2:.9g}", **common)
                else:
                    ET.SubElement(el, "geom", name=f"vis_{cid}", type="box", pos=_fmt(center), quat=_fmt(rotation_to_quat(rotation)),
                                  size=_fmt(np.asarray(size) / 2.0), **common)
            if cid == body and body != root:
                center_robot = position + rotation @ (_local_center(shape, size) + np.asarray(ctype.mechanical.envelope_offset))
                geometry[body] = {"shape": shape, "size": size, "axis_vector": (rotation @ axis_rotation(axis))[:, 2].tolist()
                                  if shape == "cylinder" else None, "center_offset": (center_robot - origin).tolist(),
                                  "rotation": rotation.tolist(), "keepout": float(ctype.mechanical.keepout_margin)}
        for name in cad_extras[body]:
            if name in meshes_used:
                ET.SubElement(el, "geom", name=f"vis_{name.replace(':', '__')}", type="mesh", mesh=meshes_used[name],
                              pos=_fmt(-origin), contype="0", conaffinity="0", group="1", mass="0", rgba="0.62 0.63 0.66 1")
        for wheel in [w for w in wheels.values() if w.body == body]:
            joint = joint_children[body]
            axis_vec = np.asarray(joint.axis, dtype=float)
            # Cylinder local z -> wheel spin axis.
            z = axis_vec / np.linalg.norm(axis_vec)
            ref = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
            x = np.cross(ref, z)
            x /= np.linalg.norm(x)
            rot = np.column_stack([x, np.cross(z, x), z])
            wheel_center = project.graph.component_pose(wheel.component)[0] - origin
            ET.SubElement(el, "geom", name=f"tire_{wheel.component}", type="cylinder", pos=_fmt(wheel_center), quat=_fmt(rotation_to_quat(rot)),
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
    add_cameras(project, elements, body_origin, lambda cid: _owner_body(project, cid, joint_children, root), assumptions)
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
        if steer.horn_actuator:
            # The horn (servo output) follows the simulated servo angle; tie rods close the linkage by equality constraints.
            ET.SubElement(actuator, "general", name=steer.horn_actuator, joint=steer.horn_joint, gainprm="4", biastype="affine",
                          biasprm="0 -4 -0.02")
    rods = [j for j in joint_children.values() if isinstance(j, _BallLink)]
    if rods:
        equality = ET.SubElement(mj, "equality")
        for rod in rods:
            anchor = np.asarray(rod.closure_point, dtype=float) - body_origin[rod.child]
            ET.SubElement(equality, "connect", name=f"close_{rod.child}", body1=rod.child,
                          body2=_owner_body(project, rod.closure, joint_children, root), anchor=_fmt(anchor), solref="0.004 1")
        assumptions.append(("mechanical", f"Steering linkage animated: horn on its servo-output joint, {len(rods)} tie rods on ball "
                                          "joints closed to the knuckle studs by equality constraints."))
    if drives:
        assumptions.append(("mechanical", "Open differential splits carrier torque equally (fixed tendon, coef 1/n); motor "
                                          "rotor inertia is reflected through ratio^2 onto the carrier; gear backlash is not modeled."))

    from ..joints import separate_parts

    connections, joint_bindings = separate_parts(project, mj, root, bodies, body_origin, body_props, cad_extras,
                                                 elements, joint_children, cad_parts, part_props, initial_pose,
                                                 contact_proxies={cid:f"tire_{cid}" for cid in wheels})
    diagnostics.extend(connections.diagnostics)
    total_mass = sum(p.mass for p in body_props.values())
    assumptions.append(("mechanical", "Each part is a separate MuJoCo body. Catalog or CAD convex-piece collisions, "
                        "breakable constraints from real connections; mounts alone provide no retention."))
    cable_plan = _add_cables(project, mj, routes, root, elements, body_origin, diagnostics, assumptions) if routes else None

    # Sleeping is safe for passive detached islands. Plugin-backed cable trees
    # remain awake: the cable plugin applies its own passive forces.
    import mujoco
    if hasattr(mujoco.mjtEnableBit, "mjENBL_SLEEP"):
        flags = mj.find("option/flag")
        flags.set("sleep", "enable")
        if cable_plan is not None:
            for el in mj.findall("worldbody/body"):
                if el.find(".//composite") is not None:
                    el.set("sleep", "never")
    sensor = ET.SubElement(mj, "sensor")
    if imu_site:
        ET.SubElement(sensor, "accelerometer", name="imu_acc", site=imu_site)
        ET.SubElement(sensor, "gyro", name="imu_gyro", site=imu_site)
    ET.SubElement(sensor, "framequat", name="chassis_quat", objtype="site", objname="chassis_ref")

    xml = ET.tostring(mj, encoding="unicode")
    if cable_plan is not None:
        total_mass += cable_plan.total_mass  # wire mass rides on the vehicle through its anchors
    model = VehicleModel(xml=xml, root=root, bodies=bodies, body_origin=body_origin, mass_properties=body_props,
                        total_mass=total_mass, wheels=wheels, drives=drives, steering=steering, imu_site=imu_site,
                        imu_component=imu_component, diagnostics=diagnostics, assumptions=assumptions,
                        mass_source=mass_source, geometry=geometry, cables=cable_plan, cad_extras=cad_extras,
                        connections=connections, joint_bindings=joint_bindings,
                        moving_joints={k:v for k,v in joint_children.items() if v.type != "fixed"})


    from ..load_cases import configure_model
    from r3_model.spec import Scenario
    configure_model(model, Scenario(id="assembly_initial", duration=0), project)
    return model


__all__ = ["VehicleModel", "WheelInfo", "DriveInfo", "SteeringInfo", "build_vehicle_model", "write_mjcf",
           "catalog_mass_properties", "cad_mass_properties", "mass_report"]
