"""Board notes derived exclusively from the declared intent and measured results."""
from __future__ import annotations
from typing import Any
from .intent import BoardIntent
from .regulators import BuckBoostDesign, design_notes_markdown, buck_boost_notes_markdown


def design_notes(intent: BoardIntent, power: dict[str, Any], erc: dict[str, Any],
                 drc: dict[str, Any], pours: list[dict[str, Any]]) -> str:
    rows = [f"# {intent.title}", "", "Generated from the project’s board intent and KiCad outputs.", "",
            f"Board: {intent.width:g} × {intent.height:g} mm; {intent.thickness:g} mm laminate; {intent.copper_oz:g} oz copper.",
            f"ERC errors: {erc.get('errors')}; DRC errors: {drc.get('errors')}; unconnected: {drc.get('unconnected')}.", ""]
    rows.extend(intent.notes)
    for key, design in intent.regulators.items():
        render = buck_boost_notes_markdown if isinstance(design, BuckBoostDesign) else design_notes_markdown
        rows.append(render(design, key))
    return "\n".join(rows) + "\n"
