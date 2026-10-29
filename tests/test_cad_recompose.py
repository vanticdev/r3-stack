"""Offline recomposition helpers: exact same-solid test and analytic faces of locally built parts."""

from __future__ import annotations

import numpy as np

from r3_onshape.geometry import Hole, PartPlan, Plane, Prism, Loop
from r3_onshape.recompose import part_from_plan, plan_sha, same_solid


def _block(hole_d: float = 0.0034, depth: float | None = None) -> PartPlan:
    plane = Plane.through([0, 0, 0], [0, 0, 1])
    body = Prism(plane, [Loop.rectangle([-0.01, -0.01], [0.01, 0.01])], 0.006, 1.0, "new", "block")
    hole = Hole(point=np.array([0.0, 0.0, 0.006]), axis=np.array([0.0, 0.0, -1.0]), diameter=hole_d, depth=depth)
    return PartPlan(name="block", kind="fabricated", material="Aluminum 6061-T6", density=2700.0, ops=[body, hole])


def _recorded(plan: PartPlan) -> dict:
    from r3_onshape.geometry import plan_manifold

    mesh = plan_manifold(plan).to_mesh()
    return {"plan_sha256": "", "vertices": np.asarray(mesh.vert_properties)[:, :3].tolist(),
            "triangles": np.asarray(mesh.tri_verts).tolist()}


def test_same_solid_by_digest_and_by_exact_boolean():
    plan = _block()
    assert same_solid(plan, {"plan_sha256": plan_sha(plan)}) == (True, 0.0)
    same, difference = same_solid(plan, _recorded(_block()))
    assert same and difference < 1e-12
    changed, difference = same_solid(_block(0.0045), _recorded(plan))
    assert not changed and difference > 1e-9


def test_local_part_recovers_planes_cylinders_and_cones():
    part = part_from_plan("block", _block(depth=0.004))
    kinds = {f.kind for f in part.faces}
    assert {"PLANE", "CYLINDER", "CONE"} <= kinds
    bore = [f for f in part.faces_of("CYLINDER")]
    assert len(bore) == 1 and abs(bore[0].radius - 0.0017) < 1e-9 and bore[0].convex is False
    tops = [f for f in part.faces_of("PLANE") if f.direction @ np.array([0, 0, 1.0]) > 0.999]
    assert tops and abs(tops[0].origin[2] - 0.006) < 1e-6
    assert part.manifold().volume() > 0
