"""Harness bill of materials: cut list per wire, connectors, splices, ties, grommets."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from r3_model.project import Project

from .router import RoutingResult

FIELDS = ["section", "harness", "item", "description", "awg", "color", "signal", "quantity", "cut_length_mm", "notes"]


def write_bom(path: Path, project: Project, routing: RoutingResult) -> list[dict[str, str]]:
    catalog = project.catalog
    rows: list[dict[str, str]] = []
    totals: dict[tuple[int, str], float] = defaultdict(float)
    for harness in project.spec.electrical.harnesses:
        for wire in harness.wires:
            route = routing.wires.get(wire.id)
            if route is None:
                continue
            totals[(wire.awg, wire.color)] += route.cut_length
            rows.append({"section": "wire", "harness": harness.id, "item": wire.id,
                         "description": f"AWG {wire.awg} silicone, {wire.from_} -> {wire.to}", "awg": str(wire.awg),
                         "color": wire.color, "signal": wire.signal, "quantity": "1",
                         "cut_length_mm": f"{route.cut_length * 1e3:.0f}",
                         "notes": f"routed {route.routed_length * 1e3:.1f} mm + slack {route.slack * 1e3:.0f} mm + "
                                  f"terminations {route.termination_allowance * 1e3:.0f} mm"
                                  + ("; ROUTE FAILED" if route.failed else "")})
        for name, conn in harness.connectors.items():
            ctype = catalog.connector(conn.type)
            integral = conn.type == "integral"
            rows.append({"section": "connector", "harness": harness.id, "item": f"{harness.id}.{name}",
                         "description": "component pigtail root (no part)" if integral else f"{ctype.family} {conn.type}",
                         "awg": f"{ctype.awg_min}-{ctype.awg_max}", "color": "", "signal": "",
                         "quantity": "0" if integral else "1", "cut_length_mm": "",
                         "notes": f"mates {conn.mates}" if conn.mates else "free end"})
        for name in harness.splices:
            rows.append({"section": "splice", "harness": harness.id, "item": f"{harness.id}.{name}",
                         "description": "solder splice with adhesive heat-shrink", "awg": "", "color": "", "signal": "",
                         "quantity": "1", "cut_length_mm": "", "notes": ""})
        route = routing.harnesses.get(harness.id)
        ties = len(route.tie_points) if route else 0
        if ties:
            rows.append({"section": "tie", "harness": harness.id, "item": f"{harness.id}.ties",
                         "description": "100 mm nylon cable tie + adhesive/screw mount", "awg": "", "color": "",
                         "signal": "", "quantity": str(ties), "cut_length_mm": "", "notes": "positions in routes.json"})
        if harness.jacket != "none" and route:
            rows.append({"section": "jacket", "harness": harness.id, "item": f"{harness.id}.jacket",
                         "description": harness.jacket, "awg": "", "color": "", "signal": "", "quantity": "1",
                         "cut_length_mm": f"{route.length * 1e3:.0f}", "notes": ""})
    for hole in routing.holes:
        rows.append({"section": "pass_through", "harness": ";".join(sorted(hole.harnesses)), "item": f"hole{hole.id}",
                     "description": f"drill {hole.diameter * 1e3:.1f} mm in {hole.plate} + grommet", "awg": "",
                     "color": "", "signal": "", "quantity": "1", "cut_length_mm": "",
                     "notes": "at " + ", ".join(f"{c * 1e3:.1f}" for c in hole.center) + " mm"})
    for (awg, color), length in sorted(totals.items()):
        rows.append({"section": "total", "harness": "", "item": f"AWG{awg}-{color}",
                     "description": f"total cut length AWG {awg} {color}", "awg": str(awg), "color": color,
                     "signal": "", "quantity": "", "cut_length_mm": f"{length * 1e3:.0f}", "notes": ""})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows
