"""Simulation worker: runs digital-twin scenario jobs with pinned inputs.

``python -m r3_sim_worker <project> [--scenario id ...] [--software-cmd ...]``
"""

from .stage import run

__all__ = ["run"]
