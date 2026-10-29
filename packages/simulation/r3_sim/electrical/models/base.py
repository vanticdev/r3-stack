"""Shared infrastructure for electrical behavior models."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from r3_model.units import to_si

from ..mna import GROUND, Circuit, NumericElement, SpiceWriter


@dataclass
class Event:
    time: float
    code: str
    component: str
    message: str
    data: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"time": self.time, "code": self.code, "component": self.component, "message": self.message, "data": self.data}


class Params:
    """Behavior parameters with unit parsing and provenance of defaults.

    ``get`` returns the catalog/instance value when present; otherwise the
    supplied default, which is recorded in ``estimated`` so the stage can list
    every value that is an engineering estimate rather than data."""

    def __init__(self, owner: str, values: dict[str, Any]) -> None:
        self.owner = owner
        self.values = values
        self.estimated: dict[str, float] = {}

    def get(self, key: str, default: float | None = None) -> float:
        if key in self.values and self.values[key] is not None:
            return to_si(self.values[key])
        if default is None:
            raise KeyError(f"{self.owner}: missing behavior parameter {key!r}")
        self.estimated[key] = float(default)
        return float(default)

    def raw(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.values


@dataclass
class PinRef:
    name: str
    node: int
    role: str
    voltage_nominal: float | None
    voltage_min: float | None
    voltage_max: float | None
    current_max: float | None


class BuildContext:
    """What a model needs to attach itself to the network."""

    def __init__(self, circuit: Circuit, project: Any, ambient_c: float) -> None:
        self.circuit = circuit
        self.project = project
        self.ambient_c = ambient_c
        self.internal_links: dict[str, list[Any]] = {}

    def pins(self, component_id: str, ctype: Any) -> dict[str, PinRef]:
        result = {}
        for name, pin in ctype.pins.items():
            result[name] = PinRef(name, self.circuit.node(f"C:{component_id}.{name}"), pin.role, pin.voltage_nominal,
                                  pin.voltage_min, pin.voltage_max, pin.current_max)
        return result


class BehaviorModel:
    """A component's electrical behavior.

    Lifecycle per step: ``begin_step`` (apply inputs, update per-step element
    parameters) -> Newton solve -> ``resolve`` (active-set changes; return True
    to re-solve the same step) -> ``end_step`` (integrate states, emit events)
    -> ``outputs``. ``dt=None`` means a DC operating point: inductors short,
    capacitors open, no state integration."""

    model = ""

    def __init__(self, component_id: str, ctype: Any, params: dict[str, Any], ctx: BuildContext) -> None:
        self.id = component_id
        self.ctype = ctype
        self.params = Params(component_id, params)
        self.ctx = ctx
        self.pins = ctx.pins(component_id, ctype) if ctype is not None else {}
        self.ambient_c = ctx.ambient_c

    # Pin selection by catalog role, in catalog order.
    def pin_by_role(self, *roles: str) -> PinRef | None:
        for pin in self.pins.values():
            if pin.role in roles:
                return pin
        return None

    def pins_by_role(self, *roles: str) -> list[PinRef]:
        return [pin for pin in self.pins.values() if pin.role in roles]

    def require(self, pin: PinRef | None, what: str) -> PinRef:
        if pin is None:
            raise ValueError(f"{self.id} ({self.model}): no {what} pin in catalog entry")
        return pin

    def build(self, circuit: Circuit) -> None:
        pass

    def initialize(self, initial: dict[str, Any]) -> None:
        pass

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        pass

    def resolve(self, x: np.ndarray) -> bool:
        return False

    def end_step(self, dt: float | None, x: np.ndarray, time: float) -> list[Event]:
        return []

    def outputs(self, x: np.ndarray) -> dict[str, Any]:
        return {}

    # State snapshot for static operating points.
    def snapshot(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k.startswith("s_")}

    def restore(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)


def voltage(x: np.ndarray, a: int, b: int = GROUND) -> float:
    va = x[a] if a != GROUND else 0.0
    vb = x[b] if b != GROUND else 0.0
    return float(va - vb)


def first_order(state: float, target: float, dt: float, tau: float) -> float:
    """Exact update of dy/dt = (target - y) / tau over dt with constant target."""
    if tau <= 0:
        return target
    return target + (state - target) * math.exp(-dt / tau)


class SupplyLoad(NumericElement):
    """Two-terminal load drawing ``current_fn(v)`` from ``pos`` to ``neg``.
    ``spice_expr(vexpr)`` returns the same current as a SPICE expression."""

    def __init__(self, name: str, pos: int, neg: int, current_fn: Any, spice_expr: Any) -> None:
        self.name = name
        self.pos, self.neg = pos, neg
        self.current_fn = current_fn
        self.spice_expr = spice_expr
        self.rows = [pos, neg]
        self.deps = [pos, neg]

    def residual(self, v: np.ndarray) -> np.ndarray:
        i = self.current_fn(v[0] - v[1])
        return np.array([i, -i])

    def current(self, x: np.ndarray) -> float:
        return float(self.current_fn(voltage(x, self.pos, self.neg)))

    def spice(self, writer: SpiceWriter, x: np.ndarray) -> None:
        writer.comment(f"{self.name}: nonlinear supply load")
        expression = self.spice_expr(writer.v(self.pos, self.neg))
        writer.add(f"{writer.name('B')} {writer.node(self.pos)} {writer.node(self.neg)} I = {expression}")


def constant_power_current(power: float, v_knee: float):
    """I = P/V above ``v_knee``; resistive continuation P V / v_knee^2 below it
    (a collapsing supply cannot deliver constant power; keeps Newton well posed)."""

    def fn(v: float) -> float:
        if v >= v_knee:
            return power / v
        return power * v / (v_knee * v_knee)

    def spice(vexpr: str) -> str:
        return f"({vexpr} >= {v_knee:.12g} ? {power:.12g}/{vexpr} : {power:.12g}*{vexpr}/{v_knee * v_knee:.12g})"

    return fn, spice


def constant_current(current: float, v_knee: float):
    """I = I0 above ``v_knee``; I0 V / v_knee below (supply collapse)."""

    def fn(v: float) -> float:
        if v >= v_knee:
            return current
        return current * max(v, -v_knee) / v_knee

    def spice(vexpr: str) -> str:
        return f"({vexpr} >= {v_knee:.12g} ? {current:.12g} : {current:.12g}*max({vexpr},{-v_knee:.12g})/{v_knee:.12g})"

    return fn, spice
