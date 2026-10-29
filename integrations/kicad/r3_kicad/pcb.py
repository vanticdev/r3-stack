"""Board plans for the pcbnew engine (``kicad_scripts/board_build.py``).

The plan joins KiCad's own netlist export (components, footprint fields, symbol
UUIDs, pin -> net) with the design intent (placement, board setup, deliberate
power copper). The engine script applies it with pcbnew; this module never
writes KiCad files itself.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .engine import StepResult, kicad_python
from .intent import BoardIntent, Zone
from .library import find_install

VIA_SIZES = [[0.6, 0.3], [0.8, 0.4]]


def board_net(name: str, nets: set[str]) -> str:
    """Local labels on the root sheet are ``/NAME`` nets in KiCad's netlist and board."""
    return f"/{name}" if f"/{name}" in nets else name


def netlist_components(xml_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    root = ET.parse(xml_path).getroot()
    pads: dict[str, dict[str, str]] = {}
    nets: list[str] = []
    for net in root.iter("net"):
        name = net.get("name", "")
        nets.append(name)
        for node in net.iter("node"):
            pads.setdefault(node.get("ref", ""), {})[node.get("pin", "")] = name
    components = []
    for comp in root.iter("comp"):
        ref = comp.get("ref", "")
        props = {p.get("name", ""): p.get("value", "") for p in comp.iter("property")}
        fields = {f.get("name", ""): (f.text or "") for f in comp.iter("field")
                  if f.get("name") not in ("Reference", "Value", "Footprint") and f.text}
        components.append({"ref": ref, "value": comp.findtext("value") or "", "footprint": comp.findtext("footprint") or "",
                           "uuid": (comp.findtext("tstamps") or "").split()[0], "fields": fields,
                           "in_bom": "exclude_from_bom" not in props, "pads": pads.get(ref, {})})
    return components, nets


def _zone(z: Zone, nets: set[str]) -> dict[str, Any]:
    return {"net": board_net(z.net, nets), "layer": z.layer, "name": z.name or f"{z.net} {z.layer}", "priority": z.priority,
            "polygon": [list(p) for p in z.polygon], "clearance": z.clearance, "min_thickness": z.min_thickness,
            "thermal_gap": z.thermal_gap, "thermal_spoke": z.thermal_spoke, "solid": z.solid}


def build_plan(intent: BoardIntent, netlist_xml: Path, board: Path, date: str = "") -> dict[str, Any]:
    install = find_install()
    components, net_list = netlist_components(netlist_xml)
    nets = set(net_list)
    for comp in components:
        part = intent.part(comp["ref"])
        if part.pcb is not None:
            comp["placement"] = {"x": part.pcb.x, "y": part.pcb.y, "rotation": part.pcb.rotation, "side": part.pcb.side,
                                 "anchors": [[n, list(xy)] for n, xy in part.pcb.anchors]}
    rules = {k: v for k, v in intent.rules.items() if isinstance(v, (int, float))}
    m = intent.mounting
    keepouts = [{"ref": p.ref, "x": p.pcb.x, "y": p.pcb.y, "rules": [
        {"name": "screw head envelope", "layer": "F.Cu", "kind": "footprints", "radius": m.top_part_radius},
        {"name": "screw head contact", "layer": "F.Cu", "kind": "copper", "radius": m.top_copper_radius},
        {"name": "standoff envelope", "layer": "B.Cu", "kind": "footprints", "radius": m.bottom_part_radius},
        {"name": "standoff contact", "layer": "B.Cu", "kind": "copper", "radius": m.bottom_copper_radius}]}
        for p in intent.parts if p.lib_id.startswith("Mechanical:MountingHole") and p.pcb is not None]
    pre_route = [z for z in intent.zones if z.before_routing]
    return {
        "board": str(board), "title": intent.title, "date": date, "origin": list(intent.origin),
        "size": [intent.width, intent.height], "corner_radius": intent.corner_radius, "thickness": intent.thickness,
        "footprint_dir": str(install.footprints) if install else "", "sheet_file": f"{intent.name}.kicad_sch",
        "rules": rules, "track_widths": intent.rules.get("track_widths", []), "via_sizes": VIA_SIZES,
        "net_classes": [{"name": c.name, "track_width": c.track_width, "clearance": c.clearance,
                         "via_diameter": c.via_diameter, "via_drill": c.via_drill,
                         "nets": [board_net(n, nets) for n in c.nets]} for c in intent.net_classes],
        "nets": [n for n in net_list if n],
        "components": components,
        "keepouts": keepouts,
        "tracks": [{"net": board_net(t.net, nets), "width": t.width, "layer": t.layer,
                    "points": [p if isinstance(p, str) else list(p) for p in t.points]} for t in intent.power_tracks],
        "vias": [{"net": board_net(v.net, nets), "x": v.x, "y": v.y, "diameter": v.diameter, "drill": v.drill}
                 for v in intent.vias],
        # Zones marked before_routing go in now (Freerouting sees them as planes); the rest after routing.
        "zones": [_zone(z, nets) for z in pre_route],
    }


def ground_plan(intent: BoardIntent, board: Path, nets: set[str]) -> dict[str, Any]:
    return {"mode": "zones", "board": str(board), "origin": list(intent.origin),
            "zones": [_zone(z, nets) for z in intent.zones if not z.before_routing],
            "stitch": {"net": board_net("GND", nets), "spacing": 4.0, "diameter": 0.6, "drill": 0.3}}


def run_plan(plan: dict[str, Any], work: Path, name: str) -> tuple[StepResult, dict[str, Any]]:
    plan_path, report_path = work / f"{name}.json", work / f"{name}-report.json"
    plan_path.write_text(json.dumps(plan, indent=1))
    result = kicad_python("board_build.py", str(plan_path), str(report_path))
    report = json.loads(report_path.read_text()) if report_path.exists() else {"errors": [result.output[-2000:]]}
    return result, report
