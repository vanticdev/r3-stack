"""An authored anchor needs installable hardware; it cannot pin cable in free air."""

from types import SimpleNamespace

import numpy as np

from r3_wiring.geometry import Box, Cylinder
from r3_wiring.manufacturing import release_status
from r3_wiring.obstacles import Obstacle, ObstacleSet
from r3_wiring.supports import Post, SupportReport, _access, _candidates, _tie_loop, check_requested_supports, load_catalog


def _top_candidates(block_access=False):
    rotation = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    shape = Cylinder(np.zeros(3), rotation, 0.0035, 0.04)
    post = Obstacle("post", "structure", shape, shape, 0.0, False, "base", True)
    obstacles = [post]
    if block_access:
        # A cover clears the post but blocks the strap underneath it.
        box = Box(np.array([0.0, 0.0, -0.0045]), np.eye(3), np.array([0.01, 0.01, 0.0007]))
        obstacles.append(Obstacle("cover", "structure", box, box, 0.0, False, "base", True))
    posts = {"post": [Post("post", np.array([-0.04, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), 0.0035, 0.08)]}
    points = np.array([[-0.03, -0.009, 0.0], [0.03, -0.009, 0.0]])
    types = tuple(t for t in load_catalog()[0] if t.kind == "post_tie")
    return _candidates(points, 0.06, 0.001, [post], ObstacleSet(obstacles, None), types,
                       0.002, 0.01, 0.06, [], posts, [np.array([0.0, 0.0, 0.0045])])


def test_requested_top_tie_is_checked_even_when_side_is_closer():
    requested = [c for c in _top_candidates() if c.get("requested") is not None]
    assert len(requested) == 1
    tie = requested[0]
    np.testing.assert_allclose(tie["position"], [0.0, 0.0, 0.0045], atol=1e-9)
    np.testing.assert_allclose(tie["path"], [0.0, -0.009, 0.0], atol=1e-9)
    assert tie["checks"]["tie_loop"]["ok"]
    assert tie["pull"] > 0.009  # the planner must move the real path onto the checked post


def test_requested_tie_cannot_override_installation_access():
    assert not [c for c in _top_candidates(block_access=True) if c.get("requested") is not None]


def test_cylinder_end_face_cannot_pretend_to_be_a_retained_tie_loop():
    shape = Cylinder(np.zeros(3), np.eye(3), 0.0035, 0.04)
    host = Obstacle("post", "structure", shape, shape, 0.0, False, "base", True)
    post = Post("post", np.zeros(3), np.array([0.0, 0.0, 1.0]), 0.0035, np.inf)
    stype = next(t for t in load_catalog()[0] if t.kind == "post_tie")
    ok, checks = _access(stype, host, np.array([0.0, 0.0, 0.04]), np.array([0.0, 0.0, 1.0]),
                         np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), set(),
                         ObstacleSet([host], None), 0.001, post)
    assert not ok and not checks["tie_loop"]["ok"]


def test_tie_loop_encloses_both_the_post_and_the_wire():
    post = Post("post", np.zeros(3), np.array([1.0, 0.0, 0.0]), 0.0035, 0.08)
    loop = _tie_loop(post, np.array([0.0, 0.0, 0.0035]), np.array([0.0, 0.0, 1.0]), 0.001)
    polygon = loop[:, 1:]
    following = np.roll(polygon, -1, axis=0)
    edge = following - polygon
    orientation = np.sign(np.sum(polygon[:, 0] * following[:, 1] - polygon[:, 1] * following[:, 0]))
    # Each complete circle must lie inside every directed edge of the convex strap loop.
    for centre, radius in (([0, 0], 0.0035), ([0, 0.0045], 0.001)):
        rel = np.asarray(centre) - polygon
        distance = orientation * (edge[:, 0] * rel[:, 1] - edge[:, 1] * rel[:, 0]) / np.linalg.norm(edge, axis=1)
        assert distance.min() >= radius
    assert loop[:, 2].min() < -0.0039


def test_missing_requested_hardware_blocks_manufacturing_release():
    harness = SimpleNamespace(id="lead", route=SimpleNamespace(tie_points=[[0, 0, 0.05]]),
                              wires=[SimpleNamespace(id="wire")])
    project = SimpleNamespace(spec=SimpleNamespace(electrical=SimpleNamespace(harnesses=[harness])))
    report = SupportReport([], [], [], {})
    check_requested_supports(project, report)
    assert [d.code for d in report.diagnostics] == ["cable_support_unplaceable"]
    release = release_status(project, report.diagnostics)
    assert release["status"] == "unreleased"
    assert not release["harnesses"]["lead"]["valid"]
    assert not release["wires"]["wire"]["valid"]
