"""R3 digital twin: co-simulation of vehicle physics (MuJoCo), electrical network,
wiring slack, 2.4 GHz radio, register-level sensors and the real robot software
running in lockstep against the twin HAL server.

Entry points:

* :func:`r3_sim.scenario.run_scenario` runs one ``simulation.scenarios`` entry.
* :func:`r3_sim.stage.run` is the pipeline stage (all scenarios).
* :mod:`r3_sim.vehicle` builds the MJCF vehicle model from the spec.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
