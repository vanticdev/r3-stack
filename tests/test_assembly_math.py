"""Exact math of the assembly compiler: frames, flip, clocking, hole-pattern
matching, loop residuals and moving-joint poses (synthetic objects)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from r3_catalog.schema import Catalog, ComponentType
from r3_model.assembly import (
    FLIP, Hole, candidate_hole_clocks, compile_assembly, frame_matrix, invert, iso_286_limits, match_holes,
    mate_matrix, pose_matrix, press_interference_max, rotation_about_axis, rotation_angle, rpy_from_matrix,
    running_clearance_max, b_in_a_plane,
)
from r3_model.spec import RobotSpec

MM = 1e-3


def _ctype(type_id: str, interfaces: dict, size=(0.02, 0.02, 0.004)) -> dict:
    return {"id": type_id, "kind": "structure", "name": type_id,
            "mechanical": {"shape": "box", "size": list(size), "mass": 0.01}, "interfaces": interfaces}


def _catalog(*types: dict) -> Catalog:
    return Catalog(components={t["id"]: ComponentType.model_validate(t) for t in types})


def _spec(components: dict, joins: list, root: str = "base", **extra) -> RobotSpec:
    return RobotSpec.model_validate({"robot": {"name": "t"}, "components": components,
                                     "assembly": {"root": root, "joins": joins}, **extra})


def codes(result) -> list[str]:
    return [d.code for d in result.diagnostics]


# --------------------------------------------------------------------------- frames


def test_frame_matrix_is_right_handed_and_orthonormal():
    frame = frame_matrix([1, 2, 3], [1, 0, 0], [0, 0, 1])
    rotation = frame[:3, :3]
    assert np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-15)
    assert np.isclose(np.linalg.det(rotation), 1.0)
    assert np.allclose(rotation[:, 1], [0, -1, 0])  # y = z × x
    assert np.allclose(invert(frame) @ frame, np.eye(4), atol=1e-15)


def test_mate_flips_z_aligns_x_then_clocks():
    for clock in (0.0, 0.3, math.pi / 2, math.pi, -2.0):
        mate = mate_matrix(clock, 0.0)
        assert np.allclose(mate[:3, 2], [0, 0, -1], atol=1e-15)  # b's z opposes a's z
        assert np.allclose(mate[:3, 0], [math.cos(clock), math.sin(clock), 0], atol=1e-15)  # x aligned, then clocked
        assert np.isclose(np.linalg.det(mate[:3, :3]), 1.0)
    assert np.array_equal(mate_matrix(0.0, 0.0), FLIP)
    # Offset moves b along a's +z (a gap), independent of clock.
    assert np.allclose(mate_matrix(1.1, 0.004)[:3, 3], [0, 0, 0.004])
    # A point of b's plane lands at Rz(clock)·(x, −y).
    point = mate_matrix(0.7, 0.0) @ np.array([0.01, 0.02, 0.0, 1.0])
    assert np.allclose(point[:2], b_in_a_plane(0.01, 0.02, 0.7), atol=1e-17)


def test_rpy_round_trip_and_gimbal_lock():
    rng = np.random.default_rng(3)
    for _ in range(200):
        rpy = rng.uniform([-math.pi, -math.pi / 2 + 1e-3, -math.pi], [math.pi, math.pi / 2 - 1e-3, math.pi])
        pose = pose_matrix([0, 0, 0], rpy)
        assert np.allclose(pose_matrix([0, 0, 0], rpy_from_matrix(pose)), pose, atol=1e-12)
    locked = pose_matrix([0, 0, 0], [0.4, math.pi / 2, 0.9])
    assert np.allclose(pose_matrix([0, 0, 0], rpy_from_matrix(locked)), locked, atol=1e-12)


def test_rotation_angle_precise_near_zero_and_pi():
    for angle in (1e-9, 1e-4, 0.5, 2.0, math.pi - 1e-7, math.pi):
        rotation = rotation_about_axis([1, 2, 3], [0, 0, 0], angle)
        assert rotation_angle(rotation) == pytest.approx(angle, rel=1e-6, abs=1e-15)


def _plate_and_block(block_face: dict, base_seat: dict) -> tuple[Catalog, dict]:
    catalog = _catalog(_ctype("base-t", {"seat": base_seat}, size=(0.1, 0.1, 0.004)), _ctype("block-t", {"face": block_face}))
    return catalog, {"base": {"type": "base-t", "pose": {"xyz": [0.1, -0.2, 0.3], "rpy": [0.1, 0.2, 0.3]}}, "block": {"type": "block-t"}}


def test_child_pose_composition_matches_formula():
    seat = {"kind": "face_mount", "origin": [0.01, 0.02, 0.004], "z": [0, 0, 1], "x": [1, 0, 0]}
    face = {"kind": "face_mount", "origin": [0.003, 0.0, 0.0], "z": [0, -1, 0], "x": [1, 0, 0]}
    catalog, components = _plate_and_block(face, seat)
    clock, offset = math.radians(30), 0.0015
    result = compile_assembly(_spec(components, [{"a": "base.seat", "b": "block.face", "clock": clock, "offset": offset}]), catalog)
    assert not result.errors
    base = pose_matrix([0.1, -0.2, 0.3], [0.1, 0.2, 0.3])
    expected = base @ frame_matrix(seat["origin"], seat["z"], seat["x"]) @ mate_matrix(clock, offset) \
        @ invert(frame_matrix(face["origin"], face["z"], face["x"]))
    assert np.allclose(result.poses["block"], expected, atol=1e-15)
    # The two interface frames are coincident up to flip/clock/offset in the world.
    a_world, b_world = result.interface_world("base.seat"), result.interface_world("block.face")
    assert np.allclose(b_world[:3, 2], -a_world[:3, 2], atol=1e-15)
    assert np.allclose(b_world[:3, 3] - a_world[:3, 3], a_world[:3, 2] * offset, atol=1e-15)
    assert math.isclose(math.atan2(float(b_world[:3, 0] @ a_world[:3, 1]), float(b_world[:3, 0] @ a_world[:3, 0])), clock)


def test_reversed_join_gives_the_same_pose():
    seat = {"kind": "face_mount", "origin": [0.01, 0.02, 0.004]}
    face = {"kind": "face_mount", "origin": [0.0, 0.0, 0.0], "z": [0, 0, -1]}
    catalog, components = _plate_and_block(face, seat)
    forward = compile_assembly(_spec(components, [{"a": "base.seat", "b": "block.face", "clock": 0.4, "offset": 0.001}]), catalog)
    # b -> a: M = Tz(o)·Rz(c)·Rx(π) is a half-turn (its own inverse), so swapping sides keeps the same clock.
    assert np.allclose(mate_matrix(0.4, 0.001) @ mate_matrix(0.4, 0.001), np.eye(4), atol=1e-15)
    backward = compile_assembly(_spec(components, [{"a": "block.face", "b": "base.seat", "clock": 0.4, "offset": 0.001}]), catalog)
    assert backward.tree[0].reversed
    assert np.allclose(forward.poses["block"], backward.poses["block"], atol=1e-15)


# --------------------------------------------------------------------------- ISO tables


def test_iso_286_fit_numbers():
    shaft = iso_286_limits(0.006, "g6", hole=False)
    bore = iso_286_limits(0.006, "H7", hole=True)
    assert shaft == pytest.approx((0.006 - 12e-6, 0.006 - 4e-6))
    assert bore == pytest.approx((0.006, 0.006 + 12e-6))
    assert bore[0] - shaft[1] == pytest.approx(4e-6) and bore[1] - shaft[0] == pytest.approx(24e-6)
    assert running_clearance_max(0.006) == pytest.approx(220e-6)  # H11/c11, 3-6 mm row (6 is "up to and including")
    assert running_clearance_max(0.0061) == pytest.approx(260e-6)  # 6-10 mm row
    assert press_interference_max(0.006) == pytest.approx(27e-6)  # H7/s6
    with pytest.raises(ValueError):
        iso_286_limits(0.006, "zz9", hole=False)


# --------------------------------------------------------------------------- hole patterns


def _b_pattern_for(a_points: list[tuple[float, float]], clock: float) -> list[tuple[float, float]]:
    """b-frame hole positions that land on ``a_points`` at ``clock`` (inverse of Rz(clock)·(x, −y))."""
    c, s = math.cos(-clock), math.sin(-clock)
    return [(c * x - s * y, -(s * x + c * y)) for x, y in a_points]


def _holes(points, d, kind="clearance", depth=0.003) -> list[Hole]:
    return [Hole(x, y, d, kind, depth) for x, y in points]


def test_asymmetric_pattern_has_a_unique_clock():
    a_points = [(0.010, 0.0), (-0.006, 0.004), (0.002, -0.009)]
    b_points = _b_pattern_for(a_points, math.radians(37))
    holes_a, holes_b = _holes(a_points, 3.2e-3), _holes(b_points, 3e-3, "tapped")
    clocks = [c for c in candidate_hole_clocks(holes_a, holes_b, 1e-4) if match_holes(holes_a, holes_b, c, 0.003, None).ok]
    assert len(clocks) == 1 and math.degrees(clocks[0]) == pytest.approx(37.0)
    match = match_holes(holes_a, holes_b, clocks[0], 0.003, None)
    assert match.max_offset < 1e-15 and len(match.pairs) == 3


@pytest.mark.parametrize("points, expected_deg", [
    ([(0.02, 0.02), (-0.02, 0.02), (-0.02, -0.02), (0.02, -0.02)], [0, 90, 180, 270]),  # square: 4-fold
    ([(0.029, 0.0245), (-0.029, 0.0245), (-0.029, -0.0245), (0.029, -0.0245)], [0, 180]),  # Pi 58 x 49: 2-fold
    ([(0.0125, 0.0), (-0.0125, 0.0)], [0, 180]),  # 540 motor face
    ([(0.01 * math.cos(k * 2 * math.pi / 3), 0.01 * math.sin(k * 2 * math.pi / 3)) for k in range(3)], [0, 120, 240]),
])
def test_symmetric_patterns_match_at_every_symmetry_clock(points, expected_deg):
    holes_a = _holes(points, 3.2e-3)
    holes_b = _holes(_b_pattern_for(points, 0.0), 3e-3, "tapped")
    good = [c for c in candidate_hole_clocks(holes_a, holes_b, 1e-4) if match_holes(holes_a, holes_b, c, 0.003, None).ok]
    assert [round(math.degrees(c), 6) for c in good] == pytest.approx(expected_deg)


def test_hole_allowance_is_floating_fastener_rule():
    # M3 through 3.2 + 3.4 clearance: (0.2 + 0.4)/2 = 0.3 mm; tapped side contributes 0.
    a = Hole(0, 0, 3.2e-3, "clearance", 0.003)
    assert match_holes([a], [Hole(0, 0.29e-3, 3.4e-3, "clearance", 0.003)], 0.0, 0.003, None).ok
    assert not match_holes([a], [Hole(0, 0.31e-3, 3.4e-3, "clearance", 0.003)], 0.0, 0.003, None).ok
    tapped = match_holes([a], [Hole(0, 0.101e-3, 3e-3, "tapped", 0.006)], 0.0, 0.003, None)
    assert not tapped.ok and tapped.allowances == [pytest.approx(0.1e-3)]


def test_ambiguous_clock_is_reported_and_explicit_clock_resolves_it():
    holes = [[0.0125, 0, "3.2 mm", "clearance", "4 mm"], [-0.0125, 0, "3.2 mm", "clearance", "4 mm"]]
    tapped = [[0.0125, 0, "3 mm", "tapped", "6 mm"], [-0.0125, 0, "3 mm", "tapped", "6 mm"]]
    catalog, components = _plate_and_block({"kind": "hole_pattern", "z": [0, 0, -1], "params": {"holes": tapped}},
                                           {"kind": "hole_pattern", "origin": [0, 0, 0.004], "params": {"holes": holes}})
    ambiguous = compile_assembly(_spec(components, [{"a": "base.seat", "b": "block.face"}]), catalog)
    diag = next(d for d in ambiguous.diagnostics if d.code == "clock_ambiguous")
    assert diag.data["candidates_deg"] == pytest.approx([0.0, 180.0])
    explicit = compile_assembly(_spec(components, [{"a": "base.seat", "b": "block.face", "clock": "180 deg"}]), catalog)
    assert not explicit.errors and explicit.joins["base.seat~block.face"]["clock_deg"] == pytest.approx(180.0)


# --------------------------------------------------------------------------- loops and moving joints


def _bar_catalog(post_length: float, tilt_deg: float = 0.0) -> Catalog:
    one = lambda d, kind, depth: {"holes": [[0, 0, d, kind, depth]]}  # noqa: E731
    tilt = math.radians(tilt_deg)
    base = _ctype("base-t", {"p1": {"kind": "hole_pattern", "origin": [-0.03, 0, 0.004], "params": one(3.2e-3, "clearance", 0.004)},
                             "p2": {"kind": "hole_pattern", "origin": [0.03, 0, 0.004], "params": one(3.2e-3, "clearance", 0.004)}},
                  size=(0.1, 0.03, 0.004))
    post = lambda pid, length, z: _ctype(pid, {  # noqa: E731
        "bottom": {"kind": "hole_pattern", "z": [0, 0, -1], "params": one(3e-3, "tapped", 0.006)},
        "top": {"kind": "hole_pattern", "origin": [0, 0, length], "z": z, "params": one(3e-3, "tapped", 0.006)}})
    bar = _ctype("bar-t", {"h1": {"kind": "hole_pattern", "origin": [-0.03, 0, 0], "z": [0, 0, -1], "params": one(3.2e-3, "clearance", 0.003)},
                           "h2": {"kind": "hole_pattern", "origin": [0.03, 0, 0], "z": [0, 0, -1], "params": one(3.2e-3, "clearance", 0.003)}},
                 size=(0.07, 0.01, 0.003))
    return _catalog(base, bar, post("post-a", 0.02, [0, 0, 1]), post("post-b", post_length, [0, math.sin(tilt), math.cos(tilt)]))


def _bar_spec() -> RobotSpec:
    return _spec({"base": {"type": "base-t"}, "pa": {"type": "post-a"}, "pb": {"type": "post-b"}, "bar": {"type": "bar-t"}},
                 [{"a": "base.p1", "b": "pa.bottom"}, {"a": "base.p2", "b": "pb.bottom"},
                  {"a": "pa.top", "b": "bar.h1"}, {"a": "pb.top", "b": "bar.h2"}])


def test_loop_closes_exactly_with_equal_posts():
    result = compile_assembly(_bar_spec(), _bar_catalog(0.02))
    assert not result.errors
    (loop,) = result.loops
    assert loop["join"] == "pb.top~bar.h2" and loop["position_error"] < 1e-15 and loop["mobility"] == -6


def test_loop_residual_reports_length_error_exactly():
    result = compile_assembly(_bar_spec(), _bar_catalog(0.0193))
    diag = next(d for d in result.diagnostics if d.code == "assembly_misfit")
    assert diag.data["position_error"] == pytest.approx(0.7e-3, abs=1e-12)
    assert diag.data["error_vector"] == pytest.approx([0, 0, -0.7e-3], abs=1e-12)
    assert diag.data["angle_error"] == pytest.approx(0.0, abs=1e-12)


def test_loop_residual_reports_tilt_exactly():
    result = compile_assembly(_bar_spec(), _bar_catalog(0.02, tilt_deg=0.25))
    diag = next(d for d in result.diagnostics if d.code == "assembly_misfit")
    assert diag.data["angle_error_deg"] == pytest.approx(0.25, rel=1e-9)
    assert diag.data["position_error"] == pytest.approx(0.0, abs=1e-15)
    # Within the 0.1 deg default tolerance a tilt passes.
    assert not compile_assembly(_bar_spec(), _bar_catalog(0.02, tilt_deg=0.09)).errors


def _moving_catalog() -> Catalog:
    base = _ctype("base-t", {
        "pin": {"kind": "shaft", "origin": [0.01, 0.0, 0.004], "z": [0, 1, 1], "x": [1, 0, 0], "params": {"d": "6 mm", "tol": "g6"}},
        "rail": {"kind": "shaft", "origin": [-0.01, 0, 0.004], "z": [1, 0, 0], "x": [0, 0, 1], "params": {"d": "8 mm", "tol": "h6"}},
        "ball": {"kind": "ball_stud", "origin": [0, 0.01, 0.004], "params": {"d": "4.8 mm"}}})
    arm = _ctype("arm-t", {"hub": {"kind": "bore", "origin": [0.02, 0.0, 0.0], "z": [0, 0, -1], "params": {"d": "6 mm", "tol": "H7"}}})
    carriage = _ctype("carriage-type", {"bush": {"kind": "bore", "origin": [0, 0, 0], "z": [-1, 0, 0], "x": [0, 0, 1], "params": {"d": "8 mm", "tol": "H7"}}})
    link = _ctype("link-t", {"cup": {"kind": "ball_socket", "origin": [0.0, 0.0, 0.005], "z": [0, 0, 1], "params": {"d": "4.9 mm"}}})
    return _catalog(base, arm, carriage, link)


def _moving_spec(**extra) -> RobotSpec:
    return _spec({"base": {"type": "base-t", "pose": {"xyz": [0.5, 0.2, 0.1], "rpy": [0, 0, 0.3]}},
                  "arm": {"type": "arm-t"}, "carriage": {"type": "carriage-type"}, "link": {"type": "link-t"}},
                 [{"a": "base.pin", "b": "arm.hub", "kind": "revolute", "joint": "elbow", "limits": [-1, 1]},
                  {"a": "base.rail", "b": "carriage.bush", "kind": "slider", "joint": "slide"},
                  {"a": "base.ball", "b": "link.cup", "kind": "ball", "id": "ball"}], **extra)


def test_moving_joints_at_nonzero_q():
    result = compile_assembly(_moving_spec(), _moving_catalog())
    assert not result.errors, [d.message for d in result.errors]
    elbow = next(j for j in result.joints if j.id == "elbow")
    slide = next(j for j in result.joints if j.id == "slide")
    assert elbow.type == "revolute" and slide.type == "prismatic"
    axis = np.asarray(elbow.axis)
    assert np.linalg.norm(axis) == pytest.approx(1.0)
    assert np.allclose(axis, result.interface_world("base.pin")[:3, 2])

    q_rev, q_slide, q_ball = 0.83, 0.0125, np.array([0.1, -0.4, 0.25])
    moved = result.poses_at({"elbow": q_rev, "slide": q_slide, "ball": q_ball})
    # Revolute: a rigid rotation by q about the derived world axis through the derived origin.
    assert np.allclose(moved["arm"], rotation_about_axis(elbow.axis, elbow.origin, q_rev) @ result.poses["arm"], atol=1e-15)
    # Slider: a translation by q along the derived axis, no rotation.
    shift = np.eye(4)
    shift[:3, 3] = np.asarray(slide.axis) * q_slide
    assert np.allclose(moved["carriage"], shift @ result.poses["carriage"], atol=1e-15)
    # Ball: the socket center stays on the stud center; orientation changes by the rotation vector.
    center = result.interface_world("base.ball")[:3, 3]
    assert np.allclose(result.interface_world("link.cup", moved)[:3, 3], center, atol=1e-15)
    assert rotation_angle(result.poses["link"][:3, :3].T @ moved["link"][:3, :3]) == pytest.approx(np.linalg.norm(q_ball))
    # The base never moves.
    assert np.array_equal(moved["base"], result.poses["base"])


def test_explicit_joint_must_agree():
    elbow = next(j for j in compile_assembly(_moving_spec(), _moving_catalog()).joints if j.id == "elbow")
    # Any point on the axis line is a valid origin.
    shifted = (np.asarray(elbow.origin) + 0.037 * np.asarray(elbow.axis)).tolist()
    authored = {"id": "elbow", "type": "revolute", "parent": "base", "child": "arm", "axis": list(elbow.axis), "origin": shifted}
    agree = compile_assembly(_moving_spec(mechanical={"joints": [authored]}), _moving_catalog())
    assert "joint_conflict" not in codes(agree)
    tilted = compile_assembly(_moving_spec(mechanical={"joints": [{**authored, "axis": [1, 0, 0]}]}), _moving_catalog())
    diag = next(d for d in tilted.diagnostics if d.code == "joint_conflict")
    assert diag.data["axis"]["angle"] == pytest.approx(math.acos(float(np.asarray(elbow.axis) @ [1, 0, 0])))
    moved = compile_assembly(_moving_spec(mechanical={"joints": [{**authored, "origin": [0, 0, 0]}]}), _moving_catalog())
    assert next(d for d in moved.diagnostics if d.code == "joint_conflict").data["origin"]["axis_distance"] > 0.01


def test_flat_drive_and_ball_checks():
    base = _ctype("base-t", {"d_shaft": {"kind": "shaft", "origin": [0, 0, 0.004], "params": {"d": "5 mm", "flat_offset": "2.0 mm", "tol": "h6"}}})
    gear = _ctype("gear-t", {"d_bore": {"kind": "bore", "z": [0, 0, -1], "params": {"d": "5 mm", "flat_offset": "1.9 mm", "tol": "H7"}}})
    catalog = _catalog(base, gear)
    spec = _spec({"base": {"type": "base-t"}, "gear": {"type": "gear-t"}},
                 [{"a": "base.d_shaft", "b": "gear.d_bore", "kind": "drive_flat", "clock": "10 deg"}])
    result = compile_assembly(spec, catalog)
    flat = next(d for d in result.diagnostics if d.code == "fit_out_of_range")
    assert flat.data["interference"] == pytest.approx(0.1e-3)
    misaligned = next(d for d in result.diagnostics if d.code == "drive_flat_invalid")
    assert math.degrees(misaligned.data["misalignment"]) == pytest.approx(10.0)
