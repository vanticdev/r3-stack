"""Part plans: every part is one body, built only from axis-aligned standard
operations, and the only intended overlaps are threads and press fits."""

import itertools
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_onshape.geometry import Hole, Prism, Revolve, axis_base, operation_manifold, plan_manifold
from r3_onshape.plan import build_plans

RC_CAR = Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car"


@pytest.fixture(scope="module")
def project() -> Project:
    return Project.load(RC_CAR)


@pytest.fixture(scope="module")
def plans(project):
    return build_plans(project)


def test_every_component_and_fastener_is_a_part(project, plans):
    expected = project.spec.part_ids()
    assert set(plans) == expected
    assert all(plans[f.part].kind == "fastener" for f in project.spec.mechanical.fasteners)
    purchased = [p for p in plans.values() if p.kind == "purchased"]
    assert purchased and all(p.mass is not None for p in purchased)
    assert all(p.density > 0 for p in plans.values() if p.kind != "purchased")


def test_every_add_operation_keeps_one_body(plans):
    """Onshape creates a second part when an ADD does not touch the body."""
    for name, plan in plans.items():
        solid = None
        for op in plan.ops:
            piece = operation_manifold(op)
            removing = isinstance(op, Hole) or op.op == "remove"
            solid = piece if solid is None else (solid - piece if removing else solid + piece)
            bodies = [c for c in solid.decompose() if c.volume() > 0]
            assert len(bodies) == 1, f"{name}: {op.name} splits the part"


def test_operations_are_axis_aligned(plans):
    for plan in plans.values():
        for op in plan.ops:
            if isinstance(op, Revolve):
                axis_base(op.axis)
            elif isinstance(op, Hole):
                axis_base(op.axis)
            else:
                assert isinstance(op, Prism)


def test_purchased_parts_get_no_spec_derived_holes(plans):
    for plan in plans.values():
        if plan.kind == "purchased":
            assert not any(op.name.endswith((" clearance", " tapped")) for op in plan.ops), plan.name


def test_only_threads_and_press_fits_overlap(project, plans):
    solids = {n: plan_manifold(p) for n, p in plans.items()}
    allowed = set()
    for f in project.spec.mechanical.fasteners:
        if f.thread_into:
            allowed.add(frozenset((f.part, f.thread_into)))
        if f.nut_part:
            allowed.add(frozenset((f.part, f.nut_part)))
    for i in project.spec.mechanical.interfaces:
        if i.kind in ("press_fit", "spline"):
            allowed.add(frozenset(i.parts))
    boxes = {n: np.asarray(s.bounding_box()).reshape(2, 3) for n, s in solids.items()}
    for a, b in itertools.combinations(sorted(solids), 2):
        if np.any(boxes[a][1] < boxes[b][0]) or np.any(boxes[b][1] < boxes[a][0]):
            continue
        volume = (solids[a] ^ solids[b]).volume()
        if volume > 5e-11:
            assert frozenset((a, b)) in allowed, f"{a} x {b}: {volume * 1e9:.2f} mm^3"


def test_thread_overlap_matches_engagement(project, plans):
    """Plan-level thread volume = pi/4 (d^2 - D^2) x engagement (gb_1: 7 mm into the case)."""
    a, b = plan_manifold(plans["fastener:gb_1"]), plan_manifold(plans["gearbox"])
    volume = (a ^ b).volume()
    per_mm = np.pi / 4 * (3.0 ** 2 - 2.5 ** 2) * 1e-9
    assert volume / per_mm == pytest.approx(7.0, rel=0.01)
