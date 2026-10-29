"""Hall-effect wheel encoder (open-drain, active low) on a drive axle.

``pulses_per_wheel_rev`` magnets pass the sensor per axle revolution; each
magnet holds the output low for half a pitch (50 % duty). Edge times are found
by linear interpolation of the axle angle inside the simulation step and get
Gaussian timing jitter. With the board pull-up, the line reads 1 when idle.
Zero angle places a magnet at the sensor centre, rather than exactly on a
switching boundary; this matches the nominal aligned magnetic-target assembly.
"""

from __future__ import annotations

import math

import numpy as np


class HallEncoder:
    def __init__(self, pulses_per_rev: int, jitter_s: float, rng: np.random.Generator) -> None:
        self.pulses = max(1, int(pulses_per_rev))
        self.jitter = jitter_s
        self.rng = rng
        self.half_pitch = math.pi / self.pulses  # 2 pi / (2 N): low/high segments
        self.last_angle: float | None = None
        self.level = 1
        self.powered = True

    def _segment(self, angle: float) -> int:
        return math.floor(angle / self.half_pitch + 0.5)

    def update(self, t0: float, t1: float, angle: float) -> list[tuple[float, bool]]:
        """Return line edges (t, rising) between t0 and t1 for the axle angle at t1."""
        if self.last_angle is None:
            self.last_angle = angle
            self.level = 1 if self._segment(angle) % 2 else 0
            return []
        a0, a1 = self.last_angle, angle
        self.last_angle = angle
        s0, s1 = self._segment(a0), self._segment(a1)
        if s0 == s1:
            return []
        edges = []
        step = 1 if s1 > s0 else -1
        for boundary in range(s0 + (1 if step > 0 else 0), s1 + (1 if step > 0 else 0), step):
            theta = (boundary - 0.5) * self.half_pitch
            frac = (theta - a0) / (a1 - a0) if a1 != a0 else 1.0
            t = t0 + min(max(frac, 0.0), 1.0) * (t1 - t0) + float(self.rng.normal(0.0, self.jitter))
            # Even segments are centred on a magnet = output pulled low.
            segment_after = boundary if step > 0 else boundary - 1
            new_level = 1 if segment_after % 2 else 0
            if new_level != self.level:
                edges.append((t, new_level == 1))
                self.level = new_level
        edges.sort()
        return edges
