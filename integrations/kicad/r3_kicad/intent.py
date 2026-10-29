"""Typed design intent for a board: what an engineer decides before opening KiCad.

The intent names official KiCad library symbols and footprints, the net on every
used pin, where each symbol goes on the schematic sheet and where each footprint
goes on the board, the net classes, and the power copper to be laid by hand.
KiCad turns it into a schematic, a board and fabrication data; nothing here is a
KiCad file format. All lengths are millimetres in KiCad's frame (x right, y down).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["F", "B"]


@dataclass(frozen=True)
class Placement:
    x: float
    y: float
    rotation: float = 0.0
    side: Side = "F"
    # Pads that must land on board points ("1", (x, y)): pcbnew picks the quarter turn that meets them.
    anchors: tuple[tuple[str, tuple[float, float]], ...] = ()


@dataclass(frozen=True)
class Part:
    ref: str
    lib_id: str                                   # official symbol, "Library:Name"
    value: str
    footprint: str = ""                           # official footprint, "Library:Name" ("" = symbol default)
    pins: dict[str, str] = field(default_factory=dict)   # pin number -> net
    no_connect: tuple[str, ...] = ()              # pin numbers that get a no-connect flag
    sch: tuple[float, float, int] = (0.0, 0.0, 0)  # schematic position (mm) and rotation (deg)
    pcb: Placement | None = None
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    connector: str = ""                           # catalog connector id (J_GPIO, ...)
    in_bom: bool = True
    dnp: bool = False

    @property
    def library(self) -> str:
        return self.lib_id.split(":", 1)[0]

    @property
    def symbol_name(self) -> str:
        return self.lib_id.split(":", 1)[1]


@dataclass(frozen=True)
class NetClass:
    name: str
    track_width: float
    clearance: float
    via_diameter: float
    via_drill: float
    nets: tuple[str, ...]
    design_current: float = 0.0


@dataclass(frozen=True)
class PowerTrack:
    """A deliberately routed power connection: a polyline through waypoints, each
    end snapping to a pad ("REF.PAD") or a free point. ``vias`` drops vias at the
    listed waypoints (layer change)."""

    net: str
    width: float
    points: tuple[tuple[float, float] | str, ...]
    layer: str = "F.Cu"
    current: float = 0.0


@dataclass(frozen=True)
class Via:
    net: str
    x: float
    y: float
    diameter: float = 0.6
    drill: float = 0.3


@dataclass(frozen=True)
class Zone:
    net: str
    layer: str
    polygon: tuple[tuple[float, float], ...]
    priority: int = 0
    name: str = ""
    clearance: float = 0.25
    min_thickness: float = 0.25
    thermal_gap: float = 0.3
    thermal_spoke: float = 0.4
    solid: bool = False                           # solid pad connection instead of thermal reliefs
    # Poured before autorouting (Freerouting sees it as a plane and drops vias into it) or after.
    before_routing: bool = False


@dataclass(frozen=True)
class Mounting:
    """Screw attachment through the mounting holes and the envelopes that must stay clear.

    Top: the cap-screw head, covered by a printed cap that needs ``print_clearance`` around the
    head; components stay outside head + clearance. Bottom: the metal hex standoff of the Pi HAT
    stack (no printed part), so components stay outside its across-corners circle. Copper stays
    outside the metal contact circles on both sides (solder mask alone is not trusted under a
    clamped head or standoff)."""

    screw: str = "ISO 4762 M2.5 socket head cap screw"
    drill: float = 2.7
    head_diameter: float = 4.5            # ISO 4762 M2.5 dk (max), mm
    head_height: float = 2.5              # ISO 4762 M2.5 k, mm
    standoff: str = "M2.5 x 11 mm F-F hex standoff, 5.0 mm A/F (HAT stacking height)"
    standoff_across_corners: float = 5.77  # 5.0 A/F / cos 30, mm
    standoff_height: float = 11.0
    print_clearance: float = 0.3          # FDM fit clearance per side, mm (estimate)

    @property
    def top_copper_radius(self) -> float:
        return self.head_diameter / 2

    @property
    def top_part_radius(self) -> float:
        return self.head_diameter / 2 + self.print_clearance

    @property
    def bottom_copper_radius(self) -> float:
        return self.standoff_across_corners / 2

    @property
    def bottom_part_radius(self) -> float:
        return self.standoff_across_corners / 2

    @property
    def cap_inner_diameter(self) -> float:
        """Printed cap bore over the screw head: head + clearance on each side."""
        return self.head_diameter + 2 * self.print_clearance


@dataclass
class BoardIntent:
    name: str
    title: str
    component: str                                # robot.yaml component id
    catalog_type: str
    width: float
    height: float
    corner_radius: float
    thickness: float
    copper_oz: float
    holes: tuple[tuple[float, float], ...]
    parts: list[Part]
    net_classes: list[NetClass]
    power_tracks: list[PowerTrack] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    rules: dict[str, float] = field(default_factory=dict)
    net_currents: dict[str, float] = field(default_factory=dict)
    frame_origin: tuple[float, float] = (0.0, 0.0)   # board point of the component frame origin
    sheet: str = "A3"
    origin: tuple[float, float] = (100.0, 60.0)      # board top-left corner on the KiCad page
    notes: list[str] = field(default_factory=list)
    mounting: Mounting = field(default_factory=Mounting)

    def part(self, ref: str) -> Part:
        for part in self.parts:
            if part.ref == ref:
                return part
        raise KeyError(ref)

    def nets(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for part in self.parts:
            for pin, net in part.pins.items():
                out.setdefault(net, []).append(f"{part.ref}.{pin}")
        return {net: sorted(members) for net, members in sorted(out.items())}

    def connectors(self) -> dict[str, Part]:
        return {part.connector: part for part in self.parts if part.connector}

    def to_frame(self, x: float, y: float) -> tuple[float, float]:
        """Board mm (y down) -> component frame metres (x right, y up)."""
        ox, oy = self.frame_origin
        return (round((x - ox) / 1000, 6), round((oy - y) / 1000, 6))

    def from_frame(self, hx: float, hy: float) -> tuple[float, float]:
        """Component frame metres -> board mm."""
        ox, oy = self.frame_origin
        return (ox + hx * 1000, oy - hy * 1000)

    def validate(self) -> list[str]:
        """Structural problems in the intent itself (duplicate refs, single-pin nets)."""
        problems = []
        refs = [p.ref for p in self.parts]
        problems += [f"duplicate reference {r}" for r in sorted({r for r in refs if refs.count(r) > 1})]
        for net, members in self.nets().items():
            if len(members) < 2:
                problems.append(f"net {net} has a single connection {members}")
        for part in self.parts:
            overlap = set(part.pins) & set(part.no_connect)
            if overlap:
                problems.append(f"{part.ref} pins {sorted(overlap)} are both connected and no-connect")
        return problems
