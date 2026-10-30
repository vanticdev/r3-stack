"""PWM failsafe state machine, driven by the board's declared heartbeat input.

Pulse and reverse/brake semantics follow a compatible ESC's documented protocol.
The board's catalog supplies its pins, supply, timing and optional brake pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from r3_model.units import to_si


class ArbiterState(str, Enum):
    PASS = "pass"
    FAILSAFE = "failsafe"


@dataclass(frozen=True)
class ArbiterParams:
    part: str = ""
    supply: str = "VCC"
    timeout: float = 0.100            # s, heartbeat edge age that triggers failsafe (requirement <= 100 ms)
    neutral_us: float = 1500.0
    frame: float = 0.020              # s, failsafe pulse period (50 Hz)
    rearm_edges: int = 3
    rearm_band_us: float = 50.0
    latency_us: float = 1.5           # pass-through edge delay (estimated)
    heartbeat_pin: str = "HEARTBEAT"
    input_pin: str = "PWM_IN"
    brake_us: float = 1000.0          # full reverse stick: the ESC's full brake (QuicRun brake force 100 %)
    brake_hold: float = 3.0           # s of brake pulses before neutral (0: neutral at once, the original contract)
    brake_pattern_us: tuple[float, ...] = ()  # optional fixed frame-by-frame brake modulation; never neutral
    deadband_us: float = 15.0         # ESC neutral deadband (QuicRun 1060: 3 % of the 500 us half range)
    esc_reverse_delay: float = 0.1    # s at neutral after a brake before the ESC accepts reverse (catalog)


def arbiter_params(failsafe: dict[str, Any] | None, timeout: float | None = None) -> ArbiterParams:
    """:class:`ArbiterParams` from a board's ``behavior.params.failsafe`` block (catalog units)."""
    failsafe = failsafe or {}
    defaults = ArbiterParams()
    values: dict[str, Any] = {}
    for key in ("timeout", "frame", "brake_hold", "esc_reverse_delay"):
        if failsafe.get(key) is not None:
            values[key] = float(to_si(failsafe[key]))
    for key in ("neutral_us", "rearm_band_us", "brake_us", "deadband_us"):
        if failsafe.get(key) is not None:
            values[key] = float(failsafe[key])
    if failsafe.get("brake_pattern_us") is not None:
        values["brake_pattern_us"] = tuple(float(v) for v in failsafe["brake_pattern_us"])
    if failsafe.get("rearm_edges") is not None:
        values["rearm_edges"] = int(failsafe["rearm_edges"])
    for key in ("part", "supply", "heartbeat_pin", "input_pin"):
        if failsafe.get(key):
            values[key] = str(failsafe[key])
    if timeout and "timeout" not in values:
        values["timeout"] = float(timeout)
    return ArbiterParams(**{**defaults.__dict__, **values})


class EscDrive(str, Enum):
    """The ESC drive state U4 mirrors from the pulses it passes (QuicRun Fwd/Rev/Brk)."""

    NEUTRAL = "neutral"
    FORWARD = "forward"
    BRAKE = "brake"
    REVERSE = "reverse"


@dataclass
class FailsafeArbiter:
    """Event-level model: call :meth:`heartbeat_edge` on each heartbeat toggle and
    :meth:`output_pulse` once per 20 ms frame with the Pi's commanded pulse width
    (``None`` when the Pi output stays low)."""

    params: ArbiterParams = field(default_factory=ArbiterParams)
    state: ArbiterState = ArbiterState.FAILSAFE   # boots in failsafe until the Pi proves itself
    last_edge: float | None = None
    good_edges: int = 0
    transitions: list[tuple[float, ArbiterState]] = field(default_factory=list)
    esc: EscDrive = EscDrive.NEUTRAL          # mirrored ESC drive state
    last_active: EscDrive = EscDrive.NEUTRAL  # last non-neutral ESC state
    neutral_since: float = float("-inf")
    brake_until: float = float("-inf")         # FAILSAFE frames before this time carry ``brake_us``

    def __post_init__(self) -> None:
        if any(pulse >= self.params.neutral_us - self.params.deadband_us for pulse in self.params.brake_pattern_us):
            raise ValueError("every modulated brake pulse must stay outside neutral to avoid arming reverse")

    def heartbeat_edge(self, t: float) -> None:
        if self.last_edge is not None and t - self.last_edge <= self.params.timeout:
            self.good_edges += 1
        else:
            self.good_edges = 1
        self.last_edge = t

    def _alive(self, t: float) -> bool:
        return self.last_edge is not None and t - self.last_edge <= self.params.timeout

    def _set(self, t: float, state: ArbiterState) -> None:
        if state is not self.state:
            self.state = state
            self.transitions.append((t, state))

    def _negative_is_brake(self, t: float) -> bool:
        """Would a reverse-side pulse at ``t`` be the ESC's brake (not reverse)?"""
        if self.esc in (EscDrive.FORWARD, EscDrive.BRAKE):
            return True
        if self.esc is EscDrive.NEUTRAL:
            return self.last_active is EscDrive.FORWARD or t - self.neutral_since < self.params.esc_reverse_delay
        return False

    def _track(self, t: float, pulse_us: float | None) -> None:
        """Advance the mirrored ESC state with the pulse the ESC receives in this frame (None: no pulse)."""
        p = self.params
        if pulse_us is None:
            return
        if pulse_us > p.neutral_us + p.deadband_us:
            self.esc = self.last_active = EscDrive.FORWARD
        elif pulse_us < p.neutral_us - p.deadband_us:
            if self.esc not in (EscDrive.BRAKE, EscDrive.REVERSE):
                self.esc = EscDrive.BRAKE if self._negative_is_brake(t) else EscDrive.REVERSE
            self.last_active = self.esc
        elif self.esc is not EscDrive.NEUTRAL:
            self.esc, self.neutral_since = EscDrive.NEUTRAL, t

    def output_pulse(self, t: float, input_us: float | None) -> float | None:
        """Pulse width (us) the ESC sees in the frame starting at ``t``."""
        p = self.params
        if self.state is ArbiterState.PASS and not self._alive(t):
            self._set(t, ArbiterState.FAILSAFE)
            self.good_edges = 0
            self.brake_until = t + p.brake_hold if p.brake_hold > 0 and self._negative_is_brake(t) else float("-inf")
        elif self.state is ArbiterState.FAILSAFE and self._alive(t) and self.good_edges >= p.rearm_edges:
            if input_us is not None and abs(input_us - p.neutral_us) <= p.rearm_band_us:
                self._set(t, ArbiterState.PASS)
                self.brake_until = float("-inf")
        if self.state is ArbiterState.FAILSAFE:
            out = p.brake_us if t < self.brake_until else p.neutral_us
            if t < self.brake_until and p.brake_pattern_us:
                elapsed = t - (self.brake_until - p.brake_hold)
                frame = int((elapsed + 1e-9) / p.frame)
                out = p.brake_pattern_us[frame % len(p.brake_pattern_us)]
        else:
            out = input_us
        self._track(t, out)
        return out

    def braking(self, t: float) -> bool:
        """FAILSAFE and still inside the active-brake hold at ``t``."""
        return self.state is ArbiterState.FAILSAFE and t < self.brake_until

    def failsafe_latency(self) -> float:
        """Worst-case time from the last heartbeat edge to the first failsafe (brake or neutral) frame:
        timeout plus up to one frame of waiting for the next frame boundary."""
        return self.params.timeout + self.params.frame
