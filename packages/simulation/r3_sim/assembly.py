"""Robot-independent MuJoCo assembly construction.

No locomotion model is required. Bodies, mass, primitive/convex collisions,
connections and native joints come entirely from the spec, catalog and CAD.
Vehicle, flight, marine or legged adapters can extend the returned MJCF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np

from r3_model.diagnostics import Diagnostic
from r3_model.project import Project
from r3_model.units import to_si
from .geometry import MassProperties, axis_rotation, box_inertia, cylinder_inertia, rotation_to_quat


@dataclass
class TransmissionInfo:
    """Declared gear transmission, independent of the robot's locomotion."""

    id: str
    actuator_component: str
    joints: list[str]
    ratio: float
    efficiency: float
    tendon: str
    rotor_inertia: float = 0.0
    output_inertia: float = 0.0
    backlash: float = 0.0


@dataclass
class AssemblyModel:
    xml: str
    root: str
    bodies: dict[str, list[str]]
    body_origin: dict[str, np.ndarray]
    mass_properties: dict[str, MassProperties]
    total_mass: float
    drives: list[TransmissionInfo] = field(default_factory=list)
    imu_site: str | None = None
    imu_component: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    assumptions: list[tuple[str, str]] = field(default_factory=list)
    mass_source: dict[str, str] = field(default_factory=dict)
    geometry: dict[str, dict[str, Any]] = field(default_factory=dict)
    connections: Any = None
    joint_bindings: list[dict[str, Any]] = field(default_factory=list)
    moving_joints: dict[str, Any] = field(default_factory=dict)
    cables: Any = None
    cad_extras: dict[str, list[str]] = field(default_factory=dict)
    fixture_world_contacts: dict[str, tuple[str | None, str | None]] = field(default_factory=dict, repr=False)


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
    com = position + rotation @ (_local_center(shape, size) + np.asarray(mech.envelope_offset))
    return MassProperties(mass, com, rotation @ local @ rotation.T, "catalog")


def cad_mass_properties(part: dict[str, Any]) -> MassProperties:
    ixx, iyy, izz, ixy, ixz, iyz = (float(v) for v in part["inertia"])
    inertia = np.array([[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]])
    return MassProperties(float(part["mass"]), np.asarray(part["com"], dtype=float), inertia, "cad")


def _find_root(project: Project, joint_children: dict[str, Any]) -> str:
    if project.spec.assembly and project.spec.assembly.root not in joint_children:
        return project.spec.assembly.root
    roots = [
        cid for cid, comp in project.spec.components.items() if comp.mount == "world" and cid not in joint_children
    ]
    if not roots:
        raise ValueError("no world-mounted root component for the robot assembly")
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
    r, g, b = (int(color[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return f"{r:.3f} {g:.3f} {b:.3f} {alpha}"


def _add_cables(
    project: Project,
    mj: ET.Element,
    routes: dict[str, Any],
    root: str,
    elements: dict[str, ET.Element],
    body_origin: dict[str, np.ndarray],
    diagnostics: list[Diagnostic],
    assumptions: list[tuple[str, str]],
) -> Any:
    """Routed harnesses as MuJoCo cables in this world (None if they cannot be built)."""
    try:
        from r3_wiring.bodies import ROOT
        from r3_wiring.mujoco_cables import emit_mjcf, plan_cables

        plan = plan_cables(project, routes, timestep=float(project.spec.simulation.timestep), individual_parts=True)
        body = lambda kin_body: root if kin_body == ROOT else kin_body  # noqa: E731
        emit_mjcf(mj, plan, lambda b: elements[body(b)], lambda b: body_origin[body(b)], body)
    except Exception as error:  # noqa: BLE001 - any failure leaves the vehicle without cables
        diagnostics.append(
            Diagnostic.warning(
                "cable_model_fallback",
                f"MuJoCo cables could not be built from routes.json ({type(error).__name__}: {error})",
                subjects=["wiring/routes.json"],
                data={"fallback": 1},
            )
        )
        return None
    diagnostics.extend(plan.diagnostics)
    assumptions.extend(plan.assumptions)
    return plan


def build_assembly_model(
    project: Project,
    initial_pose: tuple[float, float, float] = (0, 0, 0),
    obstacles: list[dict[str, Any]] | None = None,
    routes: dict[str, Any] | None = None,
    course: list[list[float]] | None = None,
) -> AssemblyModel:
    """Build an arbitrary robot without loading a vehicle builder or dynamics.

    Returned MJCF is also usable directly with ``mujoco.MjModel`` and
    ``JointMonitor``. ``RobotDynamics`` provides the shared monitored runtime.
    World fixtures and mandatory load profiles are applied by ``load_cases``.
    """
    from .joints import separate_parts

    spec = project.spec
    joints = {j.child: j for j in spec.mechanical.joints}
    root = _find_root(project, joints)
    cad = project.read_build_json("cad/cad_model.json") or {}
    cad_parts = cad.get("parts", {})
    mesh_dir = project.root / "build" / "cad"
    part_props = {}
    mass_source = {}
    diagnostics = []
    assumptions = []
    for cid in spec.components:
        part = cad_parts.get(cid, {})
        if part.get("mass") and part.get("com") and part.get("inertia"):
            part_props[cid] = cad_mass_properties(part)
            mass_source[cid] = part.get("mass_status", "cad")
        else:
            part_props[cid] = catalog_mass_properties(project, cid)
            mass_source[cid] = "catalog"
    for name, part in cad_parts.items():
        if (
            name not in part_props
            and part.get("owner") in spec.components
            and part.get("mass")
            and part.get("com")
            and part.get("inertia")
        ):
            part_props[name] = cad_mass_properties(part)
    estimated = [cid for cid, value in mass_source.items() if value == "catalog"]
    if estimated:
        diagnostics.append(
            Diagnostic.warning(
                "cad_mass_estimated",
                "Catalog primitive inertia is used where CAD mass properties are unavailable",
                subjects=estimated,
                data={"estimated_count": len(estimated)},
            )
        )
    mj = ET.Element("mujoco", model=spec.robot.name)
    ET.SubElement(mj, "compiler", angle="radian", autolimits="true", meshdir=str(mesh_dir))
    ET.SubElement(
        mj,
        "option",
        timestep=str(spec.simulation.physics_timestep),
        integrator="implicitfast",
        cone="pyramidal",
        impratio="10",
        gravity=f"0 0 {-spec.simulation.environment.gravity}",
    )
    visual=ET.SubElement(mj,"visual")
    ET.SubElement(visual,"global",offwidth="1280",offheight="960",azimuth="135",elevation="-25")
    ET.SubElement(visual,"quality",shadowsize="4096",offsamples="8")
    ET.SubElement(visual,"map",znear="0.005",zfar="5000",haze="0.15")
    ET.SubElement(visual,"headlight",ambient="0.35 0.35 0.35",diffuse="0.6 0.6 0.6")
    asset = ET.SubElement(mj, "asset")
    ET.SubElement(asset, "texture", name="sky", type="skybox", builtin="gradient", rgb1="0.55 0.7 0.9", rgb2="0.95 0.97 1.0", width="512", height="512")
    ET.SubElement(asset, "material", name="ground", rgba=".33 .5 .26 1")
    world = ET.SubElement(mj, "worldbody")
    ET.SubElement(world,"light",name="sun",pos="0 0 50",dir="-0.3 -0.2 -1",directional="true",castshadow="true",diffuse="0.8 0.8 0.75")
    ET.SubElement(
        world,
        "geom",
        name="ground",
        type="plane",
        size="0 0 .05",
        material="ground",
        friction=f"{spec.simulation.environment.ground_friction} .005 .0001",
        contype="0",
        conaffinity="3",
    )
    for i, obstacle in enumerate(obstacles if obstacles is not None else spec.simulation.environment.obstacles):
        ET.SubElement(
            world,
            "geom",
            name=f"obstacle_{obstacle.get('id', i)}",
            type="box",
            pos=_fmt([to_si(v) for v in obstacle.get("center", [0, 0, 0])]),
            size=_fmt(np.asarray([to_si(v) for v in obstacle.get("size", [0.1, 0.1, 0.1])]) / 2),
            contype="0",
            conaffinity="3",
        )
    from .scene_visual import add_scene, add_cameras
    add_scene(project, mj, asset, world, course, initial_pose, assumptions)
    meshes = {}
    for name in part_props:
        file = (cad_parts.get(name) or {}).get("mesh")
        if file and (mesh_dir / file).exists():
            meshes[name] = "mesh_" + name.replace(":", "__")
            ET.SubElement(asset, "mesh", name=meshes[name], file=str(mesh_dir / file))
    # Canonical visuals are in robot coordinates. The shared body assembler
    # moves each one into the frame of its own body and emits its real joints.
    elements = {}
    origins = {cid: np.zeros(3) for cid in spec.components}
    bodies = {cid: [cid] for cid in spec.components}
    props = dict(part_props)
    extras = {cid: [] for cid in spec.components}
    for cid, comp in spec.components.items():
        el = ET.SubElement(world, "body", name=cid)
        elements[cid] = el
        mech = project.catalog.component(comp.type).mechanical
        common = dict(mass="0", contype="0", conaffinity="0", group="1", rgba=_hex_rgba(mech.color))
        if cid in meshes:
            ET.SubElement(el, "geom", name=f"vis_{cid}", type="mesh", mesh=meshes[cid], **common)
        else:
            shape, size, axis = _component_shape(project, cid)
            position, rotation = project.graph.component_pose(cid)
            center = position + rotation @ (_local_center(shape, size) + np.asarray(mech.envelope_offset))
            if shape == "cylinder":
                rotation = rotation @ axis_rotation(axis)
                geom_size = [size[0] / 2, size[1] / 2]
            else:
                geom_size = np.asarray(size) / 2
            ET.SubElement(
                el,
                "geom",
                name=f"vis_{cid}",
                type=shape,
                pos=_fmt(center),
                quat=_fmt(rotation_to_quat(rotation)),
                size=_fmt(geom_size),
                **common,
            )
    connections, bindings = separate_parts(
        project, mj, root, bodies, origins, props, extras, elements, joints, cad_parts, part_props, initial_pose
    )
    add_cameras(project, elements, origins, lambda cid: cid, assumptions)
    diagnostics.extend(connections.diagnostics)
    imu_component = None
    for cid, comp in spec.components.items():
        if any(s.protocol == "mpu6050" for s in project.catalog.component(comp.type).software):
            imu_component = cid
    imu_site = None
    sensor = ET.SubElement(mj, "sensor")
    if imu_component:
        position, rotation = project.graph.component_pose(imu_component)
        ET.SubElement(
            elements[imu_component],
            "site",
            name="imu",
            pos=_fmt(position - origins[imu_component]),
            quat=_fmt(rotation_to_quat(rotation)),
            size=".001",
        )
        for type_, name in [("accelerometer", "imu_acc"), ("gyro", "imu_gyro")]:
            ET.SubElement(sensor, type_, name=name, site="imu")
        imu_site = "imu"
    tendon = ET.SubElement(mj, "tendon")
    actuator = ET.SubElement(mj, "actuator")
    drives = []
    for trans in spec.mechanical.transmissions:
        if trans.type == "ackermann_steering":
            diagnostics.append(
                Diagnostic.warning(
                    "vehicle_transmission_skipped",
                    "Ackermann steering requires the declared wheel adapter",
                    subjects=[trans.id],
                    data={"skipped": 1},
                )
            )
            continue
        parameters = _behavior_params(project, trans.actuator)
        drive = TransmissionInfo(
            trans.id,
            trans.actuator,
            list(trans.joints),
            trans.ratio,
            trans.efficiency,
            f"tendon_{trans.id}",
            rotor_inertia=_si(parameters.get("rotor_inertia"), 0),
        )
        fixed = ET.SubElement(tendon, "fixed", name=drive.tendon, armature=str(drive.rotor_inertia * drive.ratio**2))
        for joint in drive.joints:
            ET.SubElement(fixed, "joint", joint=joint, coef=str(1 / len(drive.joints)))
        ET.SubElement(actuator, "motor", name=f"act_{drive.id}", tendon=drive.tendon, gear="1")
        drives.append(drive)
    cables = _add_cables(project, mj, routes, root, elements, origins, diagnostics, assumptions) if routes else None
    # Native sleeping for passive islands; plugin trees stay awake until their
    # connections release. No constraint monitor or tolerance is disabled.
    import mujoco

    if hasattr(mujoco.mjtEnableBit, "mjENBL_SLEEP"):
        mj.find("option/flag").set("sleep", "enable")
        if cables:
            for element in mj.findall("worldbody/body"):
                if element.find(".//composite") is not None:
                    element.set("sleep", "never")
    assumptions.append(
        (
            "mechanical",
            "Every declared component and load-bearing hardware part is a separate body; real connections alone retain them.",
        )
    )
    model = AssemblyModel(
        ET.tostring(mj, encoding="unicode"),
        root,
        bodies,
        origins,
        props,
        sum(p.mass for p in props.values()) + (cables.total_mass if cables else 0),
        drives=drives,
        imu_site=imu_site,
        imu_component=imu_component,
        diagnostics=diagnostics,
        assumptions=assumptions,
        mass_source=mass_source,
        connections=connections,
        joint_bindings=bindings,
        moving_joints={k: v for k, v in joints.items() if v.type != "fixed"},
        cables=cables,
        cad_extras=extras,
    )

    from .load_cases import configure_model
    from r3_model.spec import Scenario

    configure_model(model, Scenario(id="assembly_initial", duration=0), project)
    return model


def write_mjcf(model: AssemblyModel, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    import xml.dom.minidom

    path.write_text(xml.dom.minidom.parseString(model.xml).toprettyxml(indent="  "))
    return path


def mass_report(model: AssemblyModel) -> dict[str, Any]:
    return {
        body: {
            "mass": round(p.mass, 6),
            "com": [round(v, 6) for v in p.com],
            "source": p.source,
            "members": model.bodies[body],
            "cad_parts": model.cad_extras.get(body, []),
        }
        for body, p in model.mass_properties.items()
    } | {"_total_mass": round(model.total_mass, 6)}
