"""Generate a FeatureScript Feature Studio that builds the whole robot.

The CAD source of truth is code derived from ``robot.yaml`` + catalog, so the
same Git revision always regenerates the same Onshape geometry. Each component
becomes exactly one named part, modeled in its component frame and placed by
its pose. Purchased components get a material whose density reproduces the
catalog mass on the modeled envelope (their internals are not modeled);
fabricated structure gets its real material density, so its CAD mass is a
genuine geometric result.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from r3_catalog.schema import ComponentType
from r3_model.graph import rpy_matrix
from r3_model.project import Project
from r3_model.units import to_si

FEATURE_NAME = "r3Robot"


def _v(values: Any) -> str:
    x, y, z = (float(v) for v in values)
    return f"vector({x:.6f}, {y:.6f}, {z:.6f}) * meter"


def _v2(values: Any) -> str:
    x, y = (float(v) for v in values)
    return f"vector({x:.6f}, {y:.6f}) * meter"


def _color(hex_color: str) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return f"color({r:.3f}, {g:.3f}, {b:.3f})"


def _fs_id(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def fabricated(ctype: ComponentType) -> bool:
    """Structure parts we manufacture: mass comes from geometry x density."""
    return ctype.kind == "structure" and ctype.mechanical.density is not None


def _box(fid: str, size: list[float], extra: str = "") -> list[str]:
    sx, sy, sz = size
    return [f'fCuboid(context, id + "{fid}", {{"corner1" : {_v([-sx / 2, -sy / 2, 0])}, "corner2" : {_v([sx / 2, sy / 2, sz])}}});{extra}']


def _cylinder(fid: str, diameter: float, length: float, axis: str, center: list[float] | None = None) -> list[str]:
    unit = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}[axis]
    c = np.zeros(3) if center is None else np.asarray(center, dtype=float)
    if axis == "z" and center is None:
        bottom, top = c, c + unit * length  # vertical parts stand on their mount face
    else:
        bottom, top = c - unit * length / 2, c + unit * length / 2
    return [f'fCylinder(context, id + "{fid}", {{"bottomCenter" : {_v(bottom)}, "topCenter" : {_v(top)}, "radius" : {diameter / 2:.6f} * meter}});']


def _component_geometry(cid: str, ctype: ComponentType, params: dict[str, Any]) -> list[str]:
    """FeatureScript statements creating the component's bodies in its local frame."""
    mech = ctype.mechanical
    size = [float(v) for v in mech.size]
    lines: list[str] = []
    if ctype.id.startswith("chassis-plate"):
        outline = [[to_si(a), to_si(b)] for a, b in params["outline"]]
        holes = [[to_si(a), to_si(b), to_si(d)] for a, b, d in params.get("holes", [])]
        thickness = size[2]
        pts = ", ".join(_v2(p) for p in outline + [outline[0]])
        lines += [
            'const sk = newSketchOnPlane(context, id + "sk", {"sketchPlane" : plane(vector(0, 0, 0) * meter, vector(0, 0, 1))});',
            f'skPolyline(sk, "outline", {{"points" : [{pts}]}});',
        ]
        for index, (hx, hy, hd) in enumerate(holes):
            lines.append(f'skCircle(sk, "hole{index}", {{"center" : {_v2([hx, hy])}, "radius" : {hd / 2:.6f} * meter}});')
        lines += [
            "skSolve(sk);",
            'opExtrude(context, id + "plate", {"entities" : qSketchRegion(id + "sk", true), "direction" : vector(0, 0, 1),'
            f' "endBound" : BoundingType.BLIND, "endDepth" : {thickness:.6f} * meter}});',
            'opDeleteBodies(context, id + "delsk", {"entities" : qCreatedBy(id + "sk")});',
        ]
        return lines
    if mech.shape == "cylinder":
        diameter, length = size
        lines += _cylinder("body", diameter, length, mech.axis)
        if ctype.kind == "wheel":
            # Rounded tire shoulders: fillet both rim edges of the tire cylinder.
            lines.append(f'opFillet(context, id + "tire", {{"entities" : qCreatedBy(id + "body", EntityType.EDGE), "radius" : {min(diameter, length) * 0.22:.6f} * meter}});')
        if ctype.kind == "motor":
            # Output shaft (3.17 mm) and end bell boss, kept inside the catalog envelope length.
            lines += _cylinder("shaft", 0.00317, 0.012, mech.axis, center=[0, length / 2 - 0.006, 0] if mech.axis == "y" else None)
        return lines
    if ctype.kind == "computer":
        sx, sy, sz = size
        lines += [f'fCuboid(context, id + "pcb", {{"corner1" : {_v([-sx / 2, -sy / 2, 0])}, "corner2" : {_v([sx / 2, sy / 2, 0.0016])}}});']
        # USB/Ethernet stack at the +x end and SoC heat-spreader, inside the envelope.
        lines += [f'fCuboid(context, id + "eth", {{"corner1" : {_v([sx / 2 - 0.021, -sy / 2 + 0.002, 0.0016])}, "corner2" : {_v([sx / 2, -sy / 2 + 0.018, sz - 0.0035])}}});']
        lines += [f'fCuboid(context, id + "usb1", {{"corner1" : {_v([sx / 2 - 0.017, -0.002, 0.0016])}, "corner2" : {_v([sx / 2, 0.013, sz])}}});']
        lines += [f'fCuboid(context, id + "usb2", {{"corner1" : {_v([sx / 2 - 0.017, 0.0145, 0.0016])}, "corner2" : {_v([sx / 2, 0.0275, sz])}}});']
        lines += [f'fCuboid(context, id + "soc", {{"corner1" : {_v([-0.018, -0.010, 0.0016])}, "corner2" : {_v([-0.004, 0.004, 0.0040])}}});']
        lines += [f'fCuboid(context, id + "hdr", {{"corner1" : {_v([-0.0355, 0.0220, 0.0016])}, "corner2" : {_v([0.0155, 0.0270, 0.0101])}}});']
        return lines
    if ctype.kind == "pcb":
        sx, sy, sz = size
        board_bottom = sz - 0.0035
        lines += [f'fCuboid(context, id + "pcb", {{"corner1" : {_v([-sx / 2, -sy / 2, board_bottom])}, "corner2" : {_v([sx / 2, sy / 2, board_bottom + 0.0016])}}});']
        for name, inst in ctype.connectors.items():
            px, py, _ = (float(v) for v in inst.position)
            if name == "J_GPIO":
                lines.append(f'fCuboid(context, id + "{name}", {{"corner1" : {_v([px - 0.0254, py - 0.0025, 0])}, "corner2" : {_v([px + 0.0254, py + 0.0025, board_bottom])}}});')
            else:
                lines.append(f'fCuboid(context, id + "{name}", {{"corner1" : {_v([px - 0.004, py - 0.003, board_bottom + 0.0016])}, "corner2" : {_v([px + 0.004, py + 0.003, sz])}}});')
        return lines
    lines += _box("body", size)
    return lines


def generate(project: Project) -> tuple[str, dict[str, dict[str, Any]]]:
    """Return (feature studio body, per-component metadata used for verification)."""
    spec, catalog = project.spec, project.catalog
    blocks: list[str] = []
    meta: dict[str, dict[str, Any]] = {}
    for cid, comp in spec.components.items():
        ctype = catalog.component(comp.type)
        mech = ctype.mechanical
        rotation = rpy_matrix(comp.pose.rpy)
        origin = np.asarray(comp.pose.xyz, dtype=float)
        fid = _fs_id(cid)
        geometry = [line.replace('id + "', 'pid + "') for line in _component_geometry(cid, ctype, comp.params)]
        is_fabricated = fabricated(ctype)
        material_name = mech.material or ctype.name
        if is_fabricated:
            density_expr = f"{float(mech.density):.3f} * kilogram / meter ^ 3"
        else:
            density_expr = f"({float(mech.mass):.6f} * kilogram) / evVolume(context, {{\"entities\" : bodies}})"
        body = "\n            ".join(geometry)
        blocks.append(f"""
        {{   // {cid}: {ctype.name}
            const pid = id + "{fid}";
            {body}
            r3Finish(context, pid, "{cid}", {_v(origin)}, {_v(rotation[:, 0])}, {_v(rotation[:, 2])});
            const bodies = qCreatedBy(pid, EntityType.BODY);
            setProperty(context, {{"entities" : bodies, "propertyType" : PropertyType.NAME, "value" : "{cid}"}});
            setProperty(context, {{"entities" : bodies, "propertyType" : PropertyType.APPEARANCE, "value" : {_color(mech.color)}}});
            setProperty(context, {{"entities" : bodies, "propertyType" : PropertyType.MATERIAL,
                                   "value" : material("{material_name.replace('"', "'")}", {density_expr})}});
        }}""")
        meta[cid] = {"type": comp.type, "fabricated": is_fabricated, "catalog_mass": float(mech.mass),
                     "density": float(mech.density) if mech.density else None, "material": material_name}
    return "\n".join(blocks), meta


def feature_studio_source(project: Project, fs_version: str) -> str:
    body, _ = generate(project)
    name = project.spec.robot.name
    return f"""FeatureScript {fs_version};
import(path : "onshape/std/geometry.fs", version : "{fs_version}.0");

// GENERATED by r3_onshape from robot.yaml ({name}). Do not edit in Onshape;
// edit robot.yaml / catalog and run `r3 cad sync`.

function r3Finish(context is Context, id is Id, partName is string, origin is Vector, xAxis is Vector, zAxis is Vector)
{{
    const created = qCreatedBy(id, EntityType.BODY);
    if (size(evaluateQuery(context, created)) > 1)
    {{
        opBoolean(context, id + "union", {{"tools" : created, "operationType" : BooleanOperationType.UNION}});
    }}
    opTransform(context, id + "place", {{"bodies" : qCreatedBy(id, EntityType.BODY),
                                         "transform" : toWorld(coordSystem(origin, xAxis, zAxis))}});
}}

annotation {{ "Feature Type Name" : "R3 Robot ({name})" }}
export const {FEATURE_NAME} = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {{
    }}
    {{
{body}
    }});
"""


def source_digest(project: Project) -> str:
    body, _ = generate(project)
    return hashlib.sha256(body.encode()).hexdigest()[:16]
