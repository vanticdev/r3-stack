"""Serialisation of routing results: the contract ``routes.json`` and a richer detail file."""

from __future__ import annotations

from typing import Any

import numpy as np

from r3_connectors.pins import PinEnd

from .router import RoutingResult


def _pts(points: np.ndarray) -> list[list[float]]:
    return [[round(float(c), 6) for c in p] for p in np.asarray(points)]


def end_json(end: PinEnd | None) -> dict[str, Any] | None:
    if end is None:
        return None
    return {"connector": end.connector, "pin": end.pin, "kind": end.kind, "position": _pts([end.position])[0],
            "direction": _pts([end.direction])[0], "contact": _pts([end.contact])[0]}


def routes_json(result: RoutingResult, clearances: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``build/wiring/routes.json`` contract (SI units, robot frame).

    ``path`` runs from pin to pin: it starts and ends at the conductor exits of
    the connector halves (``ends``: connector, pin, exit position and direction,
    mating contact). ``fanout`` counts the leading/trailing path vertices where
    the conductor converges from its pin into the shared bundle core."""
    return {
        "wires": {w.wire_id: {"harness": w.harness, "awg": w.awg, "path": _pts(w.path),
                              "routed_length": round(w.routed_length, 6), "cut_length": round(w.cut_length, 6),
                              "bundle_radius": [round(float(r), 7) for r in w.bundle_radius],
                              "ends": [end_json(e) for e in w.ends], "fanout": list(w.fanout)}
                  for w in result.wires.values()},
        "harnesses": {h.harness: {"bundle_path": _pts(h.bundle_path), "length": round(h.length, 6),
                                  "tie_points": _pts(h.tie_points) if h.tie_points else []}
                      for h in result.harnesses.values()},
        "clearances": [{"wire": c["wire"], "component": c["component"], "min_distance": round(c["min_distance"], 6)}
                       for c in clearances],
    }


def detail_json(result: RoutingResult) -> dict[str, Any]:
    nodes = {}
    for harness, by_name in result.nodes.items():
        for name, node in by_name.items():
            nodes[f"{harness}.{name}"] = {
                "kind": node.kind, "connector_type": node.connector_type, "mates": node.mates,
                "component": node.component, "body": node.body,
                "exit": None if node.exit is None else _pts([node.exit])[0],
                "direction": None if node.direction is None else _pts([node.direction])[0],
                "lead_length": round(node.lead_length, 6), "bundle_od": round(node.od, 6),
                "frame": None if node.frame is None else node.frame.to_json(),
                "fan_length": round(node.fan_length, 6)}
    return {
        "wires": {w.wire_id: {"harness": w.harness, "color": w.color, "signal": w.signal, "from": w.from_node, "to": w.to_node,
                              "od": w.od, "slack": w.slack, "termination_allowance": round(w.termination_allowance, 6),
                              "spec_length": w.spec_length, "failed": w.failed,
                              "min_bend_radius": round(min((b.radius for b in w.bends), default=float("inf")), 6),
                              "bends": [{"position": _pts([b.position])[0], "radius": round(b.radius, 6),
                                         "required": round(b.required, 6), "conductors": b.conductors}
                                        for b in w.bends if b.radius < 4 * b.required]}
                  for w in result.wires.values()},
        "harness_segments": {h.harness: [{"points": _pts(s["points"]), "wires": s["wires"], "bundle_od": round(s["od"], 6),
                                          "conductors": s["conductors"], "min_bend_radius": round(s["min_bend_radius"], 6)}
                                         for s in h.segments] for h in result.harnesses.values()},
        "nodes": nodes,
        "floating_mates": [{"a": f.a, "b": f.b, "types": [f.type_a, f.type_b], "center": _pts([f.center])[0],
                            "axis": _pts([f.axis])[0], "length": round(f.length, 6)} for f in result.floating],
        "pass_throughs": [{"id": h.id, "plate": h.plate, "center": _pts([h.center])[0], "axis": "xyz"[h.axis],
                           "diameter": round(h.diameter, 6), "harnesses": sorted(h.harnesses)} for h in result.holes],
        "stats": result.stats,
    }
