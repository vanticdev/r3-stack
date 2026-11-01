"""Synthetic immutable STEP/STL hand-offs; no catalog-generated CAD."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np
import yaml

if TYPE_CHECKING:
    from r3_model.project import Project


def fixture_connector_frames(project: Project) -> dict[str, dict[str, np.ndarray]]:
    """Explicit fake-native port input for fixtures using nominal catalog frames.

    This seeds test snapshot data, never solid geometry or production fallback.
    Resolver regressions provide contradictory native frames independently.
    """
    from r3_connectors.pins import instance_frame
    frames = {}
    for name, component in project.spec.components.items():
        if component.type not in project.catalog.components:
            continue
        frames[name] = {"origin": np.eye(4),
                        **{component.onshape.mate_connectors.get(port, port): instance_frame(inst).matrix()
                           for port, inst in project.catalog.component(component.type).connectors.items()}}
    return frames


def write_pull(root: Path, shapes: dict[str, Any], *, poses=None, mates=None, connectors=None,
               mass: float | dict[str, float] = 0.02, material: str | dict[str, str] = "PLA") -> dict[str, Any]:
    import trimesh
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.Interface import Interface_Static
    from r3_onshape.step_geometry import part_from_step

    folder = root / "cad/pulled"
    (folder / "parts").mkdir(parents=True, exist_ok=True)
    poses = poses or {name: np.eye(4) for name in shapes}
    data = {"schema": "r3/pull@1", "document_id": "synthetic-document", "version_id": "synthetic-version",
            "parts": {}, "poses": {name: np.asarray(frame).tolist() for name, frame in poses.items()}, "mates": mates or []}
    for index, (name, shape) in enumerate(shapes.items()):
        step = folder / f"parts/{index}.step"
        stl = folder / f"parts/{index}.stl"
        for path in (step, stl):
            if path.exists():
                path.chmod(0o644)
        # Fixture dimensions are explicitly mm, independent of reader settings.
        Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
        writer = STEPControl_Writer()
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(str(step))
        geometry = part_from_step(name, step)
        mesh = trimesh.Trimesh(geometry.vertices, geometry.triangles, process=True)
        mesh.export(stl)
        props = mesh.mass_properties
        part_mass = mass[name] if isinstance(mass, dict) else mass
        inertia = props.inertia * part_mass / props.mass
        data["parts"][name] = {"stl": str(stl.relative_to(folder)), "step": str(step.relative_to(folder)),
            "sha256": {"stl": hashlib.sha256(stl.read_bytes()).hexdigest(), "step": hashlib.sha256(step.read_bytes()).hexdigest()},
            "mass": part_mass, "com": props.center_mass.tolist(), "inertia": inertia.reshape(-1).tolist(),
            "material": material[name] if isinstance(material, dict) else material, "purchased": False,
            "mate_connectors": {key: np.asarray(frame).tolist() for key, frame in (connectors or {}).get(name, {"origin": np.eye(4)}).items()}}
        step.chmod(0o444); stl.chmod(0o444)
    manifest = folder / "pull.json"
    if manifest.exists():
        manifest.chmod(0o644)
    manifest.write_text(json.dumps(data))
    lock_path = root / "r3.lock.yaml"
    lock = yaml.safe_load(lock_path.read_text()) if lock_path.exists() else {}
    lock["cad_pull"] = {"manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
    lock_path.write_text(yaml.safe_dump(lock))
    manifest.chmod(0o444)
    from r3_model.description import refresh_pulled
    if (root / "robot.yaml").exists():
        refresh_pulled(root, data)
    return data


def box(size: Sequence[float], offset: Sequence[float] = (0, 0, 0)) -> Any:
    """Explicit millimetre coupon, centered in XY with its bottom at offset Z."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    x, y, z = size
    return BRepPrimAPI_MakeBox(gp_Pnt(offset[0]-x/2, offset[1]-y/2, offset[2]), x, y, z).Shape()


def cylinder(radius: float, length: float, axis: Sequence[float] = (0, 0, 1)) -> Any:
    """Explicit millimetre cylinder centered at its local frame."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*(-np.asarray(axis)*length/2)), gp_Dir(*axis)), radius, length).Shape()


def publish_project(project: Project, shapes: dict[str, Any], *, mass=0.02,
                    material="Aluminum 6061-T6", attachments=(), connector_frames=None) -> Project:
    """Publish explicitly authored test solids using the fixture's nominal frames.

    Attachments are (id, kind, a, b, world origin, world axis, limits). No
    geometry, mass, or attachment is inferred from the purchased catalog.
    """
    from r3_model.graph import rpy_matrix
    poses = {}
    for name in shapes:
        pose = project.spec.components[name].pose
        poses[name] = np.eye(4)
        poses[name][:3, :3] = rpy_matrix(pose.rpy)
        poses[name][:3, 3] = pose.xyz
    ports = fixture_connector_frames(project)
    connectors = {name: {"origin": np.eye(4), **ports.get(name, {}), **(connector_frames or {}).get(name, {})} for name in shapes}
    mates = []
    for name, kind, a, b, point, axis, limits in attachments:
        frame = np.eye(4)
        frame[:3, 3] = point
        z = np.asarray(axis, float); z /= np.linalg.norm(z)
        seed = np.eye(3)[np.argmin(abs(z))]
        x = np.cross(seed, z); x /= np.linalg.norm(x)
        frame[:3, :3] = np.column_stack([x, np.cross(z, x), z])
        for part in (a, b):
            connectors[part][name] = np.linalg.inv(poses[part]) @ frame
        mates.append(dict(id=name, kind=kind, a=[a, name], b=[b, name], axis=list(axis), limits=limits))
    write_pull(project.root, shapes, poses=poses, mates=mates, connectors=connectors, mass=mass, material=material)
    from r3_model.project import Project
    return Project.load(project.root)


def fixture_attachments(project: Project) -> list[tuple]:
    """Translate explicitly authored fixture joints and rated bonds to CAD mates."""
    attachments = [(j.id, {"fixed": "fastened", "continuous": "revolute", "prismatic": "slider"}.get(j.type, j.type),
                    j.parent, j.child, j.origin, j.axis, j.limits)
                   for j in project.spec.mechanical.joints]
    pairs = {frozenset((a, b)) for _, _, a, b, *_ in attachments}
    for interface in project.spec.mechanical.interfaces:
        if len(interface.parts) != 2 or interface.kind == "resting":
            continue
        a, b = interface.parts
        if frozenset((a, b)) not in pairs:
            attachments.append((interface.id, "fastened", a, b, interface.axis_point if interface.axis_point is not None else project.spec.components[b].pose.xyz, interface.axis_dir, None))
            pairs.add(frozenset((a, b)))
    for fastener in project.spec.mechanical.fasteners:
        hardware = "fastener:" + fastener.id
        targets = [*fastener.clamps, *(["nut:" + fastener.id] if fastener.nut else [])]
        for index, target in enumerate(targets):
            attachments.append((f"{fastener.id}:{index+1}", "fastened", hardware, target,
                                fastener.position, fastener.axis, None))
    return attachments


def screw_coupons(root: Path, shapes: dict[str, Any], masses: dict[str, float]) -> None:
    """Add explicit M3 test screws/nuts as native instances, never implicit mass.

    Only the synthetic plate and arm fixtures call this helper. Geometry is
    the specified 3 mm x 20 mm shank and a 5.5 mm x 3 mm nut coupon.
    """
    data = yaml.safe_load((root / "robot.yaml").read_text())
    for fastener in data.get("mechanical", {}).get("fasteners", []):
        for prefix, shape, mass in [("fastener:", cylinder(1.5, 20), .0015),
                                    *([("nut:", cylinder(2.75, 3), .0005)] if fastener.get("nut") else [])]:
            name = prefix + fastener["id"]
            data["components"][name] = dict(type="native-test-hardware", pose=dict(xyz=fastener["position"]))
            shapes[name] = shape
            masses[name] = mass
    folder = root / "catalog/components"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "native-hardware.yaml").write_text(yaml.safe_dump({"components": [
        dict(id="native-test-hardware", name="Synthetic hardware coupon", kind="structure", mechanical=dict(size=[.001,.001,.001], mass=1e-9))]}))
    (root / "robot.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


def instrumentation_pull(root: Path) -> Project:
    """Native test coupons for the stationary electrical/HAL bench.

    These deliberately simple bodies are authored test solids, not catalog
    envelopes or purported manufacturer CAD. Electrical metadata stays intact.
    """
    from r3_model.project import Project
    project = Project.load(root)
    sizes = dict(base=(360,320,6), panel=(90,90,4), pi=(85,56,8), controller=(40,30,1.6),
                 battery=(100,35,20), esc=(40,30,20), motor=(30,30,40), servo=(30,15,25),
                 fuse=(15,5,15), gps=(30,30,5), receiver=(20,10,4), imu=(15,10,2),
                 rtk_radio=(30,15,5), rx_antenna=(30,2,2), encoder=(12,12,4), arm=(80,12,12))
    assert set(sizes) == set(project.spec.components)
    shapes = {name: box(size) for name, size in sizes.items()}
    masses = {name: .02 for name in shapes}
    masses.update(base=.9, battery=.3, motor=.16, arm=.025)
    return publish_project(project, shapes, mass=masses, attachments=fixture_attachments(project),
                           connector_frames={"rx_antenna": {"feed": np.eye(4)}})


def wiring_pull(project: Project, *, long_arm: bool = False) -> Project:
    """Explicit solids for the two connector routing/dynamics coupon."""
    from r3_model.spec import RobotSpec
    # Start the new fixture publication from its authored ratings/poses. A
    # preceding native wiring fixture may have projected joints and metadata;
    # those belong to its old immutable pull, not this new CAD revision.
    source = yaml.safe_load((project.root / "robot.yaml").read_text())
    source.pop("attachments", None)
    for component in source["components"].values():
        component.pop("pulled", None)
    project.spec = RobotSpec.model_validate(source)
    project.__dict__.pop("_assembly", None)
    project.__dict__.pop("_graph", None)
    shapes = {"plate": box((320,160,3)), "src": box((20,20,20)), "dst": box((20,20,20))}
    if "arm" in project.spec.components:
        shapes["arm"] = box((20,80,20), (0,30,0)) if long_arm else box((20,20,20))
    if "block" in project.spec.components:
        shapes["block"] = box((40,80,50))
    if "guard" in project.spec.components:
        shapes["guard"] = box((10,10,10))
    from r3_model.spec import ComponentInstance, Pose, Interface
    rated_pairs = {frozenset(interface.parts) for interface in project.spec.mechanical.interfaces}
    ratings = lambda values: {key: dict(value=value, status="measured", source="Synthetic routing coupon fixture")
                              for key, value in values.items()}
    for joint in project.spec.mechanical.joints:
        if frozenset((joint.parent, joint.child)) not in rated_pairs:
            joint.interface = joint.id + "_capture"
            project.spec.mechanical.interfaces.append(Interface(id=joint.interface, kind="revolute_fit",
                parts=[joint.parent, joint.child], axis_point=joint.origin, axis_dir=joint.axis,
                params={"physics": ratings(dict(retention_force=1000., shear_force=1000., radius=.01))}))
    for parent, child in [("plate", "src"), ("arm" if "arm" in shapes else "plate", "dst"), ("plate", "block")]:
        if child in shapes and frozenset((parent, child)) not in rated_pairs:
            project.spec.mechanical.interfaces.append(Interface(id="bond_" + child, kind="adhesive", parts=[parent, child],
                axis_point=project.spec.components[child].pose.xyz, axis_dir=[0,0,1],
                params={"physics": ratings(dict(area=.0004, shear_strength=1e7, peel_strength=1e7, radius=.01))}))
    # Separate native cable plugs, with explicit shell coupons. Their electrical
    # termination metadata still resides on harness lead.A / lead.B.
    from r3_connectors.pins import component_connector_frame
    connector_frames = {}
    for name, owner, offset in [("lead.A", "src", [.014,0,.008]), ("lead.B", "dst", [-.014,0,.008])]:
        point = (np.asarray(project.spec.components[owner].pose.xyz) + offset).tolist()
        project.spec.components[name] = ComponentInstance(type="native-test-plug", mount=owner, pose=Pose(xyz=point))
        project.spec.mechanical.interfaces.append(Interface(id="capture_" + name, kind="adhesive", parts=[owner,name],
            axis_point=point, axis_dir=[1,0,0], params={"physics": {
                key: dict(value=value,status="measured",source="Synthetic captive plug fixture")
                for key,value in dict(area=.0001,shear_strength=1e8,peel_strength=1e8,radius=.003).items()}}))
        shapes[name] = box((4,8,4))
        # The housing's part frame is offset from its mating plane. Publish
        # that local connector explicitly; routing must never move the mesh.
        local = component_connector_frame(project, owner + ".J1").mated().matrix()
        local[:3, 3] -= point
        connector_frames[name] = {"mating": local}

    (project.root / "catalog/components/native-plug.yaml").write_text(yaml.safe_dump({"components": [
        dict(id="native-test-plug", name="Synthetic cable plug coupon", kind="cable", mechanical=dict(size=[.001,.001,.001], mass=1e-9))]}))
    (project.root / "robot.yaml").write_text(yaml.safe_dump(project.spec.model_dump(mode="json", exclude_none=True, by_alias=True), sort_keys=False))
    masses = {name: .02 for name in shapes}
    masses.update(plate=.3)
    if "block" in shapes:
        masses["block"] = .1
    return publish_project(project, shapes, mass=masses, attachments=fixture_attachments(project),
                           connector_frames=connector_frames)


def revise_project(project: Project, *, shapes=None, materials=None) -> Project:
    """Republish a test CAD revision, retaining unchanged explicit native solids."""
    from r3_model.pulled import load_pulled
    from r3_onshape.step_geometry import read_step_shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Pnt
    pulled = load_pulled(project.root)
    scale = gp_Trsf(); scale.SetScale(gp_Pnt(0, 0, 0), 1000)
    replacements = dict(shapes or {})
    authored = {}
    for name, part in pulled.parts.items():
        if name in project.spec.components:
            authored[name] = replacements.pop(name) if name in replacements else BRepBuilderAPI_Transform(
                read_step_shape(part.step), scale, True).Shape()
    authored.update(replacements)
    # Keep the fixture's authored capacity links; apply_pulled projects native
    # mate ids into runtime Joint.interface, which is not an authored rating.
    source = yaml.safe_load((project.root / "robot.yaml").read_text())
    data = project.spec.model_dump(mode="json", exclude_none=True, by_alias=True)
    data["mechanical"]["joints"] = [joint for joint in source.get("mechanical", {}).get("joints", [])
        if joint["parent"] in authored and joint["child"] in authored]
    (project.root / "robot.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    attachments = []
    for mate in pulled.mates:
        if mate.a[0] in authored and mate.b[0] in authored:
            frame = pulled.poses[mate.a[0]] @ pulled.parts[mate.a[0]].mate_connectors[mate.a[1]]
            attachments.append((mate.id, mate.kind, mate.a[0], mate.b[0], frame[:3, 3].tolist(), mate.axis or [0,0,1], mate.limits))
    return publish_project(project, authored,
        mass={name: pulled.parts[name].mass if name in pulled.parts else .01 for name in authored},
        material={name: (materials or {}).get(name, pulled.parts[name].material if name in pulled.parts else "Aluminum 6061-T6") for name in authored},
        attachments=attachments)
