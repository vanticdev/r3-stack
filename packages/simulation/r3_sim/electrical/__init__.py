"""Electrical digital twin: the robot's wiring, connectors and component
behavior models as an ngspice circuit (shared library, lockstep with the
co-simulation clock), plus the ``electrical`` pipeline stage."""

from .ngspice import NgSpiceError
from .system import ElectricalSystem

__all__ = ["ElectricalSystem", "NgSpiceError"]
