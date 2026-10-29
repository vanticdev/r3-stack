"""Shared infrastructure for electrical behavior models.

A behavior model owns one SPICE subcircuit (the physics, solved by ngspice)
and the device logic around it that is not circuit physics: command
decoding, protection state machines, boot timers. That logic reads the
solution at the co-simulation step boundary and drives the subcircuit
through ``EXTERNAL`` sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from r3_model.units import to_si

from ..netlist import GROUND, Netlist, Solution, fmt, smooth_min

_SAFE = re.compile(r"[^a-z0-9_]")


def spice_name(text: str) -> str:
    """Deterministic SPICE identifier for a label (lowercase, [a-z0-9_])."""
    return _SAFE.sub("_", text.lower()).strip("_")


def node_name(label: str) -> str:
    """SPICE node for a graph node label such as ``C:battery.POS``."""
    return "n_" + spice_name(label)


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
    label: str  # graph node label, e.g. "C:esc.VIN"
    node: str  # SPICE node
    role: str
    voltage_nominal: float | None
    voltage_min: float | None
    voltage_max: float | None
    current_max: float | None


class BuildContext:
    """What a model needs to attach itself to the network."""

    def __init__(self, project: Any, ambient_c: float, reference: str | None = None) -> None:
        self.project = project
        self.ambient_c = ambient_c
        self.reference = reference  # graph label mapped to SPICE ground
        self._nodes: dict[str, str] = {}

    def node(self, label: str) -> str:
        found = self._nodes.get(label)
        if found is None:
            found = self._nodes[label] = GROUND if label == self.reference else node_name(label)
        return found

    def pins(self, component_id: str, ctype: Any) -> dict[str, PinRef]:
        result = {}
        for name, pin in ctype.pins.items():
            label = f"C:{component_id}.{name}"
            result[name] = PinRef(name, label, self.node(label), pin.role, pin.voltage_nominal, pin.voltage_min,
                                  pin.voltage_max, pin.current_max)
        return result


THERMAL_AMBIENT_NODE = "tamb"


class BehaviorModel:
    """A component's electrical behavior.

    Lifecycle: ``build(netlist)`` emits the subcircuit (once per deck flavour);
    per co-simulation step ``begin_step`` sets ``controls`` (the values of the
    model's ``EXTERNAL`` sources), ngspice integrates the circuit to the step
    boundary, ``end_step`` advances device logic and returns events, and
    ``outputs`` reports the solution. ``dt=None`` means a static operating
    point: no timers advance and no device state changes."""

    model = ""
    # Controls that are continuous physical quantities owned by another domain
    # (e.g. MuJoCo speeds): fed to ngspice as a first-order hold (linear ramp
    # across the step) instead of a zero-order-hold jump.
    ramped: tuple[str, ...] = ()

    def __init__(self, component_id: str, ctype: Any, params: dict[str, Any], ctx: BuildContext) -> None:
        self.id = component_id
        self.ctype = ctype
        self.params = Params(component_id, params)
        self.ctx = ctx
        self.pins = ctx.pins(component_id, ctype) if ctype is not None else {}
        self.ambient_c = ctx.ambient_c
        self.inst = "x" + spice_name(component_id)
        self.controls: dict[str, float] = {}
        self.sources: dict[str, str] = {}  # control name -> EXTERNAL source name
        self.states: dict[str, float] = {}  # state node -> value (initial conditions of the next transient)

    # Naming -------------------------------------------------------------------
    def local(self, name: str) -> str:
        """Top-level node owned by this model (state ports, readouts)."""
        return f"m_{spice_name(self.id)}_{name}"

    def internal(self, node: str) -> str:
        """Output-vector name of a node inside this model's subcircuit."""
        return f"{self.inst}.{node}"

    def sense(self, source: str) -> str:
        """Vector name prefix of a 0 V current sense source inside the subcircuit."""
        return f"v.{self.inst}.{source}"

    def control(self, net: Netlist, name: str, value: float = 0.0) -> str:
        """Declare an ``EXTERNAL`` source for control ``name``; returns its node."""
        source = f"x_{spice_name(self.id)}_{name}"
        node = net.external(source)
        self.sources[name] = f"v{source}"
        self.controls.setdefault(name, value)
        return node

    def thermal(self, net: Netlist, temperature_node: str, heat_capacity: float, theta: float,
                power: str, lines: list[str]) -> None:
        """First-order thermal RC: C dT/dt = P - (T - T_amb) / theta (transient only;
        in operating points components stay at ambient)."""
        if net.transient:
            lines += [f"bheat 0 {temperature_node} i = {power}",
                      f"cth {temperature_node} 0 {fmt(heat_capacity)}",
                      f"rth {temperature_node} {THERMAL_AMBIENT_NODE} {fmt(theta)}"]
        else:
            lines.append(f"rth {temperature_node} {THERMAL_AMBIENT_NODE} {fmt(theta)}")

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

    # Lifecycle ----------------------------------------------------------------
    def build(self, net: Netlist) -> None:
        pass

    def initialize(self, initial: dict[str, Any]) -> None:
        pass

    def begin_step(self, dt: float | None, inputs: dict[str, Any], time: float) -> None:
        pass

    def resolve(self, sol: Solution) -> bool:
        """Operating points only: update an active-set state from the solution;
        True requests a re-solve of the same point."""
        return False

    def end_step(self, dt: float | None, sol: Solution, time: float) -> list[Event]:
        return []

    def outputs(self, sol: Solution) -> dict[str, Any]:
        return {}

    def time_sources(self, start: float, end: float) -> tuple[dict[str, Any], list[float]]:
        """EXTERNAL sources evaluated at ngspice's own time points within
        (start, end] (ngspice clock), and their corner times."""
        return {}, []

    def watches(self) -> list[tuple[str, str]]:
        """Vectors reduced over every accepted point of a step: (name, "min" | "max" | "mean")."""
        return []

    def capture_states(self, sol: Solution) -> None:
        for node in self.states:
            self.states[node] = sol.v(node)

    # State snapshot for static operating points.
    def snapshot(self) -> dict[str, Any]:
        state = {k: v for k, v in self.__dict__.items() if k.startswith("s_")}
        state["states"] = dict(self.states)
        state["controls"] = dict(self.controls)
        return state

    def restore(self, state: dict[str, Any]) -> None:
        state = dict(state)
        self.states = dict(state.pop("states", self.states))
        self.controls = dict(state.pop("controls", self.controls))
        self.__dict__.update(state)


#: Relative width of the constant-current load's knee (smooth min, see :func:`constant_current`).
KNEE_WIDTH = 0.02


def constant_current(current: float, v_knee: float, v: str) -> str:
    """I = I0 above ``v_knee``; I0 V / v_knee below (supply collapse keeps Newton well posed).

    The corner is a smooth min, I = I0 min_s(1, V / V_knee) (2 % wide: 1.4 % low exactly at the knee,
    < 0.01 % from 6 % above it): a hard corner stalls ngspice's step control when a rail decays slowly
    through the knee (e.g. on hold-up capacitance)."""
    ratio = f"max({v},{fmt(-v_knee)})/{fmt(v_knee)}"
    return f"{fmt(current)}*{smooth_min('1', ratio, KNEE_WIDTH)}"


def gated_power(power: str, v_on: float, width: float, v: str) -> str:
    """Constant-power input behind an input UVLO: I = P / V x (1 + tanh((V - V_on) / w)) / 2.

    Above V_on + 3 w this is P / V to < 0.25 %; below V_on the converter is off
    and draws nothing. Unlike a resistive continuation, a current-limited
    supply then has a single DC solution (no spurious collapsed state), and the
    expression is smooth for Newton."""
    return f"({power})/max({v},0.5)*0.5*(1+tanh(({v}-{fmt(v_on)})/{fmt(width)}))"
