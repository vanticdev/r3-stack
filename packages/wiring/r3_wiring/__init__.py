"""R3 wiring: 3D harness routing, physical harness checks and cable slack simulation.

Electrical connectivity lives in :mod:`r3_model.graph`; this package owns the
physical geometry of every wire and harness, linked to the graph by wire and
harness ids.
"""

from .cable_sim import CableSimulator
from .obstacles import ObstacleSet, build_obstacles
from .router import HarnessRouter, RouterOptions, RoutingResult, route_project

__all__ = ["CableSimulator", "HarnessRouter", "ObstacleSet", "RouterOptions", "RoutingResult", "build_obstacles", "route_project"]
