"""Routing-floor, pass-through and support-placement behaviour behind the bench wiring layout."""

from __future__ import annotations

import math

import numpy as np
import pytest

from r3_wiring.geometry import Box
from r3_wiring.obstacles import Obstacle, base_plate
from r3_wiring.supports import Post, _greedy, _tie_loop


def _box(name: str, lo: list[float], hi: list[float]) -> Obstacle:
    lo_, hi_ = np.asarray(lo), np.asarray(hi)
    shape = Box((lo_ + hi_) / 2, np.eye(3), (hi_ - lo_) / 2)
    return Obstacle(name, "structure", shape, shape, 0.002, False, "robot", True)


def test_base_plate_is_the_largest_footprint_not_a_strap_under_it():
    # A small loop reaches below a broad fixture plate; height alone must not
    # select it as the routing floor. A smaller sensor shelf sits above both.
    plate = _box("base", [-.1, -.1, .01], [.1, .1, .015])
    strap = _box("loop", [-.01, -.02, .008], [.01, .02, .04])
    shelf = _box("shelf", [-.03, -.03, .06], [.03, .03, .064])
    assert base_plate([strap, shelf, plate]).component == "base"
    assert base_plate([]) is None


def _candidate(arc: float, tilt: float = 0.0) -> dict:
    return {"arc": arc, "tilt": tilt}


def test_greedy_carries_spans_across_unheld_junctions():
    candidates = [_candidate(a) for a in np.arange(0.01, 0.05, 0.002)]
    # A 50 mm segment under a 60 mm limit needs no support between held ends ...
    assert _greedy(candidates, 0.05, 0.06) == []
    # ... but one ending in a via (unheld) has only half the limit for its tail.
    chosen = _greedy(candidates, 0.05, 0.06, head_held=True, tail_held=False)
    assert chosen and 0.05 + 0.03 - chosen[-1]["arc"] <= 0.06 + 1e-9


def test_greedy_prefers_the_straightest_spot_near_its_reach():
    candidates = [_candidate(0.040, tilt=0.4), _candidate(0.045, tilt=0.05), _candidate(0.050, tilt=0.3)]
    chosen = _greedy(candidates, 0.10, 0.055)
    assert chosen[0]["arc"] == pytest.approx(0.045)


def test_tie_loop_runs_outside_a_hollow_post():
    # A 6 mm mast tube with a 4 mm coax bore: the tie hugs the tube and the bundle, never the bore.
    post = Post("mast", np.zeros(3), np.array([0.0, 0.0, 1.0]), 0.003, 0.08)
    r = 0.0012
    mount = np.array([0.003, 0.0, 0.04])
    loop = _tie_loop(post, mount, np.array([1.0, 0.0, 0.0]), r)
    radial = np.linalg.norm(loop[:, :2], axis=1)
    assert radial.min() >= 0.003 - 1e-9
    assert np.linalg.norm(loop[:, :2] - np.array([0.003 + r, 0.0]), axis=1).max() <= 0.003 * 2 + 2 * r + 1e-3
    assert post.holds(np.array([0.003, 0.0, 0.04])) and not post.holds(np.array([0.003, 0.0, 0.09]))
    assert not post.holds(np.array([0.0, 0.0, 0.04]))  # the bore axis is not on the side face
    assert math.isinf(Post("standoff", np.zeros(3), np.array([0, 0, 1.0]), 0.0027, math.inf).length)
