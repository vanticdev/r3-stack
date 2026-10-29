"""Electrical digital twin: nonlinear MNA network of wires, connectors and
component behavior models, plus the ``electrical`` pipeline stage."""

from .mna import Circuit, SolverError
from .system import ElectricalSystem

__all__ = ["Circuit", "ElectricalSystem", "SolverError"]
