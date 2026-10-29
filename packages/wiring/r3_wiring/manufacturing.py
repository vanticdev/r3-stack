"""Harness manufacturing package: WireViz drawings, BOM, cut list, tooling, labels,
branch dimensions, assembly sequence and continuity test, under
``build/wiring/manufacturing/``.

Drawings are made by WireViz (the established open harness-documentation tool,
``wireviz.wireviz.parse``; Graphviz renders SVG/PNG). The WireViz YAML is
generated from the contact graph: every harness connector with its housing
MPN and pin labels (signals), every conductor with its gauge, colour, label and
cut length, splices as WireViz ``simple`` connectors, integral component leads
as the component they belong to.

Every file carries the release status of the geometry checks
(:func:`release_status`): ``released`` only when no geometry error remains on the
harness. Unreleased packages are still written (they are what the reviewer
fixes from), but every row and drawing title says ``UNRELEASED`` and
``manifest.json`` lists the blocking diagnostics.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from r3_model.diagnostics import Diagnostic, Severity

from .cutlength import CutLength, termination

# Diagnostics that block the release of a harness (geometry and buildability).
BLOCKING = {"wire_clearance_violation", "wire_intersects_component", "connector_clearance_violation",
            "wire_intersects_connector", "connector_intersects_component", "wire_intersects_wire",
            "wire_bend_radius_violation", "wire_route_failed", "wire_too_short", "route_infeasible",
            "cable_unsupported_span", "cable_support_unplaceable", "harness_interference", "cable_sags_into_moving_part",
            "wire_dynamic_clearance_violation", "terminal_overload", "geometry_uncertain", "cad_mesh_invalid",
            "floating_mate_unplaced", "wire_endpoint_unplaced"}
IEC_COLORS = {"red": "RD", "black": "BK", "white": "WH", "green": "GN", "yellow": "YE", "blue": "BU",
              "orange": "OG", "brown": "BN", "purple": "VT", "gray": "GY", "grey": "GY", "pink": "PK"}
ISOLATION = "> 100 MOhm at 50 V DC"  # test specification (estimate for a 7.4 V harness)
FIXTURE_RESISTANCE = 0.05  # ohm allowance for probes and fixture


def release_status(project: Any, diagnostics: list[Diagnostic]) -> dict[str, Any]:
    """Per-harness and per-wire validity from the final diagnostics."""
    harnesses = {h.id: h for h in project.spec.electrical.harnesses}
    wire_harness = {w.id: h.id for h in harnesses.values() for w in h.wires}
    wires: dict[str, list[dict[str, Any]]] = {w: [] for w in wire_harness}
    per_harness: dict[str, list[dict[str, Any]]] = {h: [] for h in harnesses}
    global_blockers = []
    for d in diagnostics:
        if d.code not in BLOCKING or d.severity is Severity.INFO:
            continue
        if d.severity is Severity.WARNING and d.code != "geometry_uncertain":
            continue
        item = {"code": d.code, "severity": d.severity.value, "message": d.message}
        hit_wire = [s for s in d.subjects if s in wire_harness]
        hit_harness = {s for s in d.subjects if s in harnesses} | {wire_harness[w] for w in hit_wire}
        for w in hit_wire:
            wires[w].append(item)
        for h in hit_harness:
            per_harness[h].append(item)
            if not hit_wire:
                for w in harnesses[h].wires:
                    wires[w.id].append(item)
        if not hit_harness and d.code == "cad_mesh_invalid":
            global_blockers.append(item)
    for h in harnesses:
        for item in global_blockers:
            per_harness[h].append(item)
    harness_out = {h: {"valid": not v, "violations": v} for h, v in per_harness.items()}
    wire_out = {w: {"valid": not v, "violations": v} for w, v in wires.items()}
    released = all(item["valid"] for item in harness_out.values())
    return {"status": "released" if released else "unreleased", "harnesses": harness_out, "wires": wire_out,
            "blocking_codes": sorted({i["code"] for v in per_harness.values() for i in v}),
            "rule": "a harness is released when no error-level geometry/buildability diagnostic (or "
                    "geometry_uncertain) names it or its wires"}


# ------------------------------------------------------------------ WireViz
def _iec(color: str) -> str:
    return IEC_COLORS.get(color.lower(), color[:2].upper())


def _pins(harness: Any, name: str) -> dict[int, str]:
    pins: dict[int, str] = {}
    for wire in harness.wires:
        for ref in (wire.from_, wire.to):
            if ref in harness.splices:
                continue
            conn, _, pin = ref.partition(".")
            if conn == name and pin.isdigit():
                pins[int(pin)] = wire.signal
    return pins


def wireviz_yaml(project: Any, harness: Any, cuts: dict[str, CutLength], status: str) -> dict[str, Any]:
    catalog = project.catalog
    connectors: dict[str, Any] = {}
    for name, conn in harness.connectors.items():
        pins = _pins(harness, name)
        count = max(pins) if pins else 1
        term = termination(conn.type)
        if conn.type == "integral":
            component = (conn.mates or "").split(".")[0]
            connectors[name] = {"type": f"{component} lead (integral)", "subtype": conn.mates or "",
                                "pincount": count, "pinlabels": [pins.get(i, "") for i in range(1, count + 1)],
                                "hide_disconnected_pins": True, "bgcolor": "GY",
                                "notes": "component's own lead: no termination at this end"}
            continue
        ctype = catalog.connector(conn.type)
        housing, contact = term.housing or {}, term.contact or {}
        count = max(count, int(ctype.pin_count))
        notes = [f"mates {conn.mates}" if conn.mates else "free end", f"termination: {term.method.replace('_', ' ')}"]
        if contact.get("mpn"):
            notes.append(f"contact {contact['manufacturer']} {contact['mpn']}")
        connectors[name] = {"type": ctype.family, "subtype": ctype.gender, "pincount": count,
                            "pinlabels": [pins.get(i, "") for i in range(1, count + 1)],
                            "manufacturer": housing.get("manufacturer"), "mpn": housing.get("mpn"),
                            "notes": "; ".join(notes)}
    for name in harness.splices:
        connectors[name] = {"style": "simple", "type": "Splice", "subtype": "solder + adhesive heat-shrink"}
    # Cables: wires between the same two ends form one WireViz bundle.
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for wire in harness.wires:
        a = wire.from_ if wire.from_ in harness.splices else wire.from_.split(".")[0]
        b = wire.to if wire.to in harness.splices else wire.to.split(".")[0]
        groups[(a, b)].append(wire)
    cables: dict[str, Any] = {}
    connections: list[list[Any]] = []
    for k, ((a, b), wires) in enumerate(groups.items(), start=1):
        name = f"W{k}"
        lengths = [cuts[w.id].nominal if w.id in cuts else 0.0 for w in wires]
        tol = max((cuts[w.id].tolerance_plus for w in wires if w.id in cuts), default=0.0)
        cables[name] = {"category": "bundle", "gauge": f"{wires[0].awg} AWG" if len({w.awg for w in wires}) == 1
                        else None, "length": round(max(lengths) * 1e3, 1), "length_unit": "mm",
                        "colors": [_iec(w.color) for w in wires], "wirelabels": [w.id for w in wires],
                        "type": "silicone stranded copper",
                        "notes": "cut " + ", ".join(f"{w.id} {l * 1e3:.0f}" for w, l in zip(wires, lengths))
                                 + f" mm (+{tol * 1e3:.0f}/-0)"}
        if cables[name]["gauge"] is None:
            cables[name].pop("gauge")
            cables[name]["notes"] += "; gauges " + ", ".join(f"{w.id} {w.awg} AWG" for w in wires)
        left = a if a in harness.splices else {a: [int(w.from_.split(".")[1]) for w in wires]}
        right = b if b in harness.splices else {b: [int(w.to.split(".")[1]) for w in wires]}
        connections.append([left, {name: list(range(1, len(wires) + 1))}, right])
    return {"metadata": {"title": f"{harness.id} [{status.upper()}]", "description": harness.description or "",
                         "notes": f"R3 wiring stage; release status: {status}"},
            "options": {"fontname": "arial", "bgcolor": "WH", "color_mode": "SHORT"},
            "connectors": connectors, "cables": cables, "connections": connections}


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def render_wireviz(spec: dict[str, Any], out_dir: Path, name: str) -> list[Path]:
    import yaml
    from wireviz.wireviz import parse

    out_dir.mkdir(parents=True, exist_ok=True)
    clean = _strip_none(spec)
    source = out_dir / f"{name}.wireviz.yaml"
    source.write_text(yaml.safe_dump(clean, sort_keys=False))
    parse(yaml.safe_dump(clean, sort_keys=False), output_formats=("svg", "png", "tsv"), output_dir=out_dir,
          output_name=name)
    return [source] + [out_dir / f"{name}.{ext}" for ext in ("svg", "png", "tsv") if (out_dir / f"{name}.{ext}").exists()]


# ------------------------------------------------------------------ tables
def _write(path: Path, rows: list[dict[str, Any]], status: str) -> Path:
    fields: list[str] = ["release"]
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({"release": status.upper(), **row})
    return path


def _mm(value: float) -> str:
    return f"{value * 1e3:.1f}"


def cut_list(project: Any, cuts: dict[str, CutLength], release: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for h in project.spec.electrical.harnesses:
        for wire in h.wires:
            cut = cuts.get(wire.id)
            if cut is None:
                continue
            a, b = cut.ends
            rows.append({"harness": h.id, "wire": wire.id, "valid": release["wires"][wire.id]["valid"],
                         "awg": wire.awg, "color": wire.color, "signal": wire.signal,
                         "cut_length_mm": _mm(cut.nominal), "tolerance_mm": f"+{cut.tolerance_plus * 1e3:.0f}/-0",
                         "bend_spread_mm": f"+-{cut.bend_extra * 1e3:.1f}",
                         "end_a": a["end"], "strip_a_mm": f"{a['strip'] * 1e3:.1f} +-{a['strip_tolerance'] * 1e3:.1f}",
                         "termination_a": a["method"], "contact_a": a["contact"] or "",
                         "end_b": b["end"], "strip_b_mm": f"{b['strip'] * 1e3:.1f} +-{b['strip_tolerance'] * 1e3:.1f}",
                         "termination_b": b["method"], "contact_b": b["contact"] or "",
                         "label": wire.id, "notes": "; ".join(cut.notes)})
    return rows


def bom_rows(project: Any, routing: Any, cuts: dict[str, CutLength], supports: Any, bundles: Any) -> list[dict[str, Any]]:
    catalog = project.catalog
    items: dict[tuple, dict[str, Any]] = {}

    def add(section: str, mpn: str | None, manufacturer: str, description: str, qty: float, unit: str,
            where: str, status: str = "estimated") -> None:
        key = (section, mpn or description)
        item = items.setdefault(key, {"section": section, "manufacturer": manufacturer, "mpn": mpn or "",
                                      "description": description, "quantity": 0.0, "unit": unit, "used_in": set(),
                                      "provenance": status})
        item["quantity"] += qty
        item["used_in"].add(where)

    for h in project.spec.electrical.harnesses:
        for name, conn in h.connectors.items():
            if conn.type == "integral":
                continue
            term = termination(conn.type)
            housing = term.housing or {}
            add("housing", housing.get("mpn"), housing.get("manufacturer", ""),
                housing.get("description", catalog.connector(conn.type).family), 1, "pcs", f"{h.id}.{name}",
                term.provenance.get("status", "estimated"))
        for wire in h.wires:
            cut = cuts.get(wire.id)
            if cut is None:
                continue
            gauge = catalog.wire(wire.awg)
            add("wire", None, "", f"AWG {wire.awg} silicone stranded copper, {wire.color} "
                f"(OD {gauge.outer_diameter * 1e3:.1f} mm)", cut.nominal + cut.tolerance_plus, "m", h.id)
            for end in cut.ends:
                term = termination(end["connector_type"] if end["connector_type"] != "splice" else None)
                if term.contact:
                    add("contact", term.contact.get("mpn"), term.contact.get("manufacturer", ""),
                        term.contact.get("description", ""), 1, "pcs", f"{h.id}", term.provenance.get("status", ""))
                if term.heat_shrink and term.method in ("solder_cup", "bullet_solder"):
                    add("heat_shrink", None, "", term.heat_shrink["description"],
                        float(str(term.heat_shrink["length"]).split()[0]) / 1e3, "m", h.id)
            add("label", None, "", "Heat-shrink wire marker sleeve, printed (wire id)", 2, "pcs", h.id)
        for name in h.splices:
            splice = termination(None)
            add("splice", None, "", splice.housing["description"] if splice.housing else "splice", 1, "pcs",
                f"{h.id}.{name}")
        add("label", None, "", "Harness flag label (harness id)", 1, "pcs", h.id)
    for s in supports.supports:
        t = s.type
        add("support", t.mpn, t.manufacturer, t.description, 1, "pcs", s.id, t.provenance.get("status", ""))
        if t.tie and t.kind != "post_tie":
            add("tie", t.tie["mpn"], t.tie["manufacturer"], t.tie["description"], 1, "pcs", s.id,
                t.tie.get("provenance", {}).get("status", ""))
        if t.fastener:
            add("fastener", None, "", t.fastener["screw"], 1, "pcs", s.id)
            add("fastener", None, "", t.fastener["nut"], 1, "pcs", s.id)
            add("machining", None, "", f"Drill {t.fastener['hole']} hole in {s.host}", 1, "pcs", s.id)
    for hole in routing.holes:
        if getattr(hole, "kind", "drilled") == "cad_slot":
            add("grommet", None, "", f"Grommet edge strip for the {hole.label} lead slot in {hole.plate}", 1, "pcs",
                f"hole{hole.id}")
            continue
        add("grommet", None, "", f"Grommet / edge strip for a {hole.diameter * 1e3:.1f} mm pass-through in {hole.plate}",
            1, "pcs", f"hole{hole.id}")
        add("machining", None, "", f"Drill {hole.diameter * 1e3 + 2:.1f} mm hole in {hole.plate} (grommet seat)", 1,
            "pcs", f"hole{hole.id}")
    for edge in bundles.edges:
        add("edge_protection", None, "", edge["protection"]["description"].split(",")[0] + " (spiral wrap)",
            edge["protection"]["length"], "m", f"{edge['harness']}@{edge['component']}")
    rows = []
    for item in items.values():
        qty = item["quantity"]
        rows.append({**item, "quantity": f"{qty:.3f}" if item["unit"] == "m" else f"{qty:.0f}",
                     "used_in": ";".join(sorted(item["used_in"]))})
    order = ["housing", "contact", "wire", "splice", "heat_shrink", "support", "tie", "fastener", "grommet",
             "edge_protection", "label", "machining"]
    rows.sort(key=lambda r: (order.index(r["section"]) if r["section"] in order else 99, r["mpn"], r["description"]))
    return rows


def tooling_rows(project: Any) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for h in project.spec.electrical.harnesses:
        types = [c.type for c in h.connectors.values()] + ([None] if h.splices else [])
        for ctype in types:
            term = termination(ctype)
            if not term.tool:
                continue
            key = term.tool.get("mpn") or term.tool.get("description")
            row = seen.setdefault(key, {"tool": term.tool.get("description", ""), "mpn": term.tool.get("mpn") or "",
                                        "manufacturer": term.tool.get("manufacturer", ""), "for": set(),
                                        "method": term.method})
            row["for"].add(ctype or "splice")
    return [{**r, "for": ";".join(sorted(r["for"]))} for r in seen.values()]


def label_rows(project: Any) -> list[dict[str, Any]]:
    rows = []
    for h in project.spec.electrical.harnesses:
        for name, conn in h.connectors.items():
            rows.append({"harness": h.id, "item": f"{h.id}.{name}", "text": f"{h.id}/{name}",
                         "location": "on the bundle, 20 mm behind the connector", "kind": "harness flag"})
        for wire in h.wires:
            for end in (wire.from_, wire.to):
                rows.append({"harness": h.id, "item": f"{wire.id}@{end}", "text": wire.id,
                             "location": f"10 mm from the {end} termination", "kind": "wire marker"})
    return rows


def branch_rows(project: Any, routing: Any) -> list[dict[str, Any]]:
    """Formboard dimensions: every bundle segment with its ends and length."""
    from .geometry import polyline_length

    rows = []
    for harness_id, route in routing.harnesses.items():
        for k, seg in enumerate(route.segments):
            pts = seg["points"]
            rows.append({"harness": harness_id, "segment": k, "wires": ";".join(seg["wires"]),
                         "conductors": seg["conductors"], "bundle_od_mm": f"{seg['od'] * 1e3:.1f}",
                         "length_mm": f"{polyline_length(pts) * 1e3:.1f}",
                         "start_xyz_mm": " ".join(f"{c * 1e3:.1f}" for c in pts[0]),
                         "end_xyz_mm": " ".join(f"{c * 1e3:.1f}" for c in pts[-1])})
    return rows


def continuity_rows(project: Any, routing: Any, cuts: dict[str, CutLength]) -> list[dict[str, Any]]:
    """Expected continuity (with the maximum resistance) and isolation between the
    test points of each harness; splices join conductors into nets."""
    catalog = project.catalog
    rows = []
    for h in project.spec.electrical.harnesses:
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        resistance: dict[tuple[str, str], float] = {}
        points = set()
        for wire in h.wires:
            a = wire.from_ if wire.from_ in h.splices else f"{h.id}.{wire.from_}"
            b = wire.to if wire.to in h.splices else f"{h.id}.{wire.to}"
            parent[find(a)] = find(b)
            length = cuts[wire.id].nominal if wire.id in cuts else 0.1
            resistance[(a, b)] = float(catalog.wire(wire.awg).resistance_per_m) * length
            for ref, point in ((wire.from_, a), (wire.to, b)):
                if ref not in h.splices:
                    points.add(point)
        nets: dict[str, list[str]] = defaultdict(list)
        for p in sorted(points):
            nets[find(p)].append(p)
        ordered = sorted(nets.values(), key=lambda v: v[0])
        for net in ordered:
            for i in range(len(net)):
                for j in range(i + 1, len(net)):
                    path_r = _path_resistance(net[i], net[j], resistance)
                    rows.append({"harness": h.id, "from": net[i], "to": net[j], "expect": "continuity",
                                 "max_resistance_ohm": f"{path_r * 1.2 + FIXTURE_RESISTANCE:.3f}",
                                 "nominal_resistance_ohm": f"{path_r:.4f}", "note": _note(h, net[i], net[j])})
        for x in range(len(ordered)):
            for y in range(x + 1, len(ordered)):
                rows.append({"harness": h.id, "from": ordered[x][0], "to": ordered[y][0], "expect": "isolation",
                             "max_resistance_ohm": "", "nominal_resistance_ohm": "", "note": ISOLATION})
    return rows


def _note(h: Any, a: str, b: str) -> str:
    notes = []
    for ref in (a, b):
        name = ref.split(".")[1] if "." in ref else ref
        conn = h.connectors.get(name)
        if conn is not None and conn.type == "integral":
            notes.append(f"{ref}: probe at the component pin {conn.mates}")
    return "; ".join(notes)


def _path_resistance(a: str, b: str, edges: dict[tuple[str, str], float]) -> float:
    """Shortest resistive path through the harness (wires and splices)."""
    graph: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (u, v), r in edges.items():
        graph[u].append((v, r))
        graph[v].append((u, r))
    import heapq

    best = {a: 0.0}
    heap = [(0.0, a)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == b:
            return d
        if d > best.get(u, math.inf):
            continue
        for v, r in graph[u]:
            if d + r < best.get(v, math.inf):
                best[v] = d + r
                heapq.heappush(heap, (d + r, v))
    return math.nan


def assembly_sequence(project: Any, cuts: dict[str, CutLength], supports: Any, status: str) -> str:
    lines = [f"# Harness assembly sequence [{status.upper()}]", ""]
    if status != "released":
        lines += ["**UNRELEASED**: geometry checks fail for at least one harness (see manifest.json). Do not cut "
                  "wire from this package until it is released.", ""]
    for h in project.spec.electrical.harnesses:
        lines.append(f"## {h.id}")
        step = 1
        lines.append(f"{step}. Cut conductors (cut list `cut_list.csv`, tolerance +X/-0):")
        for wire in h.wires:
            if wire.id in cuts:
                c = cuts[wire.id]
                lines.append(f"   - {wire.id}: AWG {wire.awg} {wire.color}, {c.nominal * 1e3:.0f} mm "
                             f"(+{c.tolerance_plus * 1e3:.0f}/-0)")
        step += 1
        lines.append(f"{step}. Slide wire markers ({', '.join(w.id for w in h.wires)}) and any heat-shrink onto "
                     f"the conductors before terminating.")
        step += 1
        lines.append(f"{step}. Strip each end to the strip length in the cut list; inspect for nicked strands.")
        for name, conn in h.connectors.items():
            if conn.type == "integral":
                continue
            term = termination(conn.type)
            step += 1
            tool = (term.tool or {}).get("mpn") or (term.tool or {}).get("description", "")
            if term.method == "crimp":
                lines.append(f"{step}. {name} ({(term.housing or {}).get('mpn')}): crimp "
                             f"{(term.contact or {}).get('mpn')} contacts with {tool}; pull-test one sample per reel "
                             f"lot; insert into the housing per the pin map in the drawing until the latch clicks.")
            else:
                lines.append(f"{step}. {name} ({(term.housing or {}).get('mpn') or conn.type}): tin conductors, "
                             f"solder into the cups/bullets ({tool}); shrink the sleeves.")
        for name in h.splices:
            step += 1
            wires = [w.id for w in h.wires if name in (w.from_, w.to)]
            lines.append(f"{step}. Splice {name}: join {', '.join(wires)} (solder), cover with adhesive-lined "
                         f"3:1 heat-shrink.")
        step += 1
        lines.append(f"{step}. Lay the harness on the formboard per `branch_dimensions.csv`; apply the harness flag "
                     f"label.")
        mine = [s for s in supports.supports if s.harness == h.id]
        if mine:
            step += 1
            lines.append(f"{step}. Install supports on the robot ({len(mine)}): " + "; ".join(
                f"{s.id} {s.type.mpn or s.type.id} on {s.host}" for s in mine) + ". Clean adhesive faces with IPA; "
                "fasten screwed mounts before routing the bundle.")
        step += 1
        lines.append(f"{step}. Continuity and isolation test per `continuity_test.csv` before installation.")
        lines.append("")
    return "\n".join(lines)


def write_package(project: Any, routing: Any, cuts: dict[str, CutLength], supports: Any, bundles: Any,
                  release: dict[str, Any], out_dir: Path, render: bool = True) -> dict[str, Any]:
    """Write the manufacturing package; returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    status = release["status"]
    files: list[str] = []
    errors: list[str] = []
    if render:
        for h in project.spec.electrical.harnesses:
            harness_status = "released" if release["harnesses"][h.id]["valid"] else "unreleased"
            spec = wireviz_yaml(project, h, cuts, harness_status)
            try:
                files += [p.name for p in render_wireviz(spec, out_dir, h.id)]
            except Exception as exc:  # noqa: BLE001 - keep the rest of the package
                errors.append(f"{h.id}: WireViz failed: {exc}")
    files.append(_write(out_dir / "bom.csv", bom_rows(project, routing, cuts, supports, bundles), status).name)
    files.append(_write(out_dir / "cut_list.csv", cut_list(project, cuts, release), status).name)
    files.append(_write(out_dir / "tooling.csv", tooling_rows(project), status).name)
    files.append(_write(out_dir / "labels.csv", label_rows(project), status).name)
    files.append(_write(out_dir / "branch_dimensions.csv", branch_rows(project, routing), status).name)
    files.append(_write(out_dir / "continuity_test.csv", continuity_rows(project, routing, cuts), status).name)
    (out_dir / "assembly_sequence.md").write_text(assembly_sequence(project, cuts, supports, status))
    files.append("assembly_sequence.md")
    manifest = {"status": status, "blocking_codes": release["blocking_codes"],
                "harnesses": {h: {"valid": v["valid"], "violations": len(v["violations"]),
                                  "codes": sorted({i["code"] for i in v["violations"]})}
                              for h, v in release["harnesses"].items()},
                "files": sorted(set(files)), "errors": errors, "rule": release["rule"]}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


__all__ = ["BLOCKING", "assembly_sequence", "bom_rows", "continuity_rows", "cut_list", "release_status",
           "render_wireviz", "wireviz_yaml", "write_package"]
