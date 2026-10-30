"""MuJoCo assembly bodies and breakable equality constraints.

MuJoCo owns every contact and integration step. This module only emits MJCF,
reads J^T lambda, and deactivates exceeded constraints. A free mount carrier
around a moving joint preserves its actuator coordinate when the mount breaks.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from r3_model.connections import Connection, ConnectionModel, compile_connections
from r3_model.diagnostics import Diagnostic
from r3_sim.geometry import MassProperties, cylinder_inertia, combine, rotation_to_quat


def _fmt(x: Any) -> str:
    return " ".join(f"{v:.10g}" for v in np.ravel(x))


def separate_parts(
    project: Any,
    mj: ET.Element,
    root: str,
    bodies: dict[str, list[str]],
    origins: dict[str, np.ndarray],
    props: dict[str, MassProperties],
    extras: dict[str, list[str]],
    elements: dict[str, ET.Element],
    joint_children: dict[str, Any],
    cad_parts: dict[str, Any],
    part_props: dict[str, MassProperties],
    initial_pose: tuple[float, float, float],
    contact_proxies: dict[str, str] | None = None,
) -> tuple[ConnectionModel, list[dict[str, Any]]]:
    """Split the legacy visual tree into individual massive parts, before cables.

    Collision shapes use the existing wiring convex decomposition (cached),
    with catalog primitives when no CAD mesh is available. Raw triangle meshes
    are never collision geoms.
    """
    from r3_model.graph import rpy_matrix
    from r3_wiring.mujoco_cables import part_geoms
    from r3_wiring.obstacles import build_obstacles

    contact_proxies = contact_proxies or {}
    custom = mj.find("custom")
    if custom is None:
        custom = ET.SubElement(mj, "custom")
    ET.SubElement(custom, "text", name="r3_root", data=root)
    cm = compile_connections(project)
    world, asset = mj.find("worldbody"), mj.find("asset")
    old_origins = {k: v.copy() for k, v in origins.items()}
    parent_of = {child: parent for parent in mj.iter() for child in parent}
    nominal = {}
    for body in elements:
        nominal[body] = old_origins[body]
    # Save the visual/collision geoms on their real component before dismantling.
    geoms: dict[str, list[ET.Element]] = {cid: [] for cid in project.spec.components}
    for body, el in list(elements.items()):
        for geom in list(el.findall("geom")):
            name = geom.get("name", "")
            cid = next(
                (c for c in project.spec.components if name in (f"vis_{c}", f"tire_{c}")),
                None,
            )
            if cid:
                position = np.fromstring(geom.get("pos", "0 0 0"), sep=" ") + old_origins[body]
                geom.set("pos", _fmt(position))  # temporarily robot-frame
                geoms[cid].append(geom)
            el.remove(geom)
    all_parts = dict(part_props)
    # Fasteners absent from CAD still have mass and a primitive shank/head.
    for f in project.spec.mechanical.fasteners:
        ft = project.catalog.fastener(f.type)
        for name in (f.part, f.nut_part):
            if name is None or name in all_parts:
                continue
            actual_type = project.catalog.fastener(f.nut) if name == f.nut_part else ft
            axis = np.asarray(f.axis, float)
            axis /= np.linalg.norm(axis)
            quat = np.zeros(4)
            mujoco.mju_quatZ2Vec(quat, axis)
            rotation_matrix = np.zeros(9)
            mujoco.mju_quat2Mat(rotation_matrix, quat)
            rotation_matrix = rotation_matrix.reshape(3, 3)
            seat = np.asarray(f.position, float)
            if name == f.nut_part:
                seat = seat + axis * float(f.grip or 0)
            length = float(actual_type.height or f.length or actual_type.thread_length or actual_type.d)
            width = float(actual_type.across_flats or actual_type.d)
            is_nut = actual_type.kind == "nut"
            volume = (
                (math.sqrt(3) / 2 * width**2 - math.pi * (actual_type.minor_diameter / 2) ** 2) * length
                if is_nut
                else math.pi * (actual_type.d / 2) ** 2 * length
            )
            mass = float(actual_type.density) * volume
            center = seat + axis * length / 2
            pieces = [
                MassProperties(
                    mass,
                    center,
                    rotation_matrix @ cylinder_inertia(mass, width, length) @ rotation_matrix.T,
                    "catalog",
                )
            ]
            head_h = float(actual_type.head_height or actual_type.collar_height or 0)
            head_d = float(actual_type.head_diameter or actual_type.collar_diameter or 0)
            if head_h and head_d and not is_nut:
                head_mass = float(actual_type.density) * math.pi * (head_d / 2) ** 2 * head_h
                pieces.append(
                    MassProperties(
                        head_mass,
                        seat - axis * head_h / 2,
                        rotation_matrix @ cylinder_inertia(head_mass, head_d, head_h) @ rotation_matrix.T,
                        "catalog",
                    )
                )
            all_parts[name] = combine(pieces)
            mass = all_parts[name].mass
            cm.diagnostics.append(
                Diagnostic.warning(
                    "joint_hardware_geometry_estimated",
                    f"{name}: cylindrical envelope and mass estimated from catalog dimensions (CAD missing)",
                    subjects=[name],
                    data={"mass_kg": mass},
                )
            )
    # Remove all old bodies (including nested ones); their children are rebuilt.
    for body, el in list(elements.items()):
        if el in list(parent_of[el]):
            parent_of[el].remove(el)
    elements.clear()
    bodies.clear()
    origins.clear()
    props.clear()
    extras.clear()
    rotation = rpy_matrix([0, 0, initial_pose[2]])
    shift = np.array([initial_pose[0], initial_pose[1], 0.0005])
    carriers = {}
    fraction = float(project.catalog.joint_defaults["carrier_mass_fraction"].value)
    if not 0 < fraction < 1:
        raise ValueError("carrier_mass_fraction must lie strictly between zero and one")
    for name, p in all_parts.items():
        origin = (
            np.zeros(3)
            if name == root
            else (
                np.asarray(joint_children[name].origin)
                if name in joint_children
                else np.asarray(project.graph.component_pose(name)[0])
                if name in project.spec.components
                else p.com.copy()
            )
        )
        origins[name] = origin
        props[name] = p
        bodies[name] = [name] if name in project.spec.components else []
        extras[name] = [] if name in project.spec.components else [name]
        native_props = p
        if name in joint_children and joint_children[name].type != "fixed":
            carrier = ET.SubElement(
                world,
                "body",
                name=f"mount:{name}",
                pos=_fmt(rotation @ origin + shift),
                quat=_fmt(rotation_to_quat(rotation)),
            )
            ET.SubElement(carrier, "freejoint", name=f"mount:{name}_free")
            # Allocate a small, isotropic inertia to the redundant mount frame.
            # Aggregate mass, COM and inertia are exact at every pose. The only
            # approximation is O(fraction) in relative-joint inertia and its
            # coupling while the real hinge moves relative to the frame.
            r = p.com - origin
            parallel = lambda v: (v @ v) * np.eye(3) - np.outer(v, v)
            at_joint = p.inertia + p.mass * parallel(r)
            minimum = float(np.min(np.linalg.eigvalsh(p.inertia)))
            at_joint_minimum = float(np.min(np.linalg.eigvalsh(at_joint)))
            # Bound both subtracted inertia terms for arbitrarily slender or
            # offset parts. Their sum stays below the smallest part eigenvalue.
            actual_fraction = min(fraction, minimum / (4 * (at_joint_minimum + p.mass * (r @ r))))
            if actual_fraction < fraction:
                cm.estimated[f"solver.carrier_mass_fraction:{name}"] = dict(
                    value=actual_fraction, status="estimated",
                    source="Numerical carrier fraction reduced to preserve positive child inertia; aggregate rigid mass properties remain exact")
            carrier_mass = p.mass * actual_fraction
            isotropic = at_joint_minimum * actual_fraction
            child_mass = p.mass - carrier_mass
            child_com = r / (1 - actual_fraction)
            child_inertia = at_joint - isotropic * np.eye(3) - child_mass * parallel(child_com)
            native_props = MassProperties(child_mass, origin + child_com, child_inertia, p.source)
            ET.SubElement(carrier, "inertial", pos="0 0 0", mass=str(carrier_mass), diaginertia=_fmt([isotropic] * 3))
            el = ET.SubElement(carrier, "body", name=name, pos="0 0 0")
            j = joint_children[name]
            attrs = dict(
                name=j.id,
                type={
                    "continuous": "hinge",
                    "revolute": "hinge",
                    "prismatic": "slide",
                    "ball": "ball",
                }[j.type],
                damping=str(j.damping),
            )
            if j.type != "ball":
                attrs["axis"] = _fmt(j.axis)
            if j.type != "continuous" and j.limits:
                attrs["range"] = _fmt(j.limits)
            if j.friction:
                attrs["frictionloss"] = str(j.friction)
            ET.SubElement(el, "joint", **attrs)
            carriers[name] = (f"mount:{name}", j.parent, origin)
        else:
            el = ET.SubElement(
                world,
                "body",
                name=name,
                pos=_fmt(rotation @ origin + shift),
                quat=_fmt(rotation_to_quat(rotation)),
            )
            ET.SubElement(el, "freejoint", name=f"{name}_free")
        elements[name] = el
        inertia = native_props.inertia
        ET.SubElement(
            el,
            "inertial",
            pos=_fmt(native_props.com - origin),
            mass=str(native_props.mass),
            fullinertia=_fmt(
                [
                    inertia[0, 0],
                    inertia[1, 1],
                    inertia[2, 2],
                    inertia[0, 1],
                    inertia[0, 2],
                    inertia[1, 2],
                ]
            ),
        )
        for geom in geoms.get(name, []):
            geom.set("pos", _fmt(np.fromstring(geom.get("pos", "0 0 0"), sep=" ") - origin))
            el.append(geom)
        if name not in project.spec.components:
            part = cad_parts.get(name, {})
            mesh = "mesh_" + name.replace(":", "__")
            if asset.find(f"mesh[@name='{mesh}']") is not None:
                ET.SubElement(
                    el,
                    "geom",
                    name=f"vis_{name}",
                    type="mesh",
                    mesh=mesh,
                    pos=_fmt(-origin),
                    mass="0",
                    contype="0",
                    conaffinity="0",
                    group="1",
                )
            else:
                ET.SubElement(
                    el,
                    "geom",
                    name=f"vis_{name}",
                    type="sphere",
                    size=".0015",
                    mass="0",
                    contype="0",
                    conaffinity="0",
                    group="1",
                )
    # Move reference/sensor/camera nodes from the old tree to their actual hosts.
    for old_el in parent_of:
        if old_el.tag != "body":
            continue
        old_name = old_el.get("name")
        if old_name not in old_origins:
            continue
        for node in list(old_el):
            if node.tag not in ("site", "camera"):
                continue
            host = next(
                (
                    c
                    for c in project.spec.components
                    if node.get("name") == "imu"
                    and any(
                        s.protocol == "mpu6050"
                        for s in project.catalog.component(project.spec.components[c].type).software
                    )
                ),
                old_name,
            )
            position = np.fromstring(node.get("pos", "0 0 0"), sep=" ") + old_origins[old_name] - origins[host]
            node.set("pos", _fmt(position))
            elements[host].append(node)
    # Reuse CAD convex pieces and box/cylinder primitives already used for cables.
    hardware = {
        name: (f, project.catalog.fastener(f.type if name == f.part else f.nut))
        for f in project.spec.mechanical.fasteners
        for name in (f.part, f.nut_part)
        if name
    }
    collision = part_geoms(project, build_obstacles(project)[0])
    common = dict(
        mass="0",
        contype="1",
        conaffinity="1",
        group="3",
        rgba="0 0 0 0",
        condim="3",
        friction=f"{project.catalog.joint_defaults['friction'].value} .005 .0001",
        solref="0.002 1",
    )
    for part in collision:
        name = part.component
        if name not in elements or name in hardware:
            continue
        el = elements[name]
        origin = origins[name]
        uses_proxy = name in contact_proxies
        common["contype"] = "16" if uses_proxy else "1"
        if part.kind == "mesh":
            for k, vertices in enumerate(part.pieces):
                mesh = f"joint_col_{name}_{k}"
                ET.SubElement(asset, "mesh", name=mesh, vertex=_fmt(vertices - origin))
                ET.SubElement(el, "geom", name=mesh, type="mesh", mesh=mesh, **common)
            if part.hull_only:
                cm.diagnostics.append(
                    Diagnostic.warning(
                        "joint_collision_estimated",
                        f"{name}: {part.hull_only} conservative convex hull pieces",
                        subjects=[name],
                        data={"hull_pieces": part.hull_only},
                    )
                )
        else:
            ET.SubElement(
                el,
                "geom",
                name=f"joint_col_{name}",
                type=part.kind,
                pos=_fmt(part.center - origin),
                quat=_fmt(rotation_to_quat(part.rotation)),
                size=_fmt(part.size),
                **common,
            )
    common["contype"] = "1"

    def hex_ring(
        name: str,
        el: Any,
        origin: np.ndarray,
        seat: np.ndarray,
        axis: np.ndarray,
        across_flats: float,
        height: float,
        bore: float,
    ) -> None:
        helper = np.eye(3)[int(np.argmin(np.abs(axis)))]
        u = np.cross(axis, helper)
        u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        count = 6
        for k in range(count):
            angles = 2 * math.pi * np.asarray([k, k + 1]) / count
            normals = math.pi / 6 + np.arange(6) * math.pi / 3
            outer = across_flats / (2 * np.max(np.cos(angles[:, None] - normals), axis=1))
            inner = bore / 2 / math.cos(math.pi / count)
            if not bore:
                inner = 0
            planar = [
                r * (math.cos(angle) * u + math.sin(angle) * v)
                for angle, r in [(angles[0], inner), (angles[0], outer[0]), (angles[1], outer[1]), (angles[1], inner)]
            ]
            vertices = np.asarray([seat + point + axis * z - origin for z in (0, height) for point in planar])
            mesh = f"joint_ring_{name}_{len(el)}_{k}"
            ET.SubElement(asset, "mesh", name=mesh, vertex=_fmt(vertices))
            ET.SubElement(el, "geom", name=mesh, type="mesh", mesh=mesh, **common)

    for name in (all_parts.keys() - project.spec.components.keys()) | hardware.keys():
        el = elements[name]
        origin = origins[name]
        if name in hardware:
            f, ft = hardware[name]
            axis = np.asarray(f.axis, float)
            axis /= np.linalg.norm(axis)
            quat = np.zeros(4)
            mujoco.mju_quatZ2Vec(quat, axis)
            seat = np.asarray(f.position, float)
            is_nut = name == f.nut_part or ft.kind == "nut"
            if name == f.nut_part:
                seat += axis * float(f.grip or 0)
            if is_nut:
                hex_ring(
                    name, el, origin, seat, axis, float(ft.across_flats or ft.d), float(ft.height or ft.d), float(ft.d)
                )
            else:
                length = float(f.length or ft.thread_length or ft.height or ft.d)
                ET.SubElement(
                    el,
                    "geom",
                    name=f"joint_col_{name}",
                    type="cylinder",
                    pos=_fmt(seat + axis * length / 2 - origin),
                    quat=_fmt(quat),
                    size=_fmt([ft.minor_diameter / 2, length / 2]),
                    **common,
                )
                if ft.kind == "standoff_mf":
                    h = float(ft.height)
                    depth = float(ft.female_depth or h)
                    hex_ring(name, el, origin, seat - axis * h, axis, float(ft.across_flats), depth, float(ft.d))
                    if h > depth:
                        hex_ring(
                            name, el, origin, seat - axis * (h - depth), axis, float(ft.across_flats), h - depth, 0
                        )
                if ft.kind == "ball_stud" and ft.ball_diameter:
                    neck = float(ft.neck_length or 0)
                    collar = float(ft.collar_height or 0)
                    radius = float(ft.ball_diameter) / 2
                    if neck:
                        ET.SubElement(
                            el,
                            "geom",
                            name=f"joint_neck_{name}",
                            type="cylinder",
                            pos=_fmt(seat - axis * (collar + neck / 2) - origin),
                            quat=_fmt(quat),
                            size=_fmt([float(ft.neck_diameter or ft.d) / 2, neck / 2]),
                            **common,
                        )
                    ET.SubElement(
                        el,
                        "geom",
                        name=f"joint_ball_{name}",
                        type="sphere",
                        pos=_fmt(seat - axis * (collar + neck + radius) - origin),
                        size=str(radius),
                        **common,
                    )
                head_h = float(ft.head_height or ft.collar_height or 0)
                head_d = float(ft.head_diameter or ft.collar_diameter or 0)
                if head_h and head_d:
                    ET.SubElement(
                        el,
                        "geom",
                        name=f"joint_head_{name}",
                        type="cylinder",
                        pos=_fmt(seat - axis * head_h / 2 - origin),
                        quat=_fmt(quat),
                        size=_fmt([head_d / 2, head_h / 2]),
                        **common,
                    )
        else:
            part = cad_parts.get(name, {})
            half = (
                (np.asarray(part["bbox_max"]) - np.asarray(part["bbox_min"])) / 2
                if part.get("bbox_max")
                else np.array([0.0015] * 3)
            )
            ET.SubElement(
                el,
                "geom",
                name=f"joint_col_{name}",
                type="box",
                pos=_fmt((np.asarray(part["bbox_min"])+np.asarray(part["bbox_max"]))/2-origin if part.get("bbox_max") else all_parts[name].com-origin),
                size=_fmt(np.maximum(half, 1e-5)),
                **common,
            )
            cm.diagnostics.append(
                Diagnostic.warning(
                    "joint_collision_estimated",
                    f"{name}: CAD bounding-box collision estimate",
                    subjects=[name],
                    data={"estimated": 1},
                )
            )
    cm.estimated["collision.friction"] = project.catalog.joint_defaults["friction"].model_dump()
    cm.diagnostics.append(
        Diagnostic.warning(
            "joint_collision_estimated",
            "Fastener collision proxies use ISO thread-core cylinders and six-wedge hollow nut/standoff prisms (circumscribed polygonal bore); helical flanks are represented by the breakable connection, not penetrating collision solids.",
            subjects=sorted(hardware),
            data={"hardware_parts": len(hardware)},
        )
    )
    # Optional contact proxies replace environment contact for declared parts;
    # their actual convex geometry still handles all internal contacts.
    for geom in mj.iter("geom"):
        if geom.get("name", "") in contact_proxies.values():
            geom.set("contype", "2")
            geom.set("conaffinity", "0")
        if geom.get("name") == "ground" or geom.get("name", "").startswith("obstacle_"):
            geom.set("conaffinity", "3")
    equality = mj.find("equality")
    if equality is None:
        equality = ET.SubElement(mj, "equality")
    # Old linkage closures are emitted on their actual moving bodies.
    for eq in list(equality):
        equality.remove(eq)
    for child, (carrier, parent, origin) in carriers.items():
        joint = joint_children[child]
        if joint.type == "ball":
            ET.SubElement(
                equality,
                "weld",
                name=f"released:{child}",
                body1=child,
                body2=carrier,
                active="false",
                solref="0.002 1",
                solimp="0.9999 0.9999 0.001",
            )
        else:
            ET.SubElement(
                equality,
                "joint",
                name=f"released:{child}",
                joint1=joint.id,
                polycoef="0 0 0 0 0",
                active="false",
                solref="0.002 1",
                solimp="0.9999 0.9999 0.001",
            )
    if carriers:
        coefficient = project.catalog.joint_defaults["carrier_mass_fraction"].model_dump()
        cm.estimated["solver.carrier_mass_fraction"] = coefficient
        cm.diagnostics.append(
            Diagnostic.warning(
                "joint_inertia_estimated",
                coefficient["source"],
                subjects=sorted(carriers),
                data={"carrier_mass_fraction": fraction, "frames": len(carriers)},
            )
        )
    bindings = []
    for index, c in enumerate(cm.connections):
        if c.kind == "resting" or c.a not in elements or c.b not in elements:
            continue
        a, b = c.a, c.b
        # A hinge's structural mount transmits load without welding its spin DOF.
        interface = next((i for i in project.spec.mechanical.interfaces if i.id == c.id), None)
        rotating = c.kind == "revolute_fit" or (interface is not None and interface.params.get("rotating_bore"))
        # Redirect the child of THIS moving joint only. A parent link may
        # itself rotate at another joint (arms, legs, gimbals); replacing it
        # by its own carrier would attach the child to the wrong link.
        if a in carriers and carriers[a][1] == c.b:
            a = carriers[a][0]
        elif b in carriers and carriers[b][1] == c.a:
            b = carriers[b][0]
        elif rotating:
            moving = next(
                (
                    name
                    for name in (c.a, c.b)
                    if name in carriers and getattr(joint_children[name], "interface", None) == c.id
                ),
                None,
            )
            if moving is None:
                moving = c.a if c.a in carriers else c.b if c.b in carriers else None
            if moving == c.a:
                a = carriers[moving][0]
            elif moving == c.b:
                b = carriers[moving][0]
        if a == b:
            continue
        attrs = dict(
            name=f"joint:{index}",
            body1=a,
            body2=b,
            solref="0.002 1",
            solimp="0.9999 0.9999 0.001",
            active="true" if min(c.axial, c.shear) > 0 else "false",
        )
        # Spherical sockets hold position and permit free rotation.
        if c.kind == "ball_joint" and not a.startswith("mount:") and not b.startswith("mount:"):
            ET.SubElement(
                equality,
                "connect",
                anchor=_fmt(np.asarray(c.point) - origins[c.a]),
                **attrs,
            )
        else:
            ET.SubElement(equality, "weld", torquescale="1", **attrs)
        bindings.append(dict(index=index, equality=attrs["name"], body1=a, body2=b))
    # A declared moving mount without any real capacity still gets an inactive
    # equality, so it can detach and be diagnosed instead of staying kinematic.
    bound = {v for binding in bindings for v in (binding["body1"], binding["body2"])}
    for child, (carrier, parent, origin) in carriers.items():
        if carrier not in bound:
            c = Connection(
                f"mount:{child}",
                parent,
                child,
                "resting",
                [0, 0, 1],
                origin.tolist(),
                0,
                0,
                0,
                0,
                cause="no physical mount connection",
            )
            cm.connections.append(c)
    # Mating surfaces (including overlapping nominal threads and coarse hardware
    # envelopes) are represented by their connection while retained. Restore
    # their contacts as soon as every associated retention constraint releases.
    contact = mj.find("contact")
    if contact is None:
        contact = ET.SubElement(mj, "contact")
    groups = {}
    for binding in bindings:
        c = cm.connections[binding["index"]]
        groups.setdefault(tuple(sorted((c.a, c.b))), []).append([binding["equality"]])
    fastener_edges = {}
    for binding in bindings:
        c = cm.connections[binding["index"]]
        if c.kind == "screw":
            fastener_edges.setdefault(c.a, []).append((c.b, binding["equality"]))
    from itertools import combinations

    for hub, edges in fastener_edges.items():
        for (a, ea), (b, eb) in combinations(edges, 2):
            if a != b:
                groups.setdefault(tuple(sorted((a, b))), []).append([ea, eb])
    for index, ((a, b), eqs) in enumerate(groups.items()):
        ET.SubElement(contact, "exclude", name=f"joint_exclude:{index}", body1=a, body2=b)
    for binding in bindings:
        binding["contact_groups"] = [
            {"name": f"joint_exclude:{index}", "paths": eqs} for index, (_, eqs) in enumerate(groups.items())
        ]
        break
    # Constraint islands and sleeping are MuJoCo optimisations; no check is skipped.
    option = mj.find("option")
    option.set("solver", "Newton")
    option.set("jacobian", "sparse")
    flag = option.find("flag")
    if flag is None:
        flag = ET.SubElement(option, "flag")
    flag.set("filterparent", "disable")
    flag.set("island", "enable")
    # Exact constraint inertia is essential for a free mount around a hinge:
    # the carrier is light only about the hinge's free axis. The default
    # body-average diagonal treats its bending directions as equally light.
    if hasattr(mujoco.mjtEnableBit, "mjENBL_DIAGEXACT"):
        flag.set("diagexact", "enable")
    return cm, bindings


@dataclass
class JointState:
    peak_utilization: float = 0.0
    preload_fraction: float = 1.0
    failed: bool = False
    peak_axial_n: float = 0.0
    peak_shear_n: float = 0.0
    peak_torque_nm: float = 0.0
    peak_bending_nm: float = 0.0


def loosen_preload(
    fraction: float,
    shear: float,
    slip_capacity: float,
    slip_distance: float,
    decay_distance: float,
    locked: bool,
) -> float:
    """Junker transverse-slip criterion (SAE 690055, 1969).

    Junker identifies the mechanism, not a universal rate. Exponential decay
    versus accumulated transverse slip is an explicitly estimated constitutive
    capacity rule; decay_distance requires calibration for the actual joint.
    """
    if locked or shear <= slip_capacity or decay_distance <= 0 or slip_distance <= 0:
        return fraction
    return fraction * math.exp(-slip_distance / decay_distance)


class JointMonitor:
    def __init__(
        self,
        model: Any,
        data: Any,
        connections: ConnectionModel,
        bindings: list[dict[str, Any]],
        root: str,
    ) -> None:
        self.model, self.data, self.connections = model, data, connections
        self.states = {c.id: JointState() for c in connections.connections}
        self.events: list[dict[str, Any]] = []
        self.diagnostics: list[Diagnostic] = []
        self.bindings = []
        self._groups = {}
        for binding in bindings:
            c = connections.connections[binding["index"]]
            if c.group:
                self._groups.setdefault(c.group, []).append((c, model.equality(binding["equality"]).id))
        self.contact_groups = []
        for group in bindings[0].get("contact_groups", []) if bindings else []:
            exclude = model.exclude(group["name"]).id
            self.contact_groups.append(
                (
                    int(model.exclude_signature[exclude]),
                    [[model.equality(n).id for n in path] for path in group["paths"]],
                )
            )
        self.other_excludes = [
            int(model.exclude_signature[i])
            for i in range(model.nexclude)
            if not model.exclude(i).name.startswith("joint_exclude:")
        ]
        self.root = root
        mujoco.mj_forward(model, data)
        for binding in bindings:
            c = connections.connections[binding["index"]]
            eq = model.equality(binding["equality"]).id
            a, b = (model.body(binding[key]).id for key in ("body1", "body2"))
            # Jacobian rows into body1's free DOFs (carrier for a moving body).
            free = a
            while free > 0 and (
                model.body_jntnum[free] == 0 or model.jnt_type[model.body_jntadr[free]] != mujoco.mjtJoint.mjJNT_FREE
            ):
                free = model.body_parentid[free]
            dof = int(model.jnt_dofadr[model.body_jntadr[free]])
            rel = data.xmat[b].reshape(3, 3).T @ (data.xpos[a] - data.xpos[b])
            rel_rot = data.xmat[b].reshape(3, 3).T @ data.xmat[a].reshape(3, 3)
            self.bindings.append((c, eq, a, b, dof, rel, rel_rot))
            if not data.eq_active[eq]:
                self.fail(c, eq, 0, "axial", 0, "zero capacity: " + c.cause)
        self.detached = set()
        self._row_cache_key = None
        self._row_cache = {}
        self._last_positions = {
            c.id: data.xmat[b].reshape(3, 3).T @ (data.xpos[a] - data.xpos[b]) for c, _, a, b, *_ in self.bindings
        }

    def reset_reference(self) -> None:
        d = self.data
        self.bindings = [
            (
                c,
                eq,
                a,
                b,
                dof,
                d.xmat[b].reshape(3, 3).T @ (d.xpos[a] - d.xpos[b]),
                d.xmat[b].reshape(3, 3).T @ d.xmat[a].reshape(3, 3),
            )
            for c, eq, a, b, dof, *_ in self.bindings
        ]
        self._last_positions = {
            c.id: d.xmat[b].reshape(3, 3).T @ (d.xpos[a] - d.xpos[b]) for c, _, a, b, *_ in self.bindings
        }

    def fail(
        self,
        c: Connection,
        eq: int,
        load: float,
        mode: str,
        capacity: float,
        cause: str,
        propagate: bool = True,
    ) -> None:
        state = self.states[c.id]
        if state.failed:
            return
        state.failed = True
        self.data.eq_active[eq] = False
        event = dict(
            t=float(self.data.time),
            event="joint_failed",
            joint=c.id,
            parts=[c.a, c.b],
            mode=mode,
            load=load,
            capacity=capacity,
            cause=cause,
            message=f"{c.id}: needs {load:.6g} {'N m' if mode in ('torsion', 'bending') else 'N'}, holds {capacity:.6g}: {cause}",
        )
        self.events.append(event)
        self._refresh_contacts()
        if c.group and propagate:
            for other, other_eq in self._groups.get(c.group, []):
                self.states[other.id].preload_fraction = 0.0
                if other.id != c.id:
                    self.fail(
                        other,
                        other_eq,
                        self.states[other.id].peak_axial_n,
                        "axial",
                        0.0,
                        f"clamp preload lost after {c.id}",
                        propagate=False,
                    )
        self.diagnostics.append(
            Diagnostic.error(
                "joint_failed",
                event["message"],
                subjects=[c.id, c.a, c.b],
                data={"t": event["t"], "load": load, "capacity": capacity},
            )
        )

    def _refresh_contacts(self) -> None:
        if not self.contact_groups:
            return
        signatures = self.other_excludes + [
            signature if any(all(self.data.eq_active[e] for e in path) for path in eqs) else 0
            for signature, eqs in self.contact_groups
        ]
        self.model.exclude_signature[:] = sorted(signatures)

    def step(self) -> None:
        """Project equality forces in one batch; no contact/constraint solve here."""
        m, d = self.model, self.data
        if not self.bindings:
            return
        if not hasattr(self, "_arrays"):
            self._arrays = True
            self._connections = [v[0] for v in self.bindings]
            group_codes = {}
            self._group_codes = np.asarray(
                [group_codes.setdefault(c.group or c.id, len(group_codes)) for c in self._connections]
            )
            self._n_groups = len(group_codes)
            self._eq = np.asarray([v[1] for v in self.bindings])
            self._a = np.asarray([v[2] for v in self.bindings])
            self._b = np.asarray([v[3] for v in self.bindings])
            self._dof = np.asarray([v[4] for v in self.bindings])
            self._free = m.dof_bodyid[self._dof]
            self._axis = np.asarray([c.axis for c in self._connections])
            self._point = np.asarray(
                [np.asarray(c.point) - self._origin(a) for c, a in zip(self._connections, self._a)]
            )
            self._caps = np.asarray([[c.axial, c.shear, c.torque, c.bending] for c in self._connections])
            self._slip = np.asarray([c.slip for c in self._connections])
            self._decay = np.asarray([c.loosening_distance for c in self._connections])
            self._locked = np.asarray([c.locked for c in self._connections])
            self._ball = np.asarray([c.kind == "ball_joint" for c in self._connections])
            self._bearing = np.asarray([c.kind == "revolute_fit" for c in self._connections])
            self._last_relative = np.asarray([self._last_positions[c.id] for c in self._connections])
        eqrows = np.flatnonzero(d.efc_type[: d.nefc] == mujoco.mjtConstraint.mjCNSTR_EQUALITY)
        key = (len(eqrows), d.efc_id[eqrows].tobytes())
        if key != self._row_cache_key:
            self._row_cache_key = key
            rows_by_id = {}
            for row in eqrows:
                rows_by_id.setdefault(int(d.efc_id[row]), []).append(row)
            indices = []
            columns = []
            row_ids = []
            sparse = mujoco.mj_isSparse(m)
            for k, (_, eq, _, _, dof, *_) in enumerate(self.bindings):
                for row in rows_by_id.get(eq, []):
                    if sparse:
                        start = int(d.efc_J_rowadr[row])
                        n = int(d.efc_J_rownnz[row])
                        cols = d.efc_J_colind[start : start + n]
                        select = np.flatnonzero((cols >= dof) & (cols < dof + 6))
                        indices.extend((start + select).tolist())
                        columns.extend((k * 6 + cols[select] - dof).tolist())
                        row_ids.extend([row] * len(select))
                    else:
                        indices.extend(range(row * m.nv + dof, row * m.nv + dof + 6))
                        columns.extend(range(k * 6, k * 6 + 6))
                        row_ids.extend([row] * 6)
            self._projection = (
                np.asarray(indices, int),
                np.asarray(columns, int),
                np.asarray(row_ids, int),
            )
        indices, columns, rows = self._projection
        q = np.bincount(
            columns,
            weights=d.efc_J[indices] * d.efc_force[rows],
            minlength=len(self.bindings) * 6,
        ).reshape(-1, 6)
        rotations = d.xmat[self._a].reshape(-1, 3, 3)
        parent_rotations = d.xmat[self._b].reshape(-1, 3, 3)
        axes = np.einsum("nij,nj->ni", rotations, self._axis)
        force = q[:, :3]
        moment = np.einsum("nij,nj->ni", d.xmat[self._free].reshape(-1, 3, 3), q[:, 3:])
        points = d.xpos[self._a] + np.einsum("nij,nj->ni", rotations, self._point)
        moment -= np.cross(points - d.xpos[self._free], force)
        axial = np.sum(force * axes, axis=1)
        torsion = np.sum(moment * axes, axis=1)
        shear = np.sqrt(np.maximum(0, np.sum(force * force, axis=1) - axial**2))
        bending = np.sqrt(np.maximum(0, np.sum(moment * moment, axis=1) - torsion**2))
        loads = np.column_stack([np.abs(axial), shear, np.abs(torsion), bending])
        loads[self._ball, 2:] = 0
        loads[self._bearing, 2] = 0
        relative = np.einsum("nji,nj->ni", parent_rotations, d.xpos[self._a] - d.xpos[self._b])
        delta = relative - self._last_relative
        local_axes = np.einsum("nji,nj->ni", parent_rotations, axes)
        slip = np.linalg.norm(delta - np.sum(delta * local_axes, axis=1)[:, None] * local_axes, axis=1)
        self._last_relative = relative
        fractions = np.asarray([self.states[c.id].preload_fraction for c in self._connections])
        slipping = (
            (shear > self._slip * fractions)
            & (~self._locked)
            & (self._decay > 0)
            & (self._slip > 0)
            & d.eq_active[self._eq]
        )
        fractions[slipping] *= np.exp(-slip[slipping] / self._decay[slipping])
        group_fractions = np.ones(self._n_groups)
        np.minimum.at(group_fractions, self._group_codes, fractions)
        fractions = group_fractions[self._group_codes]
        capacities = self._caps.copy()
        capacities[:, [0, 2, 3]] *= fractions[:, None]
        utilization = np.divide(loads, capacities, out=np.zeros_like(loads), where=capacities > 0)
        utilization[(capacities <= 0) & (loads > 1e-9)] = math.inf
        slip_u = np.divide(
            shear,
            self._slip * fractions,
            out=np.zeros_like(shear),
            where=(self._slip * fractions) > 0,
        )
        peak = np.maximum(np.max(utilization, axis=1), slip_u)
        for k, c in enumerate(self._connections):
            state = self.states[c.id]
            if state.failed or not d.eq_active[self._eq[k]]:
                continue
            state.preload_fraction = float(fractions[k])
            state.peak_utilization = max(state.peak_utilization, float(peak[k]))
            state.peak_axial_n = max(state.peak_axial_n, float(loads[k, 0]))
            state.peak_shear_n = max(state.peak_shear_n, float(loads[k, 1]))
            state.peak_torque_nm = max(state.peak_torque_nm, float(loads[k, 2]))
            state.peak_bending_nm = max(state.peak_bending_nm, float(loads[k, 3]))
            exceeded = np.flatnonzero(utilization[k] > 1)
            if len(exceeded):
                mode = int(exceeded[0])
                self.fail(
                    c,
                    int(self._eq[k]),
                    float(loads[k, mode]),
                    ("axial", "shear", "torsion", "bending")[mode],
                    float(capacities[k, mode]),
                    "transverse-slip self-loosening" if fractions[k] < 0.99 else c.cause,
                )
        # Fit displacement at the actual connection, including relative rotation.
        rest = np.asarray([v[5] for v in self.bindings])
        rest_rot = np.asarray([v[6] for v in self.bindings])
        displacement = relative - rest
        relative_rot = np.einsum("nji,njk->nik", parent_rotations, rotations)
        displacement += np.einsum("nij,nj->ni", relative_rot - rest_rot, self._point)
        error = np.linalg.norm(displacement, axis=1)
        for k, c in enumerate(self._connections):
            if self.states[c.id].failed and error[k] > c.tolerance and c.b not in self.detached:
                self.detached.add(c.b)
                self.events.append(
                    dict(
                        t=float(d.time),
                        event="part_detached",
                        joint=c.id,
                        parts=[c.b],
                        mode="displacement",
                        load=float(error[k]),
                        capacity=c.tolerance,
                        cause="fit displacement",
                        message=f"{c.b}: moved {error[k]:.6g} m (fit tolerance {c.tolerance:g} m)",
                    )
                )
                self.diagnostics.append(
                    Diagnostic.error(
                        "part_detached",
                        f"{c.b}: moved {error[k]:.6g} m from {c.id} (fit tolerance {c.tolerance:g} m)",
                        subjects=[c.b, c.id],
                        data={
                            "t": float(d.time),
                            "displacement_m": float(error[k]),
                            "tolerance_m": c.tolerance,
                        },
                    )
                )

    def _origin(self, body: int) -> np.ndarray:
        # Initial body frame expressed in robot coordinates, saved by caller.
        return self.origins.get(self.model.body(body).name, np.zeros(3)) if hasattr(self, "origins") else np.zeros(3)

    def report(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            c.id: {
                **c.as_dict(),
                **asdict(self.states[c.id]),
                "peak_utilization": self.states[c.id].peak_utilization
                if math.isfinite(self.states[c.id].peak_utilization)
                else None,
                "failures": [e for e in self.events if e.get("joint") == c.id],
            }
            for c in self.connections.connections
        }

    def final_diagnostics(self) -> list[Diagnostic]:
        return self.diagnostics + [
            Diagnostic.warning(
                "joint_utilization_high",
                f"{name}: peak utilization {s.peak_utilization:.3g}",
                subjects=[name],
                data={"utilization": s.peak_utilization},
            )
            for name, s in self.states.items()
            if s.peak_utilization >= 0.8 and not s.failed
        ]
