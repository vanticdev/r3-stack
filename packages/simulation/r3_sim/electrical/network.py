"""Passive conductor network derived from ``project.graph``.

Every graph node (component pin, cable-side contact, splice) is an MNA node.
Branches:

* ``wire`` — harness conductor, R = R'_20(AWG) * length * (1 + alpha (T - 20 C)).
* ``contact`` — one mated connector contact, R = catalog contact resistance.
* ``internal`` — component internal link (series resistor, shunt, switch, fuse).

Wire length priority: ``routes.json`` ``cut_length`` > authored ``Wire.length`` >
1.3 x straight-line distance between the endpoint connectors (estimate,
reported with ``wire_length_estimated``).

Thermal model per conductor (first order): C dT/dt = I^2 R(T) - dT / R_th.
For wires R_th is calibrated so that the catalog ampacity produces the catalog's
rated rise (30 K, see wires.yaml notes); for contacts so that the per-pin rating
produces a 30 K rise (the usual connector rating criterion, e.g. UL 1977).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic

COPPER_ALPHA = 0.00393  # 1/K, resistance temperature coefficient of annealed copper at 20 C
COPPER_VOLUMETRIC_HEAT = 8960.0 * 385.0  # J/(m^3 K)
SILICONE_VOLUMETRIC_HEAT = 1200.0 * 1250.0  # J/(m^3 K), estimate for silicone rubber insulation
COPPER_RESISTIVITY_20 = 1.724e-8  # ohm m
RATED_RISE_K = 30.0  # K rise at the catalog ampacity / connector rating (assumption)
LENGTH_ESTIMATE_FACTOR = 1.3
SILICONE_INSULATION_MAX_C = 150.0  # wires.yaml: silicone 150 C insulation
CONNECTOR_BURST_FACTOR = 2.0  # XT60 30/60 A and XT30 15/30 A datasheet ratio, applied when no burst rating exists
CONTACT_HEAT_CAPACITY_PER_AMP = 0.02  # J/K per A of rating; ~0.6 J/K for an XT60 contact (estimate)

LINK_DEFAULTS = {"switch": 0.005, "wire": 1e-4}


@dataclass
class Branch:
    key: str
    kind: str  # wire | contact | internal
    a: int
    b: int
    node_a: str
    node_b: str
    resistance_ref: float  # ohm at 20 C (wires) or fixed
    temperature_coefficient: float = 0.0
    thermal_resistance: float = math.inf  # K/W (whole branch)
    heat_capacity: float = 0.0  # J/K (whole branch)
    rating: float | None = None  # A continuous
    burst_rating: float | None = None  # A short-time
    meta: dict[str, Any] = field(default_factory=dict)
    index: int = -1  # into the resistor bank
    temperature_rise: float = 0.0  # K above ambient (state)

    def resistance(self, ambient_c: float) -> float:
        temperature = ambient_c + self.temperature_rise
        return self.resistance_ref * (1.0 + self.temperature_coefficient * (temperature - 20.0))

    def steady_rise(self, current: float, ambient_c: float) -> float:
        """Steady-state rise including R(T): dT = I^2 R(T) Rth, solved in closed form."""
        if not math.isfinite(self.thermal_resistance):
            return 0.0
        k = current * current * self.resistance_ref * self.thermal_resistance
        alpha = self.temperature_coefficient
        denominator = 1.0 - k * alpha
        if denominator <= 1e-6:
            return math.inf  # thermal runaway: the conductor fuses
        return k * (1.0 + alpha * (ambient_c - 20.0)) / denominator

    def advance_temperature(self, current: float, dt: float, ambient_c: float) -> None:
        if not math.isfinite(self.thermal_resistance) or self.heat_capacity <= 0.0:
            return
        power = current * current * self.resistance(ambient_c)
        tau = self.thermal_resistance * self.heat_capacity
        target = power * self.thermal_resistance
        self.temperature_rise = target + (self.temperature_rise - target) * math.exp(-dt / tau)

    def transient_rise(self, current: float, duration: float, ambient_c: float, initial_rise: float = 0.0) -> float:
        """Rise after ``duration`` s at constant ``current`` starting from ``initial_rise``."""
        steady = self.steady_rise(current, ambient_c)
        if not math.isfinite(steady):
            return math.inf
        tau = self.thermal_resistance * self.heat_capacity
        if tau <= 0 or not math.isfinite(tau):
            return steady
        return steady + (initial_rise - steady) * math.exp(-duration / tau)


def adiabatic_i2t_withstand(area_m2: float, initial_c: float, final_c: float) -> float:
    """Copper adiabatic short-circuit withstand I^2 t (A^2 s).

    From c_v A dT = I^2 rho20 (1 + alpha (T - 20)) / A dt:
    I^2 t = A^2 c_v / (rho20 alpha) * ln((1 + alpha (Tf - 20)) / (1 + alpha (Ti - 20)))."""
    ratio = (1.0 + COPPER_ALPHA * (final_c - 20.0)) / (1.0 + COPPER_ALPHA * (initial_c - 20.0))
    return area_m2 ** 2 * COPPER_VOLUMETRIC_HEAT / (COPPER_RESISTIVITY_20 * COPPER_ALPHA) * math.log(ratio)


class ConnectorPositions:
    """Robot-frame positions of every harness connector and splice.

    Mated to a component connector: that connector's position. Authored free
    end: its ``position``. Otherwise (e.g. two cable-side connectors plugged
    together in free space) the centroid of the far ends of the wires attached
    to it and to its mating partner, iterated until fixed (estimate)."""

    def __init__(self, project: Any) -> None:
        spec, graph = project.spec, project.graph
        self.known: dict[str, np.ndarray] = {}
        self.estimated: set[str] = set()
        partner: dict[str, str] = {}
        for harness in spec.electrical.harnesses:
            for name, splice in harness.splices.items():
                self.known[f"{harness.id}.{name}"] = np.asarray(splice.position, dtype=float)
            for name, connector in harness.connectors.items():
                ref = f"{harness.id}.{name}"
                if connector.mates and connector.mates in graph.endpoints:
                    self.known[ref] = graph.endpoints[connector.mates].position.copy()
                elif connector.position is not None:
                    self.known[ref] = np.asarray(connector.position, dtype=float)
                if connector.mates:
                    partner[ref] = connector.mates
                    partner.setdefault(connector.mates, ref)
        far_ends: dict[str, list[str]] = {}
        for harness in spec.electrical.harnesses:
            for wire in harness.wires:
                ends = [self._end_ref(harness, wire.from_), self._end_ref(harness, wire.to)]
                far_ends.setdefault(ends[0], []).append(ends[1])
                far_ends.setdefault(ends[1], []).append(ends[0])
        unknown = [ref for ref in far_ends if ref not in self.known]
        for _ in range(8):
            progressed = False
            for ref in unknown:
                if ref in self.known:
                    continue
                group = [ref] + ([partner[ref]] if ref in partner else [])
                anchors = [self.known[f] for member in group for f in far_ends.get(member, []) if f in self.known and f not in group]
                if anchors:
                    position = np.mean(anchors, axis=0)
                    for member in group:
                        if member not in self.known:
                            self.known[member] = position
                            self.estimated.add(member)
                    progressed = True
            if not progressed:
                break

    @staticmethod
    def _end_ref(harness: Any, end: str) -> str:
        if end in harness.splices:
            return f"{harness.id}.{end}"
        return f"{harness.id}.{end.partition('.')[0]}"

    def wire_endpoints(self, harness: Any, wire: Any) -> tuple[np.ndarray | None, np.ndarray | None]:
        return tuple(self.known.get(self._end_ref(harness, end)) for end in (wire.from_, wire.to))  # type: ignore[return-value]


@dataclass
class PassiveNetwork:
    branches: dict[str, Branch]
    wire_lengths: dict[str, dict[str, Any]]
    diagnostics: list[Diagnostic]
    internal_links: dict[str, list[Branch]]  # component id -> its internal-link branches


def _behavior_params(project: Any, component_id: str) -> dict[str, Any]:
    instance = project.spec.components[component_id]
    try:
        ctype = project.catalog.component(instance.type)
    except KeyError:
        return {}
    params = dict(ctype.behavior.params) if ctype.behavior else {}
    params.update(instance.params)
    return params


def build_passive_network(project: Any, circuit: Any, routes: dict[str, Any] | None) -> PassiveNetwork:
    from r3_model.units import to_si

    graph, catalog = project.graph, project.catalog
    harness_of_wire = {w.id: (h, w) for h in project.spec.electrical.harnesses for w in h.wires}
    routed = (routes or {}).get("wires", {})
    positions = ConnectorPositions(project)
    branches: dict[str, Branch] = {}
    lengths: dict[str, dict[str, Any]] = {}
    internal: dict[str, list[Branch]] = {}
    estimated_by_harness: dict[str, dict[str, float]] = {}
    diagnostics: list[Diagnostic] = []

    for node in sorted(graph.nodes):
        circuit.node(node)

    for edge in graph.edges:
        a, b = circuit.node(edge.a), circuit.node(edge.b)
        if edge.kind == "wire":
            harness, wire = harness_of_wire[edge.ref]
            gauge = catalog.wire(wire.awg)
            route = routed.get(wire.id, {})
            if route.get("cut_length"):
                length, source = float(route["cut_length"]), "routed"
            elif wire.length is not None:
                length, source = float(wire.length), "authored"
            else:
                pa, pb = positions.wire_endpoints(harness, wire)
                straight = float(np.linalg.norm(pa - pb)) if pa is not None and pb is not None else 0.1
                length, source = max(LENGTH_ESTIMATE_FACTOR * straight, 0.01), "estimated"
                estimated_by_harness.setdefault(harness.id, {})[wire.id] = round(length, 4)
            area = math.pi * gauge.conductor_diameter ** 2 / 4.0
            insulation = math.pi * (gauge.outer_diameter ** 2 - gauge.conductor_diameter ** 2) / 4.0
            r_per_m = gauge.resistance_per_m
            hot_r = r_per_m * (1.0 + COPPER_ALPHA * (25.0 + RATED_RISE_K - 20.0))
            rth_per_m = RATED_RISE_K / (gauge.ampacity ** 2 * hot_r)  # K m / W
            cth_per_m = COPPER_VOLUMETRIC_HEAT * area + SILICONE_VOLUMETRIC_HEAT * insulation
            branch = Branch(
                key=wire.id, kind="wire", a=a, b=b, node_a=edge.a, node_b=edge.b,
                resistance_ref=r_per_m * length, temperature_coefficient=COPPER_ALPHA,
                thermal_resistance=rth_per_m / length, heat_capacity=cth_per_m * length,
                rating=gauge.ampacity, burst_rating=None,
                meta={"harness": harness.id, "awg": wire.awg, "length": length, "length_source": source,
                      "signal": wire.signal, "area": area, "resistance_per_m": r_per_m, "ampacity": gauge.ampacity})
            lengths[wire.id] = {"length": length, "source": source}
        elif edge.kind == "mate":
            left, right = edge.ref.split("|")
            key = f"{edge.ref}#{edge.data.get('pin')}"
            rc = float(edge.data["contact_resistance"])
            rating = float(edge.data["current_rating"])
            types = [_connector_type(project, ref) for ref in (left, right)]
            burst_values = [getattr(catalog.connector(t), "current_rating_burst", None) for t in types if t]
            burst_values = [float(v) for v in burst_values if v]
            burst = min(burst_values) if burst_values else CONNECTOR_BURST_FACTOR * rating
            branch = Branch(
                key=key, kind="contact", a=a, b=b, node_a=edge.a, node_b=edge.b, resistance_ref=rc,
                thermal_resistance=RATED_RISE_K / (rating ** 2 * rc), heat_capacity=CONTACT_HEAT_CAPACITY_PER_AMP * rating,
                rating=rating, burst_rating=burst,
                meta={"connectors": [left, right], "types": types, "pin": edge.data.get("pin"),
                      "burst_source": "catalog" if burst_values else f"estimated {CONNECTOR_BURST_FACTOR}x continuous"})
        else:
            link_kind = edge.data.get("link_kind")
            if link_kind == "level_shifter":
                continue  # active signal element, no DC conduction path
            value = float(edge.data.get("value") or 0.0)
            params = _behavior_params(project, edge.ref)
            if link_kind == "fuse":
                cold = params.get("cold_resistance")
                resistance = to_si(cold) if cold is not None else 0.07 / max(value, 0.1)
            elif link_kind in ("series_resistor", "shunt"):
                resistance = value
            else:
                resistance = value or LINK_DEFAULTS.get(link_kind, 1e-3)
            pin_a, pin_b = edge.a.split(".", 1)[1], edge.b.split(".", 1)[1]
            key = f"{edge.ref}:{pin_a}-{pin_b}"
            branch = Branch(key=key, kind="internal", a=a, b=b, node_a=edge.a, node_b=edge.b, resistance_ref=resistance,
                            rating=value if link_kind == "fuse" else None,
                            meta={"component": edge.ref, "link_kind": link_kind, "value": value})
            internal.setdefault(edge.ref, []).append(branch)
        branch.index = circuit.resistor(a, b, branch.resistance_ref, branch.key)
        branches[branch.key] = branch

    for harness_id, wires in estimated_by_harness.items():
        diagnostics.append(Diagnostic.warning(
            "wire_length_estimated",
            f"harness {harness_id}: no routed cut length; using {LENGTH_ESTIMATE_FACTOR} x straight-line distance for {len(wires)} wire(s)",
            subjects=[harness_id, *wires], data={"lengths": wires, "factor": LENGTH_ESTIMATE_FACTOR},
            hint="Run the wiring stage to produce build/wiring/routes.json."))
    return PassiveNetwork(branches, lengths, diagnostics, internal)


def _connector_type(project: Any, ref: str) -> str | None:
    owner, _, name = ref.partition(".")
    if owner in project.spec.components:
        try:
            ctype = project.catalog.component(project.spec.components[owner].type)
        except KeyError:
            return None
        inst = ctype.connectors.get(name)
        return inst.type if inst else None
    for harness in project.spec.electrical.harnesses:
        if harness.id == owner and name in harness.connectors:
            return harness.connectors[name].type
    return None
