"""Arrange the schematic sheet: functional blocks in regions, symbols packed in
rows, a net label on every connected pin end and a no-connect flag on every
intentionally unused pin. Geometry comes from the official library symbols."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .intent import BoardIntent, Part
from .library import LibPin, LibSymbol, load_symbol, pin_outward, pin_position

GRID = 1.27
LABEL_CHAR = 1.0       # mm per character of a 50 mil label (KiCad stroke font, estimated)
LABEL_PAD = 1.6        # mm of label shape beyond the text


@dataclass(frozen=True)
class Group:
    title: str
    region: tuple[float, float, float, float]    # x0, y0, x1, y1 (mm, sheet)
    refs: tuple[str, ...]


@dataclass(frozen=True)
class LabelPlan:
    net: str
    x: float
    y: float
    angle: int          # direction the text runs: 0 right, 90 up, 180 left, 270 down
    ref: str
    pins: tuple[str, ...]


@dataclass(frozen=True)
class SymbolPlan:
    ref: str
    lib_id: str
    x: float
    y: float
    rotation: int
    box: tuple[float, float, float, float]      # occupied area including labels and fields


@dataclass
class SheetPlan:
    symbols: list[SymbolPlan] = field(default_factory=list)
    labels: list[LabelPlan] = field(default_factory=list)
    no_connects: list[tuple[float, float, str]] = field(default_factory=list)
    titles: list[tuple[str, float, float]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def symbol(self, ref: str) -> SymbolPlan:
        return next(s for s in self.symbols if s.ref == ref)


def snap(v: float, grid: float = GRID) -> float:
    return round(round(v / grid) * grid, 4)


def _graphics_points(lib_id: str) -> list[tuple[float, float]]:
    """Body outline estimate (library units, y up): the inner end of every pin, padded
    by half a grid so two-pin parts get a body width."""
    pts: list[tuple[float, float]] = []
    for pin in load_symbol(lib_id).pins:
        a = math.radians(pin.angle)
        ix, iy = pin.x + pin.length * math.cos(a), pin.y + pin.length * math.sin(a)
        pts += [(ix - 1.27, iy - 1.27), (ix + 1.27, iy + 1.27)]
    return pts


def _rotate(vx: float, vy: float, rotation: int) -> tuple[float, float]:
    a = math.radians(rotation)
    return (vx * math.cos(a) + vy * math.sin(a), -vx * math.sin(a) + vy * math.cos(a))


def label_box(net: str, x: float, y: float, angle: int) -> tuple[float, float, float, float]:
    length = LABEL_CHAR * len(net) + LABEL_PAD
    half = 0.9
    if angle == 0:
        return (x, y - half, x + length, y + half)
    if angle == 180:
        return (x - length, y - half, x, y + half)
    if angle == 90:
        return (x - half, y - length, x + half, y)
    return (x - half, y, x + half, y + length)


def _labels_for(part: Part, symbol: LibSymbol, at: tuple[float, float], rotation: int) -> list[LabelPlan]:
    """One label per connected pin position (stacked hidden pins share one)."""
    by_pos: dict[tuple[float, float], list[LibPin]] = {}
    for pin in symbol.pins:
        if pin.number in part.pins:
            by_pos.setdefault(pin_position(pin, at, rotation), []).append(pin)
    labels = []
    for (x, y), pins in by_pos.items():
        nets = {part.pins[p.number] for p in pins}
        if len(nets) > 1:
            raise ValueError(f"{part.ref}: stacked pins {[p.number for p in pins]} carry different nets {nets}")
        angle = int(round(pin_outward(pins[0], rotation))) % 360
        labels.append(LabelPlan(nets.pop(), x, y, angle, part.ref, tuple(sorted(p.number for p in pins))))
    return labels


def _no_connects_for(part: Part, symbol: LibSymbol, at: tuple[float, float], rotation: int) -> list[tuple[float, float, str]]:
    out: dict[tuple[float, float], str] = {}
    for pin in symbol.pins:
        if pin.number in part.no_connect and pin.etype != "no_connect":
            out.setdefault(pin_position(pin, at, rotation), f"{part.ref}.{pin.number}")
    return [(x, y, name) for (x, y), name in out.items()]


def _extent(part: Part, rotation: int) -> tuple[float, float, float, float]:
    """Bounding box (relative to the symbol origin, sheet axes) of body, pins and labels."""
    symbol = load_symbol(part.lib_id)
    pts = [_rotate(px, -py, rotation) for px, py in _graphics_points(part.lib_id)]
    for pin in symbol.pins:
        pts.append(pin_position(pin, (0.0, 0.0), rotation))
    boxes = [label_box(lab.net, lab.x, lab.y, lab.angle) for lab in _labels_for(part, symbol, (0.0, 0.0), rotation)]
    xs = [p[0] for p in pts] + [b[0] for b in boxes] + [b[2] for b in boxes]
    ys = [p[1] for p in pts] + [b[1] for b in boxes] + [b[3] for b in boxes]
    if not xs:
        xs, ys = [-2.54, 2.54], [-2.54, 2.54]
    # Reference / value text sits beside the body; reserve a strip on the right.
    return (min(xs) - 1.0, min(ys) - 1.5, max(xs) + 6.0, max(ys) + 1.5)


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def plan_sheet(intent: BoardIntent, groups: list[Group], rotations: dict[str, int] | None = None,
               gap: float = 3.81) -> SheetPlan:
    rotations = rotations or {}
    plan = SheetPlan()
    placed: set[str] = set()
    for group in groups:
        x0, y0, x1, y1 = group.region
        plan.titles.append((group.title, snap(x0), snap(y0)))
        cx, cy, row_h = x0, y0 + 5.08, 0.0
        for ref in group.refs:
            part = intent.part(ref)
            rot = rotations.get(ref, 0)
            ex = _extent(part, rot)
            w, h = ex[2] - ex[0], ex[3] - ex[1]
            if cx + w > x1 and cx > x0:
                cx, cy, row_h = x0, cy + row_h + gap, 0.0
            ox, oy = snap(cx - ex[0], 2.54), snap(cy - ex[1], 2.54)
            box = (ox + ex[0], oy + ex[1], ox + ex[2], oy + ex[3])
            if box[3] > y1 or box[2] > x1 + 2.54:
                plan.problems.append(f"{group.title}: {ref} does not fit the region {group.region}")
            plan.symbols.append(SymbolPlan(ref, part.lib_id, ox, oy, rot, box))
            placed.add(ref)
            cx = box[2] + gap
            row_h = max(row_h, box[3] - cy)
    missing = [p.ref for p in intent.parts if p.ref not in placed]
    if missing:
        plan.problems.append(f"parts not in any schematic group: {missing}")
    for sp in plan.symbols:
        part = intent.part(sp.ref)
        symbol = load_symbol(part.lib_id)
        plan.labels += _labels_for(part, symbol, (sp.x, sp.y), sp.rotation)
        plan.no_connects += _no_connects_for(part, symbol, (sp.x, sp.y), sp.rotation)
    for i, a in enumerate(plan.symbols):
        for b in plan.symbols[i + 1:]:
            if _overlap(a.box, b.box):
                plan.problems.append(f"{a.ref} overlaps {b.ref}")
    # Connection points of different parts must never coincide (that would short two nets).
    points: dict[tuple[float, float], str] = {}
    for lab in plan.labels:
        key = (round(lab.x, 3), round(lab.y, 3))
        if key in points and points[key] != lab.ref:
            plan.problems.append(f"pin ends of {points[key]} and {lab.ref} coincide at {key}")
        points[key] = lab.ref
    return plan
