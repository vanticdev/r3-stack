"""Routing-floor, pass-through and support-placement behaviour behind the RC-car wiring layout."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_wiring.geometry import Box
from r3_wiring.obstacles import Obstacle, base_plate
from r3_wiring.passthroughs import _inside, cad_slots
from r3_wiring.supports import Post, _greedy, _tie_loop

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


def _box(name: str, lo: list[float], hi: list[float]) -> Obstacle:
    lo_, hi_ = np.asarray(lo), np.asarray(hi)
    shape = Box((lo_ + hi_) / 2, np.eye(3), (hi_ - lo_) / 2)
    return Obstacle(name, "structure", shape, shape, 0.002, False, "robot", True)


def test_base_plate_is_the_largest_footprint_not_a_strap_under_it():
    # A battery strap loops under the 3 mm plate (it reaches lower than the plate) and up over the battery;
    # taking it as the floor put the routing volume's floor at the top of the loop (54.5 mm on the RC car).
    plate = _box("chassis", [-0.04, -0.065, 0.025], [0.30, 0.065, 0.028])
    strap = _box("battery_strap", [0.13, -0.025, 0.0235], [0.15, 0.025, 0.0545])
    deck = _box("deck", [0.035, -0.055, 0.075], [0.205, 0.055, 0.078])
    assert base_plate([strap, deck, plate]).component == "chassis"
    assert base_plate([]) is None


def test_lead_slot_in_the_deck_is_a_pass_through():
    project = Project.load(RC_CAR)
    slots = {s["id"]: s for s in cad_slots(project)}
    slot = slots["lead_slot"]
    assert slot["component"] == "deck"
    centre = slot["entry"] + slot["axis"] * slot["depth"] / 2
    through = centre[None] + np.outer(np.linspace(-0.01, 0.01, 41), slot["axis"])
    assert _inside(through, slot).any()
    beside = through + slot["along"] * (slot["length"] / 2 + slot["width"] / 2 + 0.002)
    assert not _inside(beside, slot).any()


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
