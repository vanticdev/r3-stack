"""Scripted pilot at the ELRS transmitter (scenario ``pilot`` block).

Stick tracks are ``[[t, value], ...]`` held piecewise constant (sticks jump
between scripted positions), values in [-1, 1]. Channel mapping (1-based CRSF
channels) defaults to a surface radio: ch1 steering (+1 = left, matching the
robot frame's positive yaw), ch2 throttle, AUX1 = ch5 arm switch, overridable
by ``software.config.crsf_channels``. ``arm_at`` sets the arm channel high
(2000 us, > 1700 us) from that time on; before it the switch reads 1000 us.
Stick value s maps to 1500 + 512 s us: full deflection is the CRSF channel limit
988 / 2012 us (ticks 172 / 1811), as an EdgeTX/OpenTX handset sends at 100 % travel.
"""

from __future__ import annotations

import bisect
from typing import Any

from r3_model.units import to_si

#: Full stick deflection relative to centre (us): CRSF 988..2012 us.
STICK_HALF_RANGE_US = 512.0


class Pilot:
    def __init__(self, pilot: dict[str, Any], config: dict[str, Any]) -> None:
        mapping = dict(config.get("crsf_channels") or {})
        self.channel_map = {
            "steering": int(mapping.get("steering", 1)),
            "throttle": int(mapping.get("throttle", 2)),
            "arm": int(mapping.get("arm", config.get("arm_channel", 5))),
            "steering_positive": str(mapping.get("steering_positive", "left")),
        }
        self.tracks = {name: self._track(pilot.get(name)) for name in ("throttle", "steering")}
        arm_at = pilot.get("arm_at")
        self.arm_at = None if arm_at is None else to_si(arm_at)
        disarm_at = pilot.get("disarm_at")
        self.disarm_at = None if disarm_at is None else to_si(disarm_at)

    @staticmethod
    def _track(points: Any) -> tuple[list[float], list[float]]:
        if not points:
            return [0.0], [0.0]
        ordered = sorted((to_si(t), float(v)) for t, v in points)
        return [t for t, _ in ordered], [v for _, v in ordered]

    def stick(self, name: str, t: float) -> float:
        times, values = self.tracks[name]
        index = bisect.bisect_right(times, t) - 1
        return max(-1.0, min(1.0, values[index] if index >= 0 else 0.0))

    def armed(self, t: float) -> bool:
        if self.arm_at is None or t < self.arm_at:
            return False
        return self.disarm_at is None or t < self.disarm_at

    def channels_us(self, t: float) -> list[float]:
        channels = [1500.0] * 16
        channels[self.channel_map["steering"] - 1] = 1500.0 + STICK_HALF_RANGE_US * self.stick("steering", t)
        channels[self.channel_map["throttle"] - 1] = 1500.0 + STICK_HALF_RANGE_US * self.stick("throttle", t)
        channels[self.channel_map["arm"] - 1] = 2000.0 if self.armed(t) else 1000.0
        return channels
