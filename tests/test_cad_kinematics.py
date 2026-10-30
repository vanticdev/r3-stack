"""Steering linkage solver, magnet physics and mate-connector correction math."""

import math

import numpy as np
import pytest

from r3_onshape.assembly import correction
from r3_onshape.fit import axial_field, dipole_field
from r3_onshape.kinematics import Linkage, Side, ideal_inner, steering_table

MM = 1e-3


def linkage(horn_x: float = .04, ball_y: float = .01) -> Linkage:
    """A symmetric table-top linkage, with no complete robot layout."""
    sides = []
    for sign, joint in ((1, "left"), (-1, "right")):
        pivot = np.array([0, .08 * sign, .02])
        lever = np.array([-.03, .08 * sign, .02])
        horn = np.array([-.03, ball_y * sign, .02])
        sides.append(Side(joint, joint, pivot, np.array([0., 0, 1]), lever, horn, (-.7, .7)))
    return Linkage(np.array([-.03 + horn_x, 0, .02]), np.array([0., 0, 1]), sides, .4, .16)


def test_synthetic_linkage_solves_both_sides_and_mirrors():
    mechanism = linkage()
    table = steering_table(mechanism, (-.4, .4), samples=31)
    assert {s.status for s in table["solutions"].values()} == {"ok"}
    full = table["rows"][-1]
    assert full["left"] == pytest.approx(.5864667917, abs=.005)
    assert full["right"] == pytest.approx(.5126395560, abs=.01)
    expected_inner = math.atan(1 / (1 / math.tan(full["right"]) - .16 / .4))
    expected_percent = 100 * (full["left"] - full["right"]) / (expected_inner - full["right"])
    assert full["ackermann_pct"] == pytest.approx(expected_percent)
    assert table["rows"][0]["right"] == pytest.approx(-full["left"], abs=1e-9)


def test_lockup_and_toggle_are_detected():
    locked = steering_table(linkage(), (-.9, .9), samples=21)
    assert "lockup" in {s.status for s in locked["solutions"].values()}
    toggled = steering_table(linkage(.02, .01), (-1.3, 1.3), samples=201)
    assert "toggle" in {s.status for s in toggled["solutions"].values()}


def test_ideal_ackermann_inner_angle():
    # cot(d_i) = cot(d_o) - t/l
    outer = 0.3
    inner = ideal_inner(outer, 0.16, 0.4)
    assert 1 / math.tan(inner) == pytest.approx(1 / math.tan(outer) - 0.16 / 0.4)


def test_axial_magnet_field_matches_closed_form_limits():
    # At the face of a long magnet the field tends to Br/2.
    assert axial_field(1.44, 1e-3, 1.0, 0.0) == pytest.approx(0.72, rel=1e-3)
    # Magnetic coupon: 2x1 mm N52 at 1.0 mm air gap + 0.5 mm die depth gives ~69 mT >> B_OP 17.5 mT.
    assert axial_field(1.44, 1e-3, 1e-3, 1.5e-3) == pytest.approx(0.0694, rel=0.01)
    # Between magnets on a rotating ring the field drops below the 2.5 mT release point.
    assert dipole_field(1.44, 1e-3, 1e-3, 9.5e-3, 1.5e-3, math.pi / 4) < 2.5e-3


def test_connector_correction_aligns_frames():
    a = {"origin": [0.01, 0.02, 0.03], "xAxis": [1, 0, 0], "zAxis": [0, 0, 1]}
    b = {"origin": [0.01, 0.02, 0.025], "xAxis": [0, 1, 0], "zAxis": [0, 0, -1]}
    flip, theta, translation, off_axis = correction(a, b)
    assert flip
    # After the flip B's x is still +y; rotating -90 deg about +z aligns it with +x.
    assert theta == pytest.approx(-math.pi / 2)
    assert translation[2] == pytest.approx(0.005) and off_axis == pytest.approx(0.0, abs=1e-12)
