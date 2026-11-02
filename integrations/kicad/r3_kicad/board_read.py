"""Typed view of a saved board, read back through KiCad's own engine
(``kicad_scripts/board_dump.py`` running pcbnew). Coordinates are board-local
millimetres relative to the outline's top-left corner."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .engine import kicad_python
from .netlist import net_name


@dataclass(frozen=True)
class BoardPad:
    ref: str
    number: str
    net: str
    x: float
    y: float
    layers: tuple[str, ...]
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1


@dataclass(frozen=True)
class BoardFootprint:
    ref: str
    lib_id: str
    x: float
    y: float
    rotation: float
    layer: str


@dataclass(frozen=True)
class Segment:
    net: str
    layer: str
    width: float
    start: tuple[float, float]
    end: tuple[float, float]
    locked: bool

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)


@dataclass(frozen=True)
class BoardVia:
    net: str
    x: float
    y: float
    diameter: float
    drill: float


@dataclass(frozen=True)
class ZoneFill:
    net: str
    layer: str
    name: str
    area: float            # mm^2 of filled copper
    # Filled polygons (power pours only): each a list of rings, outline first, then holes; points (x, y) mm.
    polygons: tuple = ()


@dataclass
class BoardFile:
    size: tuple[float, float]
    footprints: list[BoardFootprint] = field(default_factory=list)
    pads: list[BoardPad] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    vias: list[BoardVia] = field(default_factory=list)
    zones: list[ZoneFill] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)


def parse_dump(data: dict) -> BoardFile:
    out = BoardFile(size=tuple(data["size"]), unsupported=data.get("unsupported", []))  # type: ignore[arg-type]
    for fp in data["footprints"]:
        out.footprints.append(BoardFootprint(fp["ref"], fp["lib_id"], *fp["position"], fp["rotation"], fp["layer"]))
    for pad in data["pads"]:
        bbox = tuple(pad["bbox"]) if "bbox" in pad else None
        out.pads.append(BoardPad(pad["ref"], pad["number"], net_name(pad["net"]), *pad["position"], tuple(pad["layers"]),
                                 bbox))  # type: ignore[arg-type]
    for t in data["tracks"]:
        out.segments.append(Segment(net_name(t["net"]), t["layer"], t["width"], tuple(t["start"]), tuple(t["end"]),  # type: ignore[arg-type]
                                    t["locked"]))
    for v in data["vias"]:
        out.vias.append(BoardVia(net_name(v["net"]), *v["position"], v["diameter"], v["drill"]))
    for z in data["zones"]:
        out.zones.append(ZoneFill(net_name(z["net"]), z["layer"], z["name"], z["area_mm2"],
                                  tuple(tuple(tuple(tuple(p) for p in ring) for ring in poly) for poly in z.get("polygons", []))))
    return out


def read_board(pcb: Path, work: Path) -> BoardFile:
    target = work / f"{pcb.stem}-dump.json"
    result = kicad_python("board_dump.py", str(pcb), str(target))
    if not result.ok:
        raise RuntimeError(f"pcbnew could not read {pcb}: {result.output[-1000:]}")
    return parse_dump(json.loads(target.read_text()))
