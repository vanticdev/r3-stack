"""Schematic capture without a GUI.

KiCad 10 has no headless schematic-editing API (``kicad-cli sch`` only runs ERC,
exports and upgrades), so the sheet is written with ``kicad-sch-api``, a
structured KiCad-format library that embeds the official library symbols
verbatim. It is the same set of edits a person makes in eeschema: place
symbols from the official libraries, set value/footprint/fields, attach a net
label to every connected pin end, put a no-connect flag on every unused pin and
title each functional block. ``kicad-cli sch upgrade`` then normalizes the file
with KiCad's own writer, and ``kicad-cli sch erc`` validates it.
"""

from __future__ import annotations

import uuid as uuidlib
from dataclasses import dataclass, field
from pathlib import Path

from .intent import BoardIntent
from .library import find_install, load_symbol
from .schematic_layout import Group, SheetPlan, plan_sheet

# Fields carried on every symbol (hidden on the sheet, exported to the netlist and BOM).
HIDDEN_FIELDS = ("MPN", "Manufacturer", "Description")


@dataclass
class SchematicReport:
    symbols: int = 0
    labels: int = 0
    no_connects: int = 0
    pin_mismatches: list[str] = field(default_factory=list)


def _ksa():
    install = find_install()
    if install is None:
        raise FileNotFoundError("KiCad installation not found")
    import os

    os.environ.setdefault("KICAD_SYMBOL_DIR", str(install.symbols))
    import kicad_sch_api as ksa

    return ksa


def stable_uuid(*parts: str) -> str:
    """Deterministic UUIDs keep re-generated schematics diff-friendly and keep the
    board's footprint -> symbol links (KIID paths) stable across runs."""
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, "r3/" + "/".join(parts)))


def write_schematic(intent: BoardIntent, groups: list[Group], path: Path, date: str = "") -> SchematicReport:
    ksa = _ksa()
    plan: SheetPlan = plan_sheet(intent, groups)
    if plan.problems:
        raise ValueError("schematic plan problems: " + "; ".join(plan.problems))
    report = SchematicReport()
    sch = ksa.create_schematic(intent.name)
    sch.set_paper_size(intent.sheet)
    sch.set_title_block(title=intent.title, date=date, rev="A", company="R3",
                        comments={1: "Generated headlessly by r3_kicad from the design intent",
                                  2: "Official KiCad library symbols; ERC by kicad-cli"})
    for sp in plan.symbols:
        part = intent.part(sp.ref)
        symbol = load_symbol(part.lib_id)
        footprint = part.footprint or symbol.footprint
        extra = {name: value for name, value in (("MPN", part.mpn), ("Manufacturer", part.manufacturer),
                                                 ("Description", part.description)) if value}
        component = sch.components.add(part.lib_id, reference=part.ref, value=part.value, position=(sp.x, sp.y),
                                       footprint=footprint or None, rotation=sp.rotation,
                                       component_uuid=stable_uuid(intent.name, "symbol", part.ref))
        for name, value in extra.items():
            component.set_property(name, value)
        data = component._data
        data.hidden_properties.update(n for n in HIDDEN_FIELDS if n in extra)
        if not part.in_bom:
            data.in_bom = False
        if symbol.power or part.ref.startswith("#"):
            data.in_bom = False
            data.on_board = False
        report.symbols += 1
        # The library cache and the plan must agree on every pin end the labels land on.
        for label in (lab for lab in plan.labels if lab.ref == part.ref):
            got = sch.get_component_pin_position(part.ref, label.pins[0])
            if got is None or abs(got.x - label.x) > 0.01 or abs(got.y - label.y) > 0.01:
                report.pin_mismatches.append(f"{part.ref}.{label.pins[0]} at {got}, planned ({label.x}, {label.y})")
    for title, x, y in plan.titles:
        # Sheet text is centred on its anchor; shift by half its width (~1.53 mm per 2 mm bold glyph) to left-align.
        sch.add_text(title, position=(round(x + 0.765 * len(title), 2), y + 1.27), size=2.0, bold=True)
    for label in plan.labels:
        sch.add_label(label.net, position=(label.x, label.y), rotation=label.angle)
        report.labels += 1
    for x, y, _name in plan.no_connects:
        sch.no_connects.add((x, y))
        report.no_connects += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    sch.save(str(path))
    return report
