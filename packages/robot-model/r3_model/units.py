"""Unit parsing for authored specifications.

Internally every quantity is a float in SI base units (m, kg, s, A, V, ohm, W,
rad, Hz). Authors may write plain numbers (already SI) or strings with an
explicit unit such as ``"25 mm"``, ``"5000 mAh"``, or ``"90 deg"``.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Any

from pydantic import BeforeValidator

_SCALE: dict[str, float] = {
    # length
    "m": 1.0, "cm": 1e-2, "mm": 1e-3, "um": 1e-6, "km": 1e3, "in": 0.0254,
    # mass
    "kg": 1.0, "g": 1e-3, "mg": 1e-6,
    # time
    "s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "min": 60.0, "h": 3600.0,
    # electrical
    "V": 1.0, "mV": 1e-3, "A": 1.0, "mA": 1e-3, "uA": 1e-6,
    "ohm": 1.0, "mohm": 1e-3, "kohm": 1e3, "W": 1.0, "mW": 1e-3,
    "Ah": 3600.0, "mAh": 3.6, "F": 1.0, "uF": 1e-6, "nF": 1e-9, "H": 1.0, "mH": 1e-3, "uH": 1e-6,
    # frequency
    "Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9,
    # angle
    "rad": 1.0, "deg": math.pi / 180.0,
    # speed / misc
    "m/s": 1.0, "km/h": 1 / 3.6, "rpm": 2 * math.pi / 60.0, "N": 1.0, "Nm": 1.0,
    "kg/m3": 1.0, "g/cm3": 1000.0,
}

_PATTERN = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z/0-9]*)\s*$")


def to_si(value: Any) -> float:
    """Convert a number or ``"<number> <unit>"`` string to an SI float."""
    if isinstance(value, bool):
        raise ValueError("booleans are not quantities")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _PATTERN.match(value)
        if not match:
            raise ValueError(f"cannot parse quantity {value!r}")
        number, unit = match.groups()
        if not unit:
            return float(number)
        if unit not in _SCALE:
            raise ValueError(f"unknown unit {unit!r} in {value!r}")
        return float(number) * _SCALE[unit]
    raise ValueError(f"expected a quantity, got {type(value).__name__}")


def _vector(value: Any) -> list[float]:
    if isinstance(value, (list, tuple)):
        return [to_si(item) for item in value]
    raise ValueError("expected a list of quantities")


Quantity = Annotated[float, BeforeValidator(to_si)]
Vector = Annotated[list[float], BeforeValidator(_vector)]
