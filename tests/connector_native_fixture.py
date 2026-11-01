"""Explicit OCP connector coupons for native geometry consumer tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

from pulled_fixture import write_pull


def box(size: Sequence[float], centre: Sequence[float] = (0, 0, 0)) -> Any:
    """A millimetre box centered at centre."""
    lo = np.asarray(centre) - np.asarray(size) / 2
    return BRepPrimAPI_MakeBox(gp_Pnt(*lo), *size).Shape()


def compound(shapes: Sequence[Any]) -> Any:
    result = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def placed(shape: Any, matrix: np.ndarray) -> Any:
    """Apply an SI rigid frame to a millimetre test solid."""
    matrix = np.asarray(matrix).copy()
    matrix[:3, 3] *= 1000
    transform = gp_Trsf()
    transform.SetValues(*matrix[:3].reshape(-1).tolist())
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def posts() -> Any:
    return compound([box((.64, .64, 6), (x, 0, 3)) for x in (-2.54, 0, 2.54)])


def socket() -> Any:
    shape = box((10, 3, 7), (0, 0, -3.5))
    for x in (-2.54, 0, 2.54):
        shape = BRepAlgoAPI_Cut(shape, box((.9, .9, 8), (x, 0, -3))).Shape()
    return shape


def solder_board() -> Any:
    shape = box((10, 3, 1), (0, 0, -.5))
    for x in (-2.54, 0, 2.54):
        drill = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, 0, -2), gp_Dir(0, 0, 1)), .5, 3).Shape()
        shape = BRepAlgoAPI_Cut(shape, drill).Shape()
    return shape


def pull(root: Path, shapes: dict[str, Any], *, poses=None, connectors=None):
    from r3_model.pulled import load_pulled
    from r3_model.project import Project
    from pulled_fixture import fixture_connector_frames
    if connectors is None and (root / "robot.yaml").is_file():
        connectors = fixture_connector_frames(Project.load(root))
    write_pull(root, shapes, poses=poses, connectors=connectors)
    return load_pulled(root)


def cable_coupons(project: Any, shapes: dict[str, Any], poses: dict[str, np.ndarray], *, floating_frame=None) -> None:
    """Declare simple native cable housings for frame/inventory tests only.

    These solid coupons intentionally have no contact recesses. Their geometry
    cannot certify a pin fit, and the mesh checker must report that limitation.
    """
    import yaml
    from r3_catalog.schema import ComponentType
    from r3_model.spec import ComponentInstance
    from r3_connectors.pins import mating_site_frame
    from r3_connectors.frames import Frame
    kind = ComponentType.model_validate(dict(id="native-connector-coupon", name="Synthetic native cable housing",
        kind="cable", mechanical=dict(size=[.003,.003,.005], mass=.001)))
    project.catalog.components[kind.id] = kind
    folder = project.root / "catalog/components"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "connector-coupon.yaml").write_text(yaml.safe_dump({"components": [kind.model_dump(mode="json")]}))
    peers = {conn.mates: f"{harness.id}.{name}"
             for harness in project.spec.electrical.harnesses
             for name, conn in harness.connectors.items() if conn.mates}
    for harness in project.spec.electrical.harnesses:
        for name, conn in harness.connectors.items():
            if conn.type == "integral":
                continue
            ref = f"{harness.id}.{name}"
            if conn.mates in project.graph.endpoints and project.graph.endpoints[conn.mates].owner_kind == "component":
                frame = mating_site_frame(project, ref, conn).mated()
            elif floating_frame is not None:
                peer = conn.mates or peers.get(ref)
                frame = floating_frame if peer is None or ref < peer else floating_frame.mated()
            else:
                raise AssertionError(f"Fixture needs an explicit floating frame for {ref}")
            project.spec.components[ref] = ComponentInstance(type=kind.id)
            shapes[ref] = box((3,3,5), (0,0,-2.5))
            poses[ref] = frame.matrix()
    project.__dict__.pop("_graph", None)


def add_cables_to_pull(project: Any) -> None:
    """Add native cable coupons to an already explicit instrumentation pull."""
    import yaml
    from r3_model.pulled import load_pulled
    from r3_model.pulled_view import apply_pulled
    from r3_onshape.step_geometry import read_step_shape
    from r3_connectors.frames import Frame
    native = load_pulled(project.root)
    scale = gp_Trsf()
    scale.SetScale(gp_Pnt(0,0,0), 1000)
    shapes = {name: BRepBuilderAPI_Transform(read_step_shape(part.step), scale, True).Shape()
              for name,part in native.parts.items()}
    poses = dict(native.poses)
    cable_coupons(project, shapes, poses, floating_frame=Frame.from_axes([.2,-.2,.25], [1,0,0], [0,0,1]))
    source = yaml.safe_load((project.root / "robot.yaml").read_text())
    for name in shapes:
        if name not in source["components"]:
            source["components"][name] = project.spec.components[name].model_dump(mode="json", exclude_none=True)
    (project.root / "robot.yaml").write_text(yaml.safe_dump(source, sort_keys=False))
    # This inventory fixture has no motion qualification; preserve all native
    # component positions and use the explicit cable mating frames above.
    model = pull(project.root, shapes, poses=poses,
                 connectors={name: part.mate_connectors for name, part in native.parts.items()})
    apply_pulled(project, model)
