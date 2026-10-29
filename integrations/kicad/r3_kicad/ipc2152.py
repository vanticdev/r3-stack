"""IPC-2152 conductor sizing.

Two published curve fits of the IPC-2152 data are implemented:

* ``external`` (default): D. Brooks' fit of the IPC-2152 external-conductor
  curves (PCD&F, "Thermal management", 2015)::

      dT = 215.3 * I^2 * W^-1.15 * Th^-1.0        (dT degC, I A, W and Th in mil)

  so  ``I_max = sqrt(dT * Th * W^1.15 / 215.3)``.
* ``universal``: least-squares fit of the IPC-2152 Figure 5-2 universal chart
  (still air, no planes; smps.us)::

      A = (117.555 * dT^-0.913 + 1.15) * I^(0.84 * dT^-0.108 + 1.159)    (A in mil^2)

Neither fit applies the IPC-2152 plane modifier: the GND pour on the opposite
layer lowers the real temperature rise (Brooks reports ~50 % for a plane on the
far side of a 1.6 mm board), so the widths here are conservative.
Copper thickness per IPC: 1 oz = 1.35 mil (34.3 um).
"""

from __future__ import annotations

import math

MIL = 0.0254  # mm
OZ_THICKNESS_MIL = 1.35


def copper_thickness_mil(copper_oz: float) -> float:
    return OZ_THICKNESS_MIL * copper_oz


def max_current(width_mm: float, copper_oz: float = 1.0, delta_t: float = 10.0, model: str = "external") -> float:
    """Continuous current (A) for a trace of ``width_mm`` at ``delta_t`` rise."""
    if width_mm <= 0:
        return 0.0
    thickness = copper_thickness_mil(copper_oz)
    width_mil = width_mm / MIL
    if model == "external":
        return math.sqrt(delta_t * thickness * width_mil ** 1.15 / 215.3)
    if model == "universal":
        area = width_mil * thickness
        k = 117.555 * delta_t ** -0.913 + 1.15
        exponent = 0.84 * delta_t ** -0.108 + 1.159
        return (area / k) ** (1.0 / exponent)
    raise ValueError(f"unknown IPC-2152 model {model!r}")


def required_width(current: float, copper_oz: float = 1.0, delta_t: float = 10.0, model: str = "external") -> float:
    """Minimum trace width (mm) carrying ``current`` with ``delta_t`` rise."""
    if current <= 0:
        return 0.0
    thickness = copper_thickness_mil(copper_oz)
    if model == "external":
        width_mil = (215.3 * current ** 2 / (delta_t * thickness)) ** (1.0 / 1.15)
    elif model == "universal":
        k = 117.555 * delta_t ** -0.913 + 1.15
        exponent = 0.84 * delta_t ** -0.108 + 1.159
        width_mil = k * current ** exponent / thickness
    else:
        raise ValueError(f"unknown IPC-2152 model {model!r}")
    return width_mil * MIL


def via_max_current(drill_mm: float, plating_um: float = 20.0, delta_t: float = 10.0) -> float:
    """Current of one plated via, treating the barrel as an internal conductor of
    equal cross-section (IPC-2152 practice; internal conductors run cooler than
    external ones, so the external fit used here is conservative)."""
    circumference_mil = math.pi * (drill_mm + plating_um / 1000.0) / MIL
    plating_mil = plating_um / 1000.0 / MIL
    return math.sqrt(delta_t * plating_mil * circumference_mil ** 1.15 / 215.3)


def trace_resistance(length_mm: float, width_mm: float, copper_oz: float = 1.0, temperature_c: float = 20.0) -> float:
    """DC resistance (ohm): R = rho * L / A with rho(20 C) = 1.72e-8 ohm*m, alpha = 0.00393 / K."""
    rho = 1.72e-8 * (1 + 0.00393 * (temperature_c - 20.0))
    area = width_mm * 1e-3 * copper_thickness_mil(copper_oz) * MIL * 1e-3
    return rho * length_mm * 1e-3 / area
