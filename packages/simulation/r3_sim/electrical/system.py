"""``ElectricalSystem``: the electrical digital twin of a robot project, solved by ngspice.

Built generically from ``project.graph`` (nodes, wires, contacts, internal
links) plus the catalog behavior model of every component. The circuit is a
SPICE netlist (see :mod:`r3_sim.electrical.netlist`): conductors as resistors
(electrothermal B-sources with the copper temperature coefficient on power
nets), one subcircuit per behavior model, ``EXTERNAL`` sources for every
quantity owned by another domain (motor speed and servo load from MuJoCo,
ESC commands decoded by its firmware logic, computer power state). ngspice
runs it through its shared library in lockstep with the co-simulation clock
(:mod:`r3_sim.electrical.ngspice`): each ``step`` hands ngspice the inputs,
ngspice integrates the transient (its own time points, LTE control and step
rejection) exactly up to the step boundary, and the solution at that point is
returned keyed by component id::

    system = ElectricalSystem(project)            # routes.json read if present
    out = system.step(0.001, {"esc": {"pulse_us": 1600.0}, "motor": {"omega": 310.0},
                              "servo": {"pulse_us": 1500.0, "horn_torque": 0.2, "horn_speed": 0.5},
                              "pi": {"activity": 0.6}})
    out["motor"]["torque"], out["battery"]["v_terminal"], out["pi"]["powered"]
    out["_nets"]["5V"], out["_branches"]["W_Y_P_IN"]["current"], out["_events"]

``operating_point`` solves static (DC) points with ``op`` on a second ngspice
instance, leaving the transient untouched.

Quantities owned by MuJoCo (motor speed, servo load) are continuous, so they
enter as a first-order hold: a linear ramp from the previous to the new value
across the step, evaluated at ngspice's own time points. Discrete commands
(ESC duty and mode, enables, computer power state) are zero-order holds.

Fault injection (generic): ``inputs["_faults"] = [{"type": "short", "a": "hat.VBAT",
"b": "GND", "resistance": 0.05}, {"type": "open", "branch": "W_Y_P_HAT"}]``;
endpoints are ``<component>.<pin>`` refs, graph node ids or net names. Faults
persist until replaced by a later ``_faults`` list. A topology change restarts
the ngspice transient from the current state (every integrator node carried
over with ``.ic``; inductor currents restart from the new DC solution).
"""

from __future__ import annotations

import collections
import ctypes
import json
import weakref
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic

from .models import MODELS, NON_ELECTRICAL, BehaviorModel, BuildContext, Event
from .models.base import node_name
from .models.fuse import FuseBlade
from .netlist import GROUND, Netlist, Solution, fmt
from .network import OPEN_RESISTANCE, Branch, build_passive_network, emit_conductors
from .ngspice import NgSpice, find_library

TRANSIENT_STOP = 1e7  # s, ``.tran`` stop time (the master stops long before)
MAX_ACTIVE_SET_PASSES = 8
DEFAULT_MAX_STEP = 1e-3  # s, ngspice TMAX (the master's 1 ms co-simulation step)
# ngspice options: Gear (BDF2, stiffly stable: no trapezoidal ringing on the
# master's zero-order-hold input steps), tighter relative tolerance than the
# SPICE default (battery-capacity and stall-current checks are 0.1 % class),
# Signal-only islands are kept well posed by explicit 1 GOhm leakage on every
# electrical graph node (like gmin; ngspice's global ``rshunt`` would also leak
# the thermal nodes towards 0 C).
SPICE_OPTIONS = ".options method=gear maxord=2 reltol=1e-5 abstol=1e-9 vntol=1e-7 itl1=300 itl4=50"


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _first_order_hold(begin: float, end: float, start: float, dt: float):
    """v(t) = begin + (end - begin) (t - start) / dt on (start, start + dt]."""
    slope = (end - begin) / dt

    def value(t: float) -> float:
        return begin + slope * min(max(t - start, 0.0), dt)

    return value


class _Capture:
    """``SendData`` consumer: keeps per-step reductions of watched vectors and a
    full copy of the vectors at the master's target time point."""

    def __init__(self, ng: NgSpice, watches: list[tuple[str, str]]) -> None:
        self.ng = ng
        self.watch_spec = watches
        self.reset_plot()

    def reset_plot(self) -> None:
        self.names: list[str] = []
        self.index: dict[str, int] = {}
        self.doubles: list[ctypes.c_double] = []
        self.watched: list[tuple[str, str, int]] = []
        self.time_index = -1
        self.serial = -1
        self.target = 0.0
        self.step_start = 0.0
        self.values: np.ndarray | None = None
        self.time = 0.0
        self.reductions: dict[tuple[str, str], float] = {}
        self._prev: dict[int, float] = {}
        self._prev_time = 0.0

    def _bind(self, data: Any) -> None:
        names = self.ng.plot_vectors
        self.serial = self.ng.plot_serial
        self.names = names
        self.index = {name: k for k, name in enumerate(names)}
        self.index[GROUND] = len(names)
        offset = _VALUE_OFFSET
        self.doubles = [ctypes.c_double.from_address(ctypes.addressof(data.vecsa[k].contents) + offset) for k in range(len(names))]
        self.time_index = self.index.get("time", -1)
        self.watched = [(name, kind, self.index[name]) for name, kind in self.watch_spec if name in self.index]

    def begin(self, start: float, target: float) -> None:
        self.step_start, self.target = start, target
        self.reductions = {}
        self._prev_time = start

    def __call__(self, pointer: Any) -> None:
        data = pointer.contents
        if self.serial != self.ng.plot_serial:
            self._bind(data)
            if self.values is None and self.watched:  # first point of a plot: seed the trapezoid history
                self._prev = {k: self.doubles[k].value for _, _, k in self.watched}
        doubles = self.doubles
        time = doubles[self.time_index].value if self.time_index >= 0 else 0.0
        reductions = self.reductions
        span = time - self._prev_time
        for name, kind, k in self.watched:
            value = doubles[k].value
            key = (name, kind)
            if kind == "min":
                reductions[key] = min(reductions.get(key, value), value)
            elif kind == "max":
                reductions[key] = max(reductions.get(key, value), value)
            else:  # time integral for the step mean (trapezoid over accepted points)
                reductions[key] = reductions.get(key, 0.0) + 0.5 * (self._prev.get(k, value) + value) * span
                self._prev[k] = value
        self._prev_time = time
        if self.time_index < 0 or time >= self.target - NgSpice.TIME_EPS:
            values = np.empty(len(doubles) + 1)
            values[:-1] = [d.value for d in doubles]
            values[-1] = 0.0
            self.values, self.time = values, time

    def solution(self, step: float | None) -> Solution:
        watch = dict(self.reductions)
        if step:
            for key in list(watch):
                if key[1] == "mean":
                    watch[key] /= step
        return Solution(self.values, self.index, self.time, watch)


_VALUE_OFFSET = 8  # offsetof(vecvalues, creal): after the char* name


class ElectricalSystem:
    is_fallback = False

    def __init__(self, project: Any, routes: dict[str, Any] | str | Path | None = None, *,
                 initial: dict[str, Any] | None = None, ambient_c: float | None = None,
                 gmin: float | None = None, max_step: float = DEFAULT_MAX_STEP, esc_model: str = "averaged") -> None:
        if find_library() is None:  # fail at construction so the kernel can select its labeled fallback
            raise FileNotFoundError("libngspice not found (install ngspice's shared library or set R3_NGSPICE_LIBRARY)")
        self.project = project
        if routes is None:
            routes = project.read_build_json("wiring/routes.json")
        elif isinstance(routes, (str, Path)):
            routes = json.loads(Path(routes).read_text())
        self.routes = routes
        env = project.spec.simulation.environment
        self.ambient_c = float(env.ambient_temperature_c if ambient_c is None else ambient_c)
        self.rshunt = 1.0 / gmin if gmin else 1e9
        self.max_step = max_step
        self.diagnostics: list[Diagnostic] = []
        self.reference = self._choose_reference()
        self.ctx = BuildContext(project, self.ambient_c, self.reference)
        self.network = build_passive_network(project, routes)
        self.diagnostics.extend(self.network.diagnostics)
        self.models: dict[str, BehaviorModel] = {}
        self._build_models()
        if esc_model not in ("averaged", "switched"):
            raise ValueError(f"esc_model must be 'averaged' or 'switched', not {esc_model!r}")
        self.esc_model = esc_model
        for model in self.models.values():
            if hasattr(model, "switched"):
                model.switched = esc_model == "switched"
        self.nets, self.net_representative = self._name_nets()
        self.pin_labels = [label for label in project.graph.nodes if label.startswith("C:")]
        self._fault_list: list[dict[str, Any]] = []
        self._shorts: dict[str, tuple[str, str, float]] = {}
        self._opened: set[str] = set()
        self._conductor_states: dict[str, float] = {}
        self.time = 0.0
        self.events: list[Event] = []
        self.last: Solution | None = None
        self._tran: NgSpice | None = None
        self._tran_capture: _Capture | None = None
        self._tran_time = 0.0
        self._restart = True
        self._op: NgSpice | None = None
        self._op_capture: _Capture | None = None
        self._op_key: Any = None
        self._op_count = 0
        self._op_nodeset: dict[str, float] | None = None
        self._layout_cache: dict[str, Any] = {}
        self._held: dict[str, float] = {}  # last value of every ramped control (start of the next ramp)
        self.stats = {"steps": 0, "restarts": 0, "accepted_points": 0, "rejected_steps": 0, "operating_points": 0,
                      "operating_point_retries": 0}
        self._finalizer = weakref.finalize(self, ElectricalSystem._release, [None, None])
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
                from ..project_devices import electrical_factory
                factory = electrical_factory(self.project, cid, behavior.model)
            if factory is None:
                if powered:
                    self.diagnostics.append(Diagnostic.warning(
                        "behavior_model_unsupported", f"{cid}: behavior model {behavior.model!r} has no electrical implementation",
                        subjects=[cid], data={}))
                continue
            params = _merge(dict(behavior.params), dict(inst.params))
            self.models[cid] = factory(cid, ctype, params, self.ctx)
        for cid, model in self.models.items():
            if isinstance(model, FuseBlade):
                links = [b for b in self.network.internal_links.get(cid, []) if b.meta.get("link_kind") == "fuse"]
                if links:
                    model.attach(links[0])

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

    def spice_node(self, label: str) -> str:
        return self.ctx.node(label)

    def netlist(self, mode: str = "tran") -> Netlist:
        """The SPICE deck of the current topology and state (``tran`` or ``op``)."""
        net = Netlist(f"r3 electrical ({mode}): {self.project.spec.robot.name}", mode)
        net.comment(f"reference (SPICE ground): {self.reference}; thermal nodes in C, heat flow in W")
        net.add(f"vtamb tamb 0 dc {fmt(self.ambient_c)}")
        gain = net.external("thermal_gain") if mode == "op" else None
        fuse_links = {m.branch.key for m in self.models.values() if isinstance(m, FuseBlade) and m.branch is not None}
        emit_conductors(net, self.network, self.spice_node, self.ambient_c, skip=fuse_links, opened=self._opened,
                        states=self._conductor_states, thermal_gain=gain)
        net.comment(f"leakage of every electrical node to the reference ({self.rshunt:g} ohm, gmin equivalent)")
        used = {self.spice_node(label) for branch in self.network.branches.values() for label in (branch.node_a, branch.node_b)}
        used |= {pin.node for model in self.models.values() for pin in model.pins.values()}
        for k, node in enumerate(sorted(used - {GROUND})):
            net.add(f"rleak{k} {node} 0 {fmt(self.rshunt)}")
        for key, (a, b, resistance) in self._shorts.items():
            net.add(f"* fault {key}", f"rfault{len(net.body)} {a} {b} {fmt(resistance)}")
        for model in self.models.values():
            net.comment(f"{model.id}: {model.model}")
            model.build(net)
        return net

    def deck(self, mode: str = "tran", nodeset: dict[str, float] | None = None) -> list[str]:
        net = self.netlist(mode)
        net.nodeset = dict(nodeset or {})
        options = SPICE_OPTIONS
        analysis = f".tran {fmt(self.max_step)} {fmt(TRANSIENT_STOP)} 0 {fmt(self.max_step)}" if mode == "tran" else ".op"
        return net.deck(options, analysis)

    def watches(self) -> list[tuple[str, str]]:
        return [w for model in self.models.values() for w in model.watches()]

    # ------------------------------------------------------------ lifecycle
    def initialize(self, initial: dict[str, Any]) -> None:
        """Apply initial states (``{"battery_soc": 0.95}``, ``{"<id>": {...}}``).
        The transient (and its t = 0 operating point) starts with the first step."""
        for cid, model in self.models.items():
            state = initial.get(cid, {})
            state = dict(state) if isinstance(state, dict) else {}
            if "battery_soc" in initial and model.model == "battery.lipo":
                state.setdefault("soc", float(initial["battery_soc"]))
            model.initialize(state)
        self._restart = True

    @staticmethod
    def _release(instances: list[NgSpice | None]) -> None:
        for instance in instances:
            if instance is not None:
                instance.release()

    def close(self) -> None:
        """Return the ngspice instances to the pool (also done on garbage collection)."""
        self._finalizer()
        self._tran = self._op = None

    def _track(self) -> None:
        self._finalizer.detach()
        self._finalizer = weakref.finalize(self, ElectricalSystem._release, [self._tran, self._op])

    def node(self, ref: str) -> str:
        """Resolve a pin ref, graph node id or net name to a SPICE node."""
        if ref in self.nets:
            return self.spice_node(self.net_representative[ref])
        if ref in self.project.graph.nodes:
            return self.spice_node(ref)
        return self.spice_node(self.project.graph._pin_node(ref))

    def _set_faults(self, faults: list[dict[str, Any]]) -> bool:
        """Replace the active fault list; returns True if the topology changed."""
        shorts: dict[str, tuple[str, str, float]] = {}
        opened: set[str] = set()
        for fault in faults:
            if fault.get("type") == "short":
                shorts[f"{fault['a']}~{fault['b']}"] = (self.node(fault["a"]), self.node(fault["b"]),
                                                        float(fault.get("resistance", 0.01)))
            elif fault.get("type") == "open":
                if fault["branch"] not in self.network.branches:
                    raise KeyError(f"unknown branch {fault['branch']!r}")
                opened.add(fault["branch"])
        changed = shorts != self._shorts or opened != self._opened
        self._fault_list = [dict(f) for f in faults]
        self._shorts, self._opened = shorts, opened
        return changed

    # ------------------------------------------------------------ transient
    def _push(self, ng: NgSpice, extra: dict[str, float] | None = None) -> None:
        values = ng.source_values
        for model in self.models.values():
            for name, source in model.sources.items():
                values[source] = model.controls[name]
        if extra:
            values.update(extra)
        if ng is self._op:
            for source, value in values.items():
                ng.command(f"alter {source} dc = {fmt(value)}")

    def _start_transient(self) -> None:
        if self._tran is None:
            self._tran = NgSpice.acquire()
            self._track()
        else:
            if self.last is not None:  # conductor temperatures carry over as initial conditions
                for key, branch in self.network.branches.items():
                    if branch.thermal_node and branch.thermal_node in self.last.index:
                        self._conductor_states[key] = self.last.v(branch.thermal_node)
            self._tran.halt()
            self._tran.command("destroy all")
            self._tran.command("remcirc")
            self.stats["restarts"] += 1
        ng = self._tran
        ng.source_values = {}
        ng.load(self.deck("tran"))
        if self.time == 0.0:
            for model in self.models.values():
                model.begin_step(0.0, {}, 0.0)
        self._push(ng)
        capture = self._tran_capture = _Capture(ng, self.watches())
        capture.begin(0.0, 0.0)
        ng.start_transient(capture)
        self._tran_time = 0.0
        self._restart = False

    def step(self, dt: float, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        inputs = inputs or {}
        faults = inputs.get("_faults")
        if faults is not None and self._set_faults(faults):
            self._restart = True
        if self._restart:
            self._start_transient()
        for cid, model in self.models.items():
            model.begin_step(dt, inputs.get(cid, {}), self.time)
        ng, capture = self._tran, self._tran_capture
        self._push(ng)
        target = self._tran_time + dt
        ng.time_sources, edges = {}, []
        start = self._tran_time
        for model in self.models.values():
            for name in model.ramped:
                source, end_value = model.sources[name], model.controls[name]
                begin_value = self._held.get(source, end_value)
                self._held[source] = end_value
                if begin_value != end_value:
                    ng.time_sources[source] = _first_order_hold(begin_value, end_value, start, dt)
        for model in self.models.values():
            functions, corners = model.time_sources(self._tran_time, target)
            ng.time_sources.update(functions)
            edges += corners
        ng.edges = sorted(edges)
        capture.begin(self._tran_time, target)
        before = (ng.accepted_points, ng.rejected_steps)
        ng.advance(target)
        self._tran_time = target
        sol = capture.solution(dt)
        self.time += dt
        events: list[Event] = []
        for model in self.models.values():
            events.extend(model.end_step(dt, sol, self.time))
        self.last = sol
        self.events.extend(events)
        self.stats["steps"] += 1
        self.stats["accepted_points"] += ng.accepted_points - before[0]
        self.stats["rejected_steps"] += ng.rejected_steps - before[1]
        solver = {"engine": "ngspice", "converged": True, "accepted_points": ng.accepted_points - before[0],
                  "rejected_steps": ng.rejected_steps - before[1], "spice_time": sol.time, "unknowns": len(sol.values) - 1}
        return self._outputs(sol, events, solver)

    # ---------------------------------------------------- operating points
    def snapshot(self) -> dict[str, Any]:
        return {"models": {cid: m.snapshot() for cid, m in self.models.items()},
                "faults": [dict(f) for f in self._fault_list], "time": self.time}

    def restore(self, state: dict[str, Any]) -> None:
        for cid, model in self.models.items():
            model.restore(state["models"][cid])
        self._set_faults(state["faults"])

    def operating_point(self, inputs: dict[str, Any], *, soc: float | None = None, thermal: str = "ambient",
                        iterations: int = 4, export_spice: bool = False,
                        nodeset_scale: float = 1.0) -> dict[str, Any]:
        """Static (DC) operating point (ngspice ``op``); the system state is left unchanged.

        ``thermal="ambient"``: conductors at ambient (cold, highest current);
        ``"steady"``: conductor temperatures at their electrothermal steady state
        (solved by ngspice together with the circuit). Component temperatures stay
        at ambient. ``iterations`` and ``nodeset_scale`` are accepted for
        interface compatibility and unused."""
        saved = self.snapshot()
        faults = inputs.get("_faults")
        try:
            if faults is not None:
                self._set_faults(faults)
            for model in self.models.values():
                if soc is not None and model.model == "battery.lipo":
                    model.s_soc = soc  # type: ignore[attr-defined]
            ng = self._op_instance()
            for cid, model in self.models.items():
                model.begin_step(None, inputs.get(cid, {}), self.time)
            gain = {"vthermal_gain": 1.0 if thermal == "steady" else 0.0}
            sol = self._solve_op(gain)
            for _ in range(MAX_ACTIVE_SET_PASSES):  # UVLO comparators: explicit active set (DC hysteresis is path dependent)
                if not any([model.resolve(sol) for model in self.models.values()]):
                    break
                sol = self._solve_op(gain)
            self._op_count += 1
            self.stats["operating_points"] += 1
            if self._op_count % 200 == 0:
                ng.command("destroy all")
            events: list[Event] = []
            for model in self.models.values():
                events.extend(model.end_step(None, sol, self.time))
            out = self._outputs(sol, events, {"engine": "ngspice", "analysis": "op", "converged": True,
                                              "unknowns": len(sol.values) - 1})
            out["_x"] = sol
            if export_spice:
                out["_spice"] = "\n".join(self.deck("op"))
            return out
        finally:
            self.restore(saved)

    def _solve_op(self, extra: dict[str, float]) -> Solution:
        """``op`` with native DC source stepping and a warm-start fallback.

        A point that cannot be reached directly is retried with ``.nodeset``
        from the last converged operating point. Reactive elements are reduced
        to their DC equivalents, so transient-OP fallback cannot return an
        unfinished motor-current or battery-polarization transient as DC.
        """
        attempts = [None] + ([self._op_nodeset] if self._op_nodeset else [])
        for nodeset in attempts:
            if nodeset is not None:
                self._op_key = None
            ng = self._op_instance(nodeset)
            self._push(ng, extra)
            capture = self._op_capture
            capture.values = None
            ng.errors.clear()
            ng.command("op")
            if capture.values is not None and not ng.errors:
                sol = capture.solution(None)
                self._op_nodeset = {name: float(sol.values[k]) for name, k in sol.index.items()
                                    if name not in (GROUND, "time") and "#" not in name and not name.startswith("ext_")}
                return sol
            self.stats["operating_point_retries"] += 1
        raise RuntimeError("ngspice operating point failed:\n" + "\n".join(ng.errors[-20:] or list(ng.log)[-20:]))

    def _op_instance(self, nodeset: dict[str, float] | None = None) -> NgSpice:
        key = (tuple(sorted(self._shorts.items())), tuple(sorted(self._opened)),
               tuple(sorted((cid, bool(getattr(m, "s_blown", False))) for cid, m in self.models.items())))
        if self._op is None:
            self._op = NgSpice.acquire()
            self._track()
        if key != self._op_key:
            ng = self._op
            ng.halt()
            ng.command("destroy all")
            ng.command("remcirc")
            ng.source_values = {}
            ng.load(self.deck("op", nodeset))
            self._op_capture = _Capture(ng, [])
            ng._capture = self._op_capture
            self._op_key = key
        return self._op

    # -------------------------------------------------------------- outputs
    def voltage(self, sol: Solution, label: str) -> float:
        """Voltage of a graph node label relative to the reference (0 for a pin
        connected to nothing, which is not a SPICE node)."""
        node = self.spice_node(label)
        return sol.v(node) if node in sol.index else 0.0

    def _layout(self, sol: Solution) -> dict[str, Any]:
        """Index arrays into a solution's value vector (cached per output plot)."""
        key = self._fault_key()
        if self._layout_cache.get("index") is sol.index and self._layout_cache.get("key") == key:
            return self._layout_cache
        ground = sol.index[GROUND]
        at = lambda label: sol.index.get(self.spice_node(label), ground)  # noqa: E731
        branches = list(self.network.branches.values())
        layout: dict[str, Any] = {"key": key, "index": sol.index, "keys": [b.key for b in branches]}
        layout["a"] = np.array([at(b.node_a) for b in branches])
        layout["b"] = np.array([at(b.node_b) for b in branches])
        layout["t"] = np.array([sol.index.get(b.thermal_node, ground) if b.thermal_node else ground for b in branches])
        layout["thermal"] = np.array([bool(b.thermal_node and b.thermal_node in sol.index) for b in branches])
        layout["r_ref"] = np.array([b.resistance_ref for b in branches])
        layout["alpha"] = np.array([b.temperature_coefficient for b in branches])
        layout["r_ambient"] = np.array([b.resistance(self.ambient_c) for b in branches])
        layout["opened"] = np.array([b.key in self._opened for b in branches])
        layout["fuses"] = [(k, self.models[b.meta["component"]]) for k, b in enumerate(branches)
                           if b.meta.get("link_kind") == "fuse" and b.meta.get("component") in self.models]
        layout["templates"] = []
        for b in branches:
            entry: dict[str, Any] = {"kind": b.kind, "from": b.node_a, "to": b.node_b}
            if b.kind == "wire":
                extra = {"harness": b.meta["harness"], "awg": b.meta["awg"], "length": b.meta["length"],
                         "length_source": b.meta["length_source"]}
            elif b.kind == "contact":
                extra = {"connectors": b.meta["connectors"], "pin": b.meta["pin"], "burst_rating": b.burst_rating}
            else:
                extra = {}
            layout["templates"].append((entry, b.rating, extra))
        layout["pins"] = (self.pin_labels, np.array([at(label) for label in self.pin_labels]))
        self._layout_cache = layout
        return layout

    def _fault_key(self) -> tuple:
        return (tuple(sorted(self._opened)), tuple(sorted(self._shorts)))

    def _branch_arrays(self, sol: Solution) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
        """(keys, current from node_a to node_b, resistance, temperature rise) of every conductor."""
        lay = self._layout(sol)
        values = sol.values
        temperature = values[lay["t"]]
        resistance = np.where(lay["thermal"], lay["r_ref"] * (1.0 + lay["alpha"] * (temperature - 20.0)), lay["r_ambient"])
        resistance = np.where(lay["opened"], OPEN_RESISTANCE, resistance)
        current = (values[lay["a"]] - values[lay["b"]]) / resistance
        rise = np.where(lay["thermal"], temperature - self.ambient_c, 0.0)
        for k, fuse in lay["fuses"]:
            current[k], resistance[k] = fuse.current(sol), fuse.resistance()
        return lay["keys"], current, resistance, rise

    def _branch_table(self, sol: Solution) -> dict[str, dict[str, Any]]:
        keys, current, resistance, rise = self._branch_arrays(sol)
        templates = self._layout(sol)["templates"]
        table = {}
        for key, (base, rating, extra), i, r, dt in zip(keys, templates, current.tolist(), resistance.tolist(), rise.tolist()):
            # ``current`` flows from ``from`` to ``to`` (graph edge orientation).
            table[key] = {**base, "current": i, "resistance": r, "voltage_drop": i * r, "power_loss": i * i * r,
                          "temperature_rise": dt, "rating": rating, **extra}
        return table

    def _pin_voltages(self, sol: Solution) -> dict[str, float]:
        labels, index = self._layout(sol)["pins"]
        return dict(zip((label[2:] for label in labels), sol.values[index].tolist()))

    def branch_currents(self, sol: Solution | None = None) -> dict[str, float]:
        """Current of every conductor (from ``node_a`` to ``node_b``) at a solution (default: the last step)."""
        keys, current, _, _ = self._branch_arrays(sol or self.last)
        return dict(zip(keys, current.tolist()))

    def _outputs(self, sol: Solution, events: list[Event], solver: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {cid: model.outputs(sol) for cid, model in self.models.items()}
        out["_nets"] = {name: self.voltage(sol, rep) for name, rep in self.net_representative.items()}
        out["_pins"] = self._pin_voltages(sol)
        out["_branches"] = self._branch_table(sol)
        out["_events"] = [e.to_json() for e in events]
        out["_time"] = self.time
        out["_solver"] = solver
        return out

    # ---------------------------------------------------------------- misc
    def to_spice(self, mode: str = "tran") -> str:
        """The ngspice deck (``tran`` lockstep transient or ``op``) as text."""
        return "\n".join(self.deck(mode))

    def branch(self, key: str) -> Branch:
        return self.network.branches[key]

    def fuse_models(self) -> dict[str, FuseBlade]:
        return {cid: m for cid, m in self.models.items() if isinstance(m, FuseBlade)}

    @property
    def unknowns(self) -> int:
        """Number of ngspice output vectors (node voltages and branch currents)."""
        capture = self._tran_capture or self._op_capture
        if capture is not None and capture.names:
            return len(capture.names) - (1 if "time" in capture.index else 0)
        return len(self.project.graph.nodes)

    def __repr__(self) -> str:
        return f"ElectricalSystem({self.project.spec.robot.name!r}, models={len(self.models)}, engine=ngspice)"


__all__ = ["ElectricalSystem", "node_name"]
