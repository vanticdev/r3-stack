"""``ElectricalSystem``: the electrical digital twin of a robot project.

Built generically from ``project.graph`` (nodes, wires, contacts, internal
links) plus the catalog behavior model of every component. Each ``step`` solves
the nonlinear DC network by MNA/Newton (warm-started), integrates component
states (SOC, RC polarization, inductor current, temperatures, fuse melting
integral, brownout/boot timers) and returns outputs keyed by component id::

    system = ElectricalSystem(project)            # routes.json read if present
    out = system.step(0.001, {"esc": {"pulse_us": 1600.0}, "motor": {"omega": 310.0},
                              "servo": {"pulse_us": 1500.0, "horn_torque": 0.2, "horn_speed": 0.5},
                              "pi": {"activity": 0.6}})
    out["motor"]["torque"], out["battery"]["v_terminal"], out["pi"]["powered"]
    out["_nets"]["5V"], out["_branches"]["W_Y_P_IN"]["current"], out["_events"]

Fault injection (generic): ``inputs["_faults"] = [{"type": "short", "a": "hat.VBAT",
"b": "GND", "resistance": 0.05}, {"type": "open", "branch": "W_Y_P_HAT"}]``;
endpoints are ``<component>.<pin>`` refs, graph node ids or net names. Faults
persist until replaced by a later ``_faults`` list.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic

from .mna import DEFAULT_GMIN, GROUND, Circuit, SolveInfo
from .models import MODELS, NON_ELECTRICAL, BehaviorModel, BuildContext, Event
from .models.fuse import OPEN_RESISTANCE, FuseBlade
from .network import Branch, build_passive_network

MAX_ACTIVE_SET_PASSES = 8


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class ElectricalSystem:
    def __init__(self, project: Any, routes: dict[str, Any] | str | Path | None = None, *,
                 initial: dict[str, Any] | None = None, ambient_c: float | None = None,
                 gmin: float = DEFAULT_GMIN) -> None:
        self.project = project
        if routes is None:
            routes = project.read_build_json("wiring/routes.json")
        elif isinstance(routes, (str, Path)):
            routes = json.loads(Path(routes).read_text())
        self.routes = routes
        env = project.spec.simulation.environment
        self.ambient_c = float(env.ambient_temperature_c if ambient_c is None else ambient_c)
        self.circuit = Circuit(gmin)
        self.diagnostics: list[Diagnostic] = []
        self.reference = self._choose_reference()
        self.circuit.set_reference(self.reference)
        self.network = build_passive_network(project, self.circuit, routes)
        self.diagnostics.extend(self.network.diagnostics)
        self.models: dict[str, BehaviorModel] = {}
        self._build_models()
        self.nets, self.net_representative = self._name_nets()
        self._faults: dict[str, int] = {}
        self._opened: dict[str, float] = {}
        self.time = 0.0
        self.events: list[Event] = []
        self.x = np.zeros(self.circuit.size)
        self.last_info = SolveInfo(0, 0.0, True)
        self.initialize(initial or {})

    # ------------------------------------------------------------------ build
    def _choose_reference(self) -> str:
        spec, catalog = self.project.spec, self.project.catalog
        fallback = None
        for cid, inst in spec.components.items():
            try:
                ctype = catalog.component(inst.type)
            except KeyError:
                continue
            for name, pin in ctype.pins.items():
                if pin.role == "ground":
                    if ctype.kind == "battery":
                        return f"C:{cid}.{name}"
                    fallback = fallback or f"C:{cid}.{name}"
        return fallback or sorted(self.project.graph.nodes)[0]

    def _build_models(self) -> None:
        spec, catalog = self.project.spec, self.project.catalog
        ctx = BuildContext(self.circuit, self.project, self.ambient_c)
        for cid, inst in spec.components.items():
            try:
                ctype = catalog.component(inst.type)
            except KeyError:
                continue
            behavior = ctype.behavior
            powered = any(p.role in ("power_in", "power_out") for p in ctype.pins.values())
            if behavior is None or behavior.model in NON_ELECTRICAL:
                if powered:
                    self.diagnostics.append(Diagnostic.warning(
                        "electrical_model_missing", f"{cid} ({inst.type}) has power pins but no electrical behavior model",
                        subjects=[cid]))
                continue
            factory = MODELS.get(behavior.model)
            if factory is None:
                if powered:
                    self.diagnostics.append(Diagnostic.warning(
                        "behavior_model_unsupported", f"{cid}: behavior model {behavior.model!r} has no electrical implementation",
                        subjects=[cid], data={}))
                continue
            params = _merge(dict(behavior.params), dict(inst.params))
            model = factory(cid, ctype, params, ctx)
            model.build(self.circuit)
            self.models[cid] = model
        for cid, model in self.models.items():
            if isinstance(model, FuseBlade):
                links = [b for b in self.network.internal_links.get(cid, []) if b.meta.get("link_kind") == "fuse"]
                if links:
                    model.attach(links[0], self.circuit)

    def _name_nets(self) -> tuple[dict[str, list[str]], dict[str, str]]:
        graph, spec, catalog = self.project.graph, self.project.spec, self.project.catalog
        signals: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        harness_wires = {w.id: w for h in spec.electrical.harnesses for w in h.wires}
        for edge in graph.edges:
            if edge.kind == "wire" and harness_wires[edge.ref].signal:
                signals[graph.net_of(edge.a)][harness_wires[edge.ref].signal] += 1
        roles: dict[str, str] = {}
        kinds: dict[str, str] = {}
        for cid, inst in spec.components.items():
            try:
                ctype = catalog.component(inst.type)
            except KeyError:
                continue
            for name, pin in ctype.pins.items():
                roles[f"C:{cid}.{name}"] = pin.role
                kinds[f"C:{cid}.{name}"] = ctype.kind
        names: dict[str, list[str]] = {}
        representative: dict[str, str] = {}
        ground_root = graph.net_of(self.reference) if self.reference in graph.nodes else None
        kind_priority = {"pcb": 0, "computer": 1}
        for root, members in graph.nets().items():
            pins = [m for m in members if m.startswith("C:")]
            if not pins:
                continue
            if root == ground_root:
                name = spec.electrical.ground_net
            elif signals.get(root):
                name = signals[root].most_common(1)[0][0]
            else:
                pin = sorted(pins, key=lambda m: (kind_priority.get(kinds.get(m, ""), 9), m))[0]
                name = pin.split(".", 1)[1] if len(pins) > 1 else pin[2:]
            if name in names:
                name = f"{name}@{root[2:]}"
            names[name] = members
            sources = [m for m in pins if roles.get(m) == "power_out"]
            if root == ground_root:
                representative[name] = self.reference
            else:
                representative[name] = sources[0] if sources else pins[0]
        return names, representative

    # ------------------------------------------------------------ lifecycle
    def initialize(self, initial: dict[str, Any]) -> None:
        """Apply initial states (``{"battery_soc": 0.95}``, ``{"<id>": {...}}``)
        and solve the DC operating point used as the first warm start."""
        for cid, model in self.models.items():
            state = dict(initial.get(cid, {}))
            if "battery_soc" in initial and model.model == "battery.lipo":
                state.setdefault("soc", initial["battery_soc"])
            model.initialize(state)
        self.x, self.last_info = self._solve(None, {}, self.x)

    def node(self, ref: str) -> int:
        """Resolve a pin ref, graph node id or net name to an unknown index."""
        if ref in self.nets:
            return self.circuit.index(self.net_representative[ref])
        if self.circuit.has(ref):
            return self.circuit.index(ref)
        return self.circuit.index(self.project.graph._pin_node(ref))

    def _apply_faults(self, faults: list[dict[str, Any]] | None) -> None:
        if faults is None:
            return
        resistors = self.circuit.resistors
        for index in self._faults.values():
            resistors.resistance[index] = 1e12
        for key, original in self._opened.items():
            self.network.branches[key].meta["opened"] = False
        self._opened.clear()
        for fault in faults:
            if fault.get("type") == "short":
                key = f"fault:{fault['a']}~{fault['b']}"
                r = float(fault.get("resistance", 0.01))
                if key in self._faults:
                    resistors.resistance[self._faults[key]] = r
                else:
                    self._faults[key] = self.circuit.resistor(self.node(fault["a"]), self.node(fault["b"]), r, key)
            elif fault.get("type") == "open":
                branch = self.network.branches[fault["branch"]]
                branch.meta["opened"] = True
                self._opened[branch.key] = 0.0

    def _update_conductors(self) -> None:
        resistance = self.circuit.resistors.resistance
        for branch in self.network.branches.values():
            if branch.meta.get("opened"):
                resistance[branch.index] = OPEN_RESISTANCE
            elif branch.kind == "wire":
                resistance[branch.index] = branch.resistance(self.ambient_c)
            elif branch.kind == "contact" or branch.meta.get("link_kind") != "fuse" or branch.meta["component"] not in self.models:
                resistance[branch.index] = branch.resistance_ref

    def _solve(self, dt: float | None, inputs: dict[str, Any], x0: np.ndarray) -> tuple[np.ndarray, SolveInfo]:
        self._update_conductors()
        for cid, model in self.models.items():
            model.begin_step(dt, inputs.get(cid, {}), self.time)
        x, info = self.circuit.solve(x0)
        for _ in range(MAX_ACTIVE_SET_PASSES):
            changed = [model.resolve(x) for model in self.models.values()]
            if not any(changed):
                break
            x, info = self.circuit.solve(x)
        return x, info

    def branch_currents(self, x: np.ndarray) -> dict[str, float]:
        currents = self.circuit.resistors.currents(x)
        return {key: float(currents[b.index]) for key, b in self.network.branches.items()}

    def step(self, dt: float, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        inputs = inputs or {}
        self._apply_faults(inputs.get("_faults"))
        x, info = self._solve(dt, inputs, self.x)
        self.time += dt
        events: list[Event] = []
        for model in self.models.values():
            events.extend(model.end_step(dt, x, self.time))
        currents = self.branch_currents(x)
        for key, branch in self.network.branches.items():
            branch.advance_temperature(currents[key], dt, self.ambient_c)
        self.x, self.last_info = x, info
        self.events.extend(events)
        return self._outputs(x, events, info)

    # ---------------------------------------------------- operating points
    def snapshot(self) -> dict[str, Any]:
        return {"models": {cid: m.snapshot() for cid, m in self.models.items()},
                "branches": {k: b.temperature_rise for k, b in self.network.branches.items()},
                "time": self.time, "x": self.x.copy(), "resistance": list(self.circuit.resistors.resistance)}

    def restore(self, state: dict[str, Any]) -> None:
        for cid, model in self.models.items():
            model.restore(state["models"][cid])
        for key, rise in state["branches"].items():
            self.network.branches[key].temperature_rise = rise
        self.time, self.x = state["time"], state["x"]
        resistance = self.circuit.resistors.resistance
        resistance[:len(state["resistance"])] = state["resistance"]
        for index in range(len(state["resistance"]), len(resistance)):
            resistance[index] = 1e12  # fault resistors created after the snapshot: open

    def operating_point(self, inputs: dict[str, Any], *, soc: float | None = None, thermal: str = "ambient",
                        iterations: int = 4, export_spice: bool = False,
                        nodeset_scale: float = 1.0) -> dict[str, Any]:
        """Static (DC) operating point; the system state is left unchanged.

        ``thermal="ambient"``: conductors at ambient (cold, highest current);
        ``"steady"``: conductor temperatures iterated to their steady rise."""
        saved = self.snapshot()
        faults = inputs.get("_faults")
        try:
            if faults is not None:
                self._apply_faults(faults)
            for model in self.models.values():
                if soc is not None and model.model == "battery.lipo":
                    model.s_soc = soc  # type: ignore[attr-defined]
                if hasattr(model, "s_temperature"):
                    model.s_temperature = self.ambient_c  # type: ignore[attr-defined]
            for branch in self.network.branches.values():
                branch.temperature_rise = 0.0
            x = self.x
            for _ in range(iterations if thermal == "steady" else 1):
                x, info = self._solve(None, inputs, x)
                if thermal != "steady":
                    break
                for key, current in self.branch_currents(x).items():
                    rise = self.network.branches[key].steady_rise(current, self.ambient_c)
                    self.network.branches[key].temperature_rise = min(rise, 500.0)
            events: list[Event] = []
            for model in self.models.values():
                events.extend(model.end_step(None, x, self.time))
            out = self._outputs(x, events, info)
            out["_x"] = x
            if export_spice:
                out["_spice"] = self.circuit.to_spice(x, title=f"r3 electrical operating point: {self.project.spec.robot.name}",
                                                     nodeset_scale=nodeset_scale)
            return out
        finally:
            if faults is not None:
                self._apply_faults([])
            self.restore(saved)

    # -------------------------------------------------------------- outputs
    def _v(self, x: np.ndarray, label: str) -> float:
        index = self.circuit.index(label)
        return 0.0 if index == GROUND else float(x[index])

    def _outputs(self, x: np.ndarray, events: list[Event], info: SolveInfo) -> dict[str, Any]:
        out: dict[str, Any] = {cid: model.outputs(x) for cid, model in self.models.items()}
        out["_nets"] = {name: self._v(x, rep) for name, rep in self.net_representative.items()}
        out["_pins"] = {label[2:]: self._v(x, label) for label in self.project.graph.nodes if label.startswith("C:")}
        currents = self.branch_currents(x)
        resistance = self.circuit.resistors.resistance
        branches: dict[str, Any] = {}
        for key, branch in self.network.branches.items():
            i = currents[key]
            r = resistance[branch.index]
            # ``current`` flows from ``from`` to ``to`` (graph edge orientation).
            entry = {"kind": branch.kind, "from": branch.node_a, "to": branch.node_b, "current": i, "resistance": r, "voltage_drop": i * r, "power_loss": i * i * r,
                     "temperature_rise": branch.temperature_rise, "rating": branch.rating}
            if branch.kind == "wire":
                entry.update(harness=branch.meta["harness"], awg=branch.meta["awg"], length=branch.meta["length"],
                             length_source=branch.meta["length_source"])
            elif branch.kind == "contact":
                entry.update(connectors=branch.meta["connectors"], pin=branch.meta["pin"], burst_rating=branch.burst_rating)
            branches[key] = entry
        out["_branches"] = branches
        out["_events"] = [e.to_json() for e in events]
        out["_time"] = self.time
        out["_solver"] = {"iterations": info.iterations, "residual": info.residual, "converged": info.converged,
                          "unknowns": self.circuit.size}
        return out

    # ---------------------------------------------------------------- SPICE
    def to_spice(self, x: np.ndarray | None = None) -> tuple[str, dict[str, str]]:
        """SPICE netlist of the network at the last solved state (or ``x``)."""
        return self.circuit.to_spice(self.x if x is None else x, title=f"r3 electrical: {self.project.spec.robot.name}")

    def branch(self, key: str) -> Branch:
        return self.network.branches[key]

    def fuse_models(self) -> dict[str, FuseBlade]:
        return {cid: m for cid, m in self.models.items() if isinstance(m, FuseBlade)}

    @property
    def unknowns(self) -> int:
        return self.circuit.size

    def __repr__(self) -> str:
        return f"ElectricalSystem({self.project.spec.robot.name!r}, models={len(self.models)}, unknowns={self.unknowns})"

