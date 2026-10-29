"""Minimal ngspice test bench for single behavior models.

Wraps one or more models plus hand-written cards (sources, loads) in a deck
and runs it with the same lockstep driver as :class:`ElectricalSystem`::

    bench = Bench([battery], ["bload n_c_bat_pos 0 i = 5"], max_step=1.0)
    sol = bench.step(1.0)            # transient, lockstep
    sol = bench.operating_point()    # ``op`` of the same circuit in op mode
"""

from __future__ import annotations

from typing import Any, Sequence

from .models.base import BehaviorModel
from .netlist import Netlist, Solution, fmt
from .ngspice import NgSpice
from .system import SPICE_OPTIONS, TRANSIENT_STOP, _Capture


class Bench:
    def __init__(self, models: Sequence[BehaviorModel], cards: Sequence[str] = (), *, ambient_c: float = 25.0,
                 max_step: float = 1e-3) -> None:
        self.models = list(models)
        self.cards = list(cards)
        self.ambient_c = ambient_c
        self.max_step = max_step
        self.time = 0.0
        self._ng: NgSpice | None = None
        self._capture: _Capture | None = None
        self._spice_time = 0.0

    def deck(self, mode: str) -> list[str]:
        net = Netlist(f"r3 bench ({mode})", mode)
        net.add(f"vtamb tamb 0 dc {fmt(self.ambient_c)}", *self.cards)
        for model in self.models:
            model.build(net)
        analysis = f".tran {fmt(self.max_step)} {fmt(TRANSIENT_STOP)} 0 {fmt(self.max_step)}" if mode == "tran" else ".op"
        return net.deck(SPICE_OPTIONS, analysis)

    def _instance(self) -> NgSpice:
        if self._ng is None:
            self._ng = NgSpice.acquire()
        else:
            self._ng.halt()
            self._ng.command("destroy all")
            self._ng.command("remcirc")
        self._ng.source_values = {}
        return self._ng

    def _push(self) -> None:
        for model in self.models:
            for name, source in model.sources.items():
                self._ng.source_values[source] = model.controls[name]

    def start(self, inputs: dict[str, dict[str, Any]] | None = None) -> Solution:
        """Load the transient deck (models' current states as initial conditions) and solve t = 0."""
        ng = self._instance()
        ng.load(self.deck("tran"))
        for model in self.models:
            model.begin_step(0.0, (inputs or {}).get(model.id, {}), self.time)
        self._push()
        watches = [w for model in self.models for w in model.watches()]
        self._capture = _Capture(ng, watches)
        self._capture.begin(0.0, 0.0)
        ng.start_transient(self._capture)
        self._spice_time = 0.0
        return self._capture.solution(None)

    def step(self, dt: float, inputs: dict[str, dict[str, Any]] | None = None) -> Solution:
        if self._capture is None:
            self.start(inputs)
        inputs = inputs or {}
        for model in self.models:
            model.begin_step(dt, inputs.get(model.id, {}), self.time)
        self._push()
        target = self._spice_time + dt
        self._capture.begin(self._spice_time, target)
        self._ng.advance(target)
        self._spice_time = target
        sol = self._capture.solution(dt)
        self.time += dt
        for model in self.models:
            model.end_step(dt, sol, self.time)
        return sol

    def operating_point(self, inputs: dict[str, dict[str, Any]] | None = None) -> Solution:
        ng = self._instance()
        self._capture = None
        ng.load(self.deck("op"))
        for model in self.models:
            model.begin_step(None, (inputs or {}).get(model.id, {}), self.time)
        self._push()
        capture = _Capture(ng, [])
        ng._capture = capture
        ng.command("op")
        return capture.solution(None)

    def close(self) -> None:
        if self._ng is not None:
            self._ng.release()
            self._ng = None
