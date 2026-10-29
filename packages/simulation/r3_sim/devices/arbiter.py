"""Hardware failsafe arbiter on a board (``failsafe_arbiter`` internal link), in the twin.

The behaviour is the board's firmware contract, :class:`r3_kicad.arbiter.FailsafeArbiter`
(one model shared by the KiCad board stage and the twin). This wrapper adds what the
co-simulation needs around it:

* heartbeat edges come from the software's writes to the heartbeat GPIO line
  (a write that changes the level is one edge, as on the physical pin);
* the arbiter decides once per output frame (``frame`` = 20 ms, aligned to the
  PWM period starting at t = 0): in PASS the ESC sees the Pi's pulse, in
  FAILSAFE the arbiter's own ``neutral_us`` pulses;
* it runs from the computer's 3V3: unpowered, its output is held low by the
  pull-down (no pulses, the ESC's own signal-loss failsafe applies) and it boots
  again in FAILSAFE when power returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from r3_kicad.arbiter import ArbiterParams, ArbiterState, FailsafeArbiter
from r3_model.units import to_si


def arbiter_params(failsafe: dict[str, Any] | None, timeout: float | None = None) -> ArbiterParams:
    """:class:`ArbiterParams` from a board's ``behavior.params.failsafe`` block (catalog units)."""
    failsafe = failsafe or {}
    defaults = ArbiterParams()
    values: dict[str, Any] = {}
    for key in ("timeout", "frame"):
        if failsafe.get(key) is not None:
            values[key] = float(to_si(failsafe[key]))
    for key in ("neutral_us", "rearm_band_us"):
        if failsafe.get(key) is not None:
            values[key] = float(failsafe[key])
    if failsafe.get("rearm_edges") is not None:
        values["rearm_edges"] = int(failsafe["rearm_edges"])
    if failsafe.get("part"):
        values["part"] = str(failsafe["part"])
    if timeout and "timeout" not in values:
        values["timeout"] = float(timeout)
    return ArbiterParams(**{**defaults.__dict__, **values})


@dataclass(eq=False)  # identity semantics: one device may sit behind several bindings
class ArbiterDevice:
    board: str
    params: ArbiterParams
    model: FailsafeArbiter = field(init=False)
    powered: bool = True
    level: int | None = None  # last heartbeat level written by the software
    edges: int = 0
    last_edge_t: float | None = None
    next_frame: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.model = FailsafeArbiter(self.params)

    @property
    def failsafe(self) -> bool:
        return self.model.state is ArbiterState.FAILSAFE

    def heartbeat_write(self, t: float, value: int) -> None:
        """The software wrote ``value`` to the heartbeat line at ``t``."""
        value = 1 if value else 0
        if self.level is not None and value != self.level and self.powered:
            self.model.heartbeat_edge(t)
            self.edges += 1
            self.last_edge_t = t
        self.level = value

    def set_powered(self, t: float, powered: bool) -> None:
        if powered and not self.powered:
            self.model = FailsafeArbiter(self.params)  # boots in FAILSAFE
            self.level, self.last_edge_t = None, None
            self.events.append({"t": t, "event": "arbiter_boot", "board": self.board})
        self.powered = powered

    def output(self, t: float, input_us: float) -> float:
        """Pulse width (us) the ESC sees at ``t`` for the Pi's commanded ``input_us`` (0: no pulses)."""
        if not self.powered:
            return 0.0
        while t + 1e-9 >= self.next_frame:
            frame_t = self.next_frame
            self.next_frame += self.params.frame
            before = self.model.state
            self.model.output_pulse(frame_t, input_us if input_us > 0 else None)
            if self.model.state is not before:
                self.events.append({"t": frame_t, "event": f"arbiter_{self.model.state.value}", "board": self.board,
                                    "last_heartbeat_edge_t": self.last_edge_t})
        if self.failsafe:
            return self.params.neutral_us
        return input_us
