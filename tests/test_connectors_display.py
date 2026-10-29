"""Display paths spread conductors that share a bundle core (user report: "the wires seem to be merged")."""

from __future__ import annotations

import itertools
import math

import numpy as np

from r3_connectors.display import _project, display_paths, overlaps

OD = 1.6e-3
PITCH = 2.54e-3


def _core() -> np.ndarray:
    """Straight 50 mm, a 90 degree bend of 10 mm radius, straight 50 mm (1 mm samples)."""
    straight = np.column_stack([np.linspace(0.0, 0.05, 51), np.zeros(51), np.zeros(51)])
    angles = np.linspace(-math.pi / 2, 0.0, 17)[1:]
    bend = np.column_stack([0.05 + 0.01 * np.cos(angles), 0.01 + 0.01 * np.sin(angles), np.zeros(16)])
    rise = np.column_stack([np.full(50, 0.06), np.linspace(0.011, 0.06, 50), np.zeros(50)])
    return np.vstack([straight, bend, rise])


def _fan(pin: np.ndarray, joint: np.ndarray, count: int) -> np.ndarray:
    t = np.linspace(0.0, 1.0, count + 1)[:-1]
    s = t * t * (3 - 2 * t)
    return pin + (joint - pin) * s[:, None]


def _routes(far_order: list[int], head_counts: list[int]) -> dict:
    """Conductors sharing ``_core`` (identical vertices), fanned to pins at both ends.
    Head fan-out vertex counts differ per conductor, as the router emits them."""
    core = _core()
    wires = {}
    for i, head in enumerate(head_counts):
        near_pin = np.array([-0.012, (i - 1) * PITCH, 0.0])
        far_pin = np.array([0.06 + (far_order[i] - 1) * PITCH, 0.072, 0.0])
        tail = _fan(far_pin, core[-1], 6)[::-1]
        path = np.vstack([_fan(near_pin, core[0], head), core, tail])
        wires[f"W{i}"] = {"harness": "lead", "path": path.tolist(), "fanout": [head, len(tail)], "od": OD,
                          "ends": [{"connector": "lead.A", "pin": str(i + 1)}, {"connector": "lead.B", "pin": str(far_order[i] + 1)}]}
    return {"wires": wires}


def _core_separation(paths: dict[str, np.ndarray], routes: dict) -> float:
    worst = math.inf
    for a, b in itertools.combinations(paths, 2):
        head, tail = routes["wires"][a]["fanout"]
        core = paths[a][head: len(paths[a]) - tail]
        worst = min(worst, float(_project(core, paths[b])[0].min()))
    return worst


def test_shared_core_with_unequal_fanouts_is_spread() -> None:
    routes = _routes([0, 1, 2], [8, 7, 6])
    raw = {k: np.asarray(w["path"]) for k, w in routes["wires"].items()}
    diameters = {k: OD for k in raw}
    assert overlaps(raw, routes["wires"], diameters), "fixture must start merged"
    shown = display_paths(routes, diameters)
    assert overlaps(shown, routes["wires"], diameters) == []
    assert _core_separation(shown, routes) >= OD - 5e-5
    for wire_id, path in shown.items():  # still ends exactly on its pins
        assert np.allclose(path[[0, -1]], raw[wire_id][[0, -1]])


def test_ribbon_twists_to_meet_reversed_far_pins() -> None:
    routes = _routes([2, 1, 0], [8, 8, 8])  # far connector mounted the other way round
    diameters = {k: OD for k in routes["wires"]}
    shown = display_paths(routes, diameters)
    assert overlaps(shown, routes["wires"], diameters) == []


def test_crossover_lead_keeps_core_separate() -> None:
    routes = _routes([0, 2, 1], [8, 8, 8])  # TX/RX swap between the ends
    diameters = {k: OD for k in routes["wires"]}
    shown = display_paths(routes, diameters)
    assert _core_separation({k: shown[k] for k in ("W0", "W1")}, routes) >= OD - 5e-5
    assert all(o["fraction"] < 0.25 for o in overlaps(shown, routes["wires"], diameters))


def test_conductors_already_apart_are_untouched() -> None:
    routes = _routes([0, 1, 2], [8, 8, 8])
    for i, wire in enumerate(routes["wires"].values()):
        wire["path"] = (np.asarray(wire["path"]) + np.array([0.0, 0.0, 0.01 * i])).tolist()
    shown = display_paths(routes, {k: OD for k in routes["wires"]})
    for wire_id, wire in routes["wires"].items():
        assert np.allclose(shown[wire_id], wire["path"])
