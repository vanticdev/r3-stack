"""R3 digital twin: co-simulation of robot assembly physics (MuJoCo), electrical network,
wiring slack, 2.4 GHz radio, register-level sensors and the real robot software
running in lockstep against the twin HAL server.

Entry points:

* :func:`r3_sim.scenario.run_scenario` runs one ``simulation.scenarios`` entry.
* :func:`r3_sim.stage.run` is the pipeline stage (all scenarios).
* :func:`r3_sim.assembly.build_assembly_model` builds any robot without a vehicle adapter.
* :class:`r3_sim.dynamics.RobotDynamics` runs the shared monitored physics.
* :mod:`r3_sim.vehicle` builds the MJCF vehicle model from the spec.
"""

from __future__ import annotations

from .headless import configure as _configure_headless

_configure_headless()  # before any submodule imports MuJoCo

__all__ = ["__version__"]

__version__ = "0.1.0"
