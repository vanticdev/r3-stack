"""Hardware failsafe arbiter on the carrier HAT (U4, Microchip ATtiny412-SS).

The Pi's ESC PWM (GPIO18, header pin 12) reaches the ESC only through U4. The
Pi proves it is alive by toggling a heartbeat GPIO (GPIO27, header pin 13) at
the control rate. U4 behaviour (firmware contract; the twin models this class):

* PASS: every edge of ``PI_ESC_PWM`` is mirrored to ``ESC_OUT`` by a pin-change
  interrupt (ATtiny412 at 20 MHz: ~1.5 us latency, < 1 us jitter, estimated
  from the 8-cycle interrupt entry plus the port write).
* A heartbeat *edge* (either direction) older than ``timeout`` (100 ms)
  switches to FAILSAFE. The switch waits for the output to be low, so a pulse
  in flight is never truncated.
* FAILSAFE: TCA0 generates 1500 us neutral pulses every 20 ms (50 Hz) and the
  Pi input is ignored. The status LED (D3) is lit.
* Re-arm: ``rearm_edges`` consecutive heartbeat edges, each within the
  timeout, *and* an input pulse within ``rearm_band`` of neutral. The ESC is
  never handed a non-neutral command on recovery.
* Power: U4 runs from the Pi's 3V3. If the Pi loses power, U4 stops and the
  100 k pull-down holds ``ESC_PWM`` low; the ESC's own signal-loss failsafe
  then applies (no pulses, not a hold of the last command).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ArbiterState(str, Enum):
    PASS = "pass"
    FAILSAFE = "failsafe"


@dataclass(frozen=True)
class ArbiterParams:
    part: str = "ATtiny412-SS"
    supply: str = "3V3"
    timeout: float = 0.100            # s, heartbeat edge age that triggers failsafe (requirement <= 100 ms)
    neutral_us: float = 1500.0
    frame: float = 0.020              # s, failsafe pulse period (50 Hz)
    rearm_edges: int = 3
    rearm_band_us: float = 50.0
    latency_us: float = 1.5           # pass-through edge delay (estimated)
    heartbeat_pin: str = "J_GPIO.13"  # GPIO27
    input_pin: str = "J_GPIO.12"      # GPIO18 (PWM0 channel 2)


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

    def output_pulse(self, t: float, input_us: float | None) -> float | None:
        """Pulse width (us) the ESC sees in the frame starting at ``t``."""
        p = self.params
        if self.state is ArbiterState.PASS and not self._alive(t):
            self._set(t, ArbiterState.FAILSAFE)
            self.good_edges = 0
        elif self.state is ArbiterState.FAILSAFE and self._alive(t) and self.good_edges >= p.rearm_edges:
            if input_us is not None and abs(input_us - p.neutral_us) <= p.rearm_band_us:
                self._set(t, ArbiterState.PASS)
        if self.state is ArbiterState.FAILSAFE:
            return p.neutral_us
        return input_us

    def failsafe_latency(self) -> float:
        """Worst-case time from the last heartbeat edge to the first neutral frame:
        timeout plus up to one frame of waiting for the next frame boundary."""
        return self.params.timeout + self.params.frame
