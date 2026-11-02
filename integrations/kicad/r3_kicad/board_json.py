"""``build/pcb/<board>/board.json`` (r3-pipeline.md) and the catalog consistency check.

Everything here is computed from what KiCad produced: its netlist export and the
saved board. Units are SI (m, A, ohm)."""

from __future__ import annotations

import math
import re
from typing import Any

from r3_model.diagnostics import Diagnostic

from . import ipc2152
from .board_read import BoardFile
from .intent import BoardIntent
from .netlist import Netlist

MM = 1e-3


def _ref_key(text: str) -> tuple[str, int, str]:
    m = re.match(r"([A-Za-z_#]+)(\d+)(.*)", text)
    return (m.group(1), int(m.group(2)), m.group(3)) if m else (text, 0, "")


def pin_label(ref: str, pin: str, function: str, connector_ids: dict[str, str]) -> str:
    if ref in connector_ids:
        return f"{connector_ids[ref]}.{pin}"
    name = function.strip("~{}") if function else ""
    if name and not re.fullmatch(r"Pin_\d+|\d+|~|", name) and not name.startswith(("NC", "Pin")):
        return f"{ref}.{name}"
    return f"{ref}.{pin}"


def nets_map(netlist: Netlist, intent: BoardIntent) -> dict[str, list[str]]:
    connector_ids = {p.ref: p.connector for p in intent.parts if p.connector}
    out: dict[str, set[str]] = {}
    for net, nodes in netlist.nets.items():
        if net.startswith("unconnected-"):
            continue
        for ref, pin, function in nodes:
            if ref.startswith("#"):
                continue
            out.setdefault(net, set()).add(pin_label(ref, pin, function, connector_ids))
    return {net: sorted(members, key=_ref_key) for net, members in sorted(out.items())}


def connectors_map(netlist: Netlist, intent: BoardIntent) -> dict[str, dict[str, str]]:
    pin_nets = netlist.pin_nets()
    out: dict[str, dict[str, str]] = {}
    for part in sorted(intent.parts, key=lambda p: p.connector):
        if not part.connector:
            continue
        pins = {pin: net for (ref, pin), net in pin_nets.items() if ref == part.ref and not net.startswith("unconnected-")}
        out[part.connector] = {k: pins[k] for k in sorted(pins, key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, s))
                               if k.isdigit()}
    return out


def traces(board: BoardFile, copper_oz: float, delta_t: float = 10.0, delta_t_power: float = 20.0) -> list[dict[str, Any]]:
    """Every copper segment with its IPC-2152 continuous rating: hand-routed (locked) power copper at the
    power rise it was sized for, autorouted copper at the signal rise."""
    out = []
    for seg in board.segments:
        rise = delta_t_power if seg.locked else delta_t
        out.append({
            "net": seg.net, "layer": seg.layer, "width": round(seg.width * MM, 6), "length": round(seg.length * MM, 6),
            "start": [round(seg.start[0] * MM, 6), round(seg.start[1] * MM, 6)],
            "end": [round(seg.end[0] * MM, 6), round(seg.end[1] * MM, 6)],
            # IPC-2152 continuous rating of this segment at the stated rise (external layer, no plane modifier)
            "max_current": round(ipc2152.max_current(seg.width, copper_oz, rise), 3),
            "delta_t": rise,
            "resistance": round(ipc2152.trace_resistance(seg.length, seg.width, copper_oz), 6),
            "origin": "deliberate" if seg.locked else "freerouting",
        })
    return out


def power_summary(board: BoardFile, intent: BoardIntent, trace_list: list[dict], delta_t: float) -> dict[str, Any]:
    """Per power net: pour area per layer, via count/capacity, narrowest deliberate track."""
    out: dict[str, Any] = {}
    via_i = {}
    for net, current in sorted(intent.net_currents.items()):
        vias = [v for v in board.vias if v.net == net]
        capacity = sum(via_i.setdefault(v.drill, ipc2152.via_max_current(v.drill)) for v in vias)
        pours: dict[str, float] = {}
        for zone in board.zones:
            if zone.net == net:
                pours[zone.layer] = round(pours.get(zone.layer, 0.0) + zone.area, 2)
        entry: dict[str, Any] = {"design_current": current, "vias": len(vias), "via_capacity": round(capacity, 2),
                                 "pour_area_mm2": pours,
                                 "required_width_mm": round(ipc2152.required_width(current, intent.copper_oz, delta_t), 3)}
        deliberate = [t for t in trace_list if t["net"] == net and t["origin"] == "deliberate" and t["length"] > 0.002]
        if deliberate:
            narrow = min(deliberate, key=lambda t: t["width"])
            entry.update({"narrowest_deliberate_width": narrow["width"],
                          "narrowest_deliberate_rating": round(ipc2152.max_current(narrow["width"] / MM, intent.copper_oz, delta_t), 3)})
        out[net] = entry
    return out


def _catalog_pad_centroid(cat, catalog) -> tuple[float, float] | None:
    """Pad centroid predicted by the catalog connector pose and its type's pin map
    (``r3_catalog`` connector geometry), in the component frame."""
    geometry = catalog.connectors[cat.type].geometry if catalog is not None and cat.type in catalog.connectors else None
    if geometry is None:
        return None
    pads = [p.pad for p in geometry.pins.values() if p.pad is not None]
    if not pads:
        return None
    from r3_connectors.pins import instance_frame

    frame = instance_frame(cat)
    points = [frame.point(pad) for pad in pads]
    return (sum(q[0] for q in points) / len(points), sum(q[1] for q in points) / len(points))


def connector_geometry(board: BoardFile, intent: BoardIntent, catalog_component, catalog=None) -> dict[str, Any]:
    """Pad centroid of each catalog connector in the component frame, from KiCad's pad positions, against
    the catalog: the pads predicted by the connector pose and pin map when the type has geometry
    (``catalog_pad_centroid``), else the connector position."""
    out: dict[str, Any] = {}
    by_ref = {f.ref: f for f in board.footprints}
    for part in intent.parts:
        if not part.connector or part.ref not in by_ref:
            continue
        pads = [p for p in board.pads if p.ref == part.ref and p.number.isdigit()]
        if not pads:
            continue
        cx = sum(p.x for p in pads) / len(pads)
        cy = sum(p.y for p in pads) / len(pads)
        hx, hy = intent.to_frame(cx, cy)
        entry: dict[str, Any] = {"ref": part.ref, "pad_centroid": [hx, hy], "side": by_ref[part.ref].layer}
        if catalog_component is not None and part.connector in catalog_component.connectors:
            cat = catalog_component.connectors[part.connector]
            entry["catalog_position"] = list(cat.position)
            predicted = _catalog_pad_centroid(cat, catalog)
            reference = predicted if predicted is not None else cat.position[:2]
            if predicted is not None:
                entry["catalog_pad_centroid"] = [round(predicted[0], 6), round(predicted[1], 6)]
            entry["deviation_xy"] = round(math.dist((hx, hy), reference), 5)
        out[part.connector] = entry
    return out


def catalog_check(component_id: str, catalog_component, connectors: dict[str, dict[str, str]],
                  nets: dict[str, list[str]], intent: BoardIntent) -> list[Diagnostic]:
    """Connector pin->net maps must equal the catalog component entry."""
    diags: list[Diagnostic] = []
    name = intent.name
    if catalog_component is None:
        return [Diagnostic.error("board_catalog_mismatch", f"catalog has no component {intent.catalog_type!r}",
                                 subjects=[component_id], data={"kind": "missing_component"})]
    for cid, conn in catalog_component.connectors.items():
        board_pins = connectors.get(cid)
        if board_pins is None:
            diags.append(Diagnostic.error("board_catalog_mismatch", f"catalog connector {cid} is not on the board",
                                          subjects=[component_id, cid], data={"kind": "missing_connector"}))
            continue
        # Catalog pins with role "nc" must be left unconnected on the board.
        expected = {pin: net for pin, net in conn.pins.items()
                    if not (net in catalog_component.pins and catalog_component.pins[net].role == "nc")}
        if board_pins != expected:
            diff = {pin: {"catalog": expected.get(pin), "board": board_pins.get(pin)}
                    for pin in sorted(set(board_pins) | set(expected), key=lambda p: (len(p), p))
                    if expected.get(pin) != board_pins.get(pin)}
            diags.append(Diagnostic.error("board_catalog_mismatch", f"{cid} pin->net map differs from catalog {intent.catalog_type}",
                                          subjects=[component_id, cid], data={"kind": "pin_map", "differences": diff}))
    for cid in connectors:
        if cid not in catalog_component.connectors:
            diags.append(Diagnostic.error("board_catalog_mismatch", f"board connector {cid} is not in the catalog entry",
                                          subjects=[component_id, cid], data={"kind": "extra_connector"}))
    for pin, spec in catalog_component.pins.items():
        if spec.role != "nc" and pin not in nets:
            diags.append(Diagnostic.error("board_catalog_mismatch", f"catalog pin {pin} has no net on the board",
                                          subjects=[component_id, pin], data={"kind": "missing_net"}))
    for link in catalog_component.internal_links:
        if link.kind == "series_resistor":
            parts = [p for p in intent.parts if p.lib_id == "Device:R" and set(p.pins.values()) == {link.a, link.b}]
            if not parts:
                diags.append(Diagnostic.error("board_catalog_mismatch", f"internal link {link.a}-{link.b} ({link.kind}) has no part",
                                              subjects=[component_id, link.a, link.b], data={"kind": "internal_link"}))
            elif not math.isfinite(_value(parts[0].value)) or abs(_value(parts[0].value) - float(link.value)) > 0.01 * float(link.value):
                diags.append(Diagnostic.error("board_catalog_mismatch", f"{parts[0].ref} is {parts[0].value}, catalog {link.value}",
                                              subjects=[component_id, parts[0].ref],
                                              data={"kind": "internal_link_value", "board": _value(parts[0].value),
                                                    "catalog": float(link.value)}))
    return diags


def _value(text: str) -> float:
    scale = {"m": 1e-3, "k": 1e3, "M": 1e6, "u": 1e-6, "n": 1e-9, "p": 1e-12}
    tokens = text.split()
    if not tokens:
        return float('nan')
    token = tokens[0]
    embedded = re.fullmatch(r'(\d+)([RrKkMmGg])(\d+)', token)
    if embedded:
        a, suffix, b = embedded.groups()
        return float(f'{a}.{b}') * {'R': 1, 'r': 1, 'k': 1e3, 'K': 1e3, 'M': 1e6,
                                   'm': 1e-3, 'G': 1e9, 'g': 1e9}[suffix]
    try:
        return float(token[:-1]) * scale[token[-1]] if token[-1] in scale else float(token)
    except (ValueError, KeyError):
        return float("nan")
