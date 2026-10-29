"""R3 wiring: 3D harness routing, physical harness checks and cable slack simulation.

Cable dynamics are MuJoCo's (``mujoco.elasticity.cable``): :mod:`.mujoco_cables`
describes the routed harnesses as MuJoCo cables for any MuJoCo world (the
vehicle in ``r3_sim``, or the standalone shaker in :mod:`.cable_sim`).

Electrical connectivity lives in :mod:`r3_model.graph`; this package owns the
physical geometry of every wire and harness, linked to the graph by wire and
harness ids.
"""

from .cable_sim import CableSimulator
from .mujoco_cables import CableMonitor, CablePlan, plan_cables
from .obstacles import ObstacleSet, build_obstacles
from .router import HarnessRouter, RouterOptions, RoutingResult, route_project

__all__ = ["CableMonitor", "CablePlan", "CableSimulator", "HarnessRouter", "plan_cables", "ObstacleSet", "RouterOptions", "RoutingResult", "build_obstacles", "route_project"]
