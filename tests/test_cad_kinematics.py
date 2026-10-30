"""Steering linkage solver, magnet physics and mate-connector correction math."""

import math

import numpy as np
import pytest

from r3_onshape.assembly import correction
from r3_onshape.fit import axial_field, dipole_field
from r3_onshape.kinematics import Linkage, Side, ideal_inner, steering_table

MM = 1e-3


def linkage(horn_x: float, ball_y: float, arm: float = 25.0, alpha_deg: float = 0.0) -> Linkage:
    alpha = math.radians(alpha_deg)
    sides = []
    for sign, joint in ((1, "steer_fl"), (-1, "steer_fr")):
        kingpin = np.array([260.0, 67.5 * sign, 0.0]) * MM
        knuckle = np.array([260 - arm * math.cos(alpha), sign * (67.5 - arm * math.sin(alpha)), 82.6]) * MM
        horn = np.array([260 - arm * math.cos(alpha), sign * ball_y, 82.6]) * MM
        sides.append(Side(joint, joint, kingpin, np.array([0.0, 0.0, 1.0]), knuckle, horn, (-0.45, 0.45)))
    servo = np.array([260 - arm * math.cos(alpha) + horn_x, 0.0, 0.0]) * MM
    return Linkage(servo, np.array([0.0, 0.0, 1.0]), sides, 0.26, 0.135)


def test_bench_linkage_reaches_limit_with_ackermann():
    table = steering_table(linkage(19.8, 6.0), (-math.radians(30), math.radians(30)), samples=31)
    assert {s.status for s in table["solutions"].values()} == {"ok"}
    full = table["rows"][-1]
    assert full["steer_fl"] == pytest.approx(0.445, abs=0.005)  # inner wheel just inside the 0.45 rad stop
    assert full["steer_fr"] == pytest.approx(0.367, abs=0.01)
    assert 90 < full["ackermann_pct"] < 105
    # Symmetric: right lock mirrors left lock.
    assert table["rows"][0]["steer_fr"] == pytest.approx(-full["steer_fl"], abs=1e-9)


def test_lockup_and_toggle_are_detected():
    locked = steering_table(linkage(12.5, 12.0, alpha_deg=14.55), (-math.radians(51), math.radians(51)), samples=21)
    assert "lockup" in {s.status for s in locked["solutions"].values()}
    toggled = steering_table(linkage(17.0, 6.0, alpha_deg=14.55), (-math.radians(51), math.radians(51)), samples=21)
    assert "toggle" in {s.status for s in toggled["solutions"].values()}


def test_ideal_ackermann_inner_angle():
    # cot(d_i) = cot(d_o) - t/l
    outer = 0.3
    inner = ideal_inner(outer, 0.135, 0.26)
    assert 1 / math.tan(inner) == pytest.approx(1 / math.tan(outer) - 0.135 / 0.26)


def test_axial_magnet_field_matches_closed_form_limits():
    # At the face of a long magnet the field tends to Br/2.
    assert axial_field(1.44, 1e-3, 1.0, 0.0) == pytest.approx(0.72, rel=1e-3)
    # Encoder design: 2x1 mm N52 at 1.0 mm air gap + 0.5 mm die depth gives ~69 mT >> B_OP 17.5 mT.
    assert axial_field(1.44, 1e-3, 1e-3, 1.5e-3) == pytest.approx(0.0694, rel=0.01)
    # Between magnets on the 20 mm ring the field drops below the 2.5 mT release point.
    assert dipole_field(1.44, 1e-3, 1e-3, 9.5e-3, 1.5e-3, math.pi / 4) < 2.5e-3


def test_connector_correction_aligns_frames():
    a = {"origin": [0.01, 0.02, 0.03], "xAxis": [1, 0, 0], "zAxis": [0, 0, 1]}
    b = {"origin": [0.01, 0.02, 0.025], "xAxis": [0, 1, 0], "zAxis": [0, 0, -1]}
    flip, theta, translation, off_axis = correction(a, b)
    assert flip
    # After the flip B's x is still +y; rotating -90 deg about +z aligns it with +x.
    assert theta == pytest.approx(-math.pi / 2)
    assert translation[2] == pytest.approx(0.005) and off_axis == pytest.approx(0.0, abs=1e-12)
