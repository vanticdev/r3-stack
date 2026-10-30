"""Local part plans (phase B): every component and plain fastener is one Onshape
part modeled at its own origin, built only from axis-aligned standard operations;
fabricated parts take their holes from their interfaces, purchased parts keep
only their catalog features; at the compiled poses the only overlaps are threads,
press fits and splines."""

import itertools
from pathlib import Path

import numpy as np
import pytest

from r3_model.project import Project
from r3_onshape.geometry import Hole, Prism, Revolve, axis_base, operation_manifold, plan_manifold
from r3_onshape.parts import build_part_catalog, instance_poses, world_manifold

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mechanism"


@pytest.fixture(scope="module")
def project() -> Project:
    return Project.load(BENCH)


@pytest.fixture(scope="module")
def catalog(project):
    return build_part_catalog(project)


def test_every_component_and_fastener_is_an_instance(project, catalog):
    parts, instances = catalog
    plain = [f for f in project.spec.mechanical.fasteners if not f.body]
    assert set(instances) == set(project.spec.components) | {f.part for f in plain} | {f.nut_part for f in plain if f.nut}
    assert all(parts[instances[f.part]].plan.kind == "fastener" for f in plain)
    purchased_parts, _ = build_part_catalog(Project.load(Path(__file__).parent / "fixtures/bench"))
    purchased = [p for p in purchased_parts.values() if p.plan.kind == "purchased"]
    assert purchased and all(p.plan.mass is not None and p.library for p in purchased if p.type != "test-controller")
    assert all(p.plan.density > 0 for p in parts.values() if p.plan.kind != "purchased")


def test_every_add_operation_keeps_one_body(catalog):
    """Onshape creates a second part when an ADD does not touch the body."""
    parts, _ = catalog
    for key, part in parts.items():
        if part.bank_model:
            # The builder imports these purchased parts; it never executes their
            # offline proxy ADD operations. Real PCB assemblies can contain gaps.
            continue
        solid = None
        for op in part.plan.ops:
            if isinstance(op, Hole) or getattr(op, "op", "add") == "remove":
                continue
            piece = operation_manifold(op)
            solid = piece if solid is None else solid + piece
            assert len(solid.decompose()) == 1, f"{key}: {op.name} makes a separate body"


def test_operations_are_axis_aligned(catalog):
    parts, _ = catalog
    for part in parts.values():
        for op in part.plan.ops:
            if isinstance(op, Prism):
                assert op.plane.base in ("TOP", "FRONT", "RIGHT")
            else:
                axis_base(op.axis)  # raises if not parallel to a part axis


def test_purchased_parts_get_no_interface_holes(project, catalog):
    parts, _ = catalog
    for part in parts.values():
        if part.plan.kind == "purchased":
            # Only explicitly documented holes in a missing-model proxy may
            # follow a shared pattern. An imported purchased body is untouched.
            allowed = set(project.catalog.component(part.type).mechanical.proxy_holes_from) if not part.bank_model else set()
            interface_ops = [op for op in part.plan.ops if ":" in (op.name or "")]
            assert all(isinstance(op, Hole) and op.name.partition(":")[0] in allowed
                       for op in interface_ops), part.name


def test_fabricated_holes_come_from_interfaces(catalog):
    parts, _ = catalog
    base = next(p for p in parts.values() if p.name == "Coupon base")
    holes = [op for op in base.plan.ops if isinstance(op, Hole)]
    assert len(holes) == 2
    assert all(op.style == "clearance" and op.diameter == pytest.approx(.0034) for op in holes)
    slots = [op for op in base.plan.ops if isinstance(op, Prism) and "slot" in op.name]
    assert len(slots) == 1


def test_thread_overlap_matches_engagement(project, catalog):
    parts, instances = catalog
    poses = instance_poses(project)
    fastener = project.spec.mechanical.fasteners[0]
    screw = world_manifold(parts[instances[fastener.part]], poses[fastener.part])
    support = world_manifold(parts[instances["support"]], poses["support"])
    expected = np.pi / 4 * (.003 ** 2 - .0025 ** 2) * (fastener.length - .004)
    assert (screw ^ support).volume() == pytest.approx(expected, rel=.03)
