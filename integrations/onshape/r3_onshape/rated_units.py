"""Verify factory-internal motion without interpreting simplified vendor internals.

Only explicitly declared members of the same purchased asset qualify. Every
external interface remains in the ordinary geometry/retention checks.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from r3_connectors.frames import Frame
from r3_catalog.schema import Placement
from r3_model.diagnostics import Diagnostic

from .purchased import bank_entry

if TYPE_CHECKING:
    from .fit import FitChecker


def verify_rated_internal(checker: FitChecker, interface: Any, info: dict[str, Any]) -> bool | None:
    """None means ordinary geometric fit; False is a failed rated verification."""
    request = interface.rated_internal
    if request is None:
        return None
    from .fit import ANGLE_TOL, OFFSET_TOL, _key

    parts = interface.parts
    components = checker.spec.components
    owner = request.unit
    if len(set(parts)) != 2 or owner not in parts or any(name not in components for name in parts):
        checker.err('rated_joint_scope_invalid', f'{interface.id}: rated unit must own both supplied members', parts)
        return None
    member = next(name for name in parts if name != owner)
    a, b = (checker.catalog.component(components[name].type) for name in (owner, member))
    if a.mechanical.fabricated or b.mechanical.fabricated or components[member].purchased_unit != owner:
        checker.err('rated_joint_scope_invalid', f'{interface.id}: this is not internal to one purchased unit', parts)
        return None
    rating = a.mechanical.rated_internal_joints.get(request.joint)
    if rating is None:
        checker.err('rated_joint_rating_missing', f'{interface.id}: {owner} has no declared rating {request.joint}', parts)
        return False
    joins = checker.spec.assembly.joins if checker.spec.assembly else []
    join = next((j for j in joins if j.join_id == interface.join), None)
    expected = {f'{owner}.{rating.interface}', f'{member}.{rating.member_interface}'}
    if (b.id != rating.member_type or join is None or {join.a, join.b} != expected
            or (rating.motion == 'rotational' and join.kind != 'revolute')
            or (rating.motion == 'translational' and join.kind != 'slider')):
        checker.err('rated_joint_scope_invalid', f'{interface.id}: interfaces do not identify the rated factory joint', parts)
        return None
    entries = [bank_entry(c) for c in (a, b)]
    sources = [next((s for s in e.sources if s.direct and not s.requires_login and s.sha256 and s.solid_indices), None)
               if e else None for e in entries]
    s0, s1 = sources
    if (s0 is None or s1 is None or s0.sha256 != s1.sha256 or s0.url != s1.url or s0.member != s1.member
            or set(s0.solid_indices) & set(s1.solid_indices)):
        checker.err('rated_joint_source_mismatch', f'{interface.id}: members need distinct solids of the same pinned vendor assembly', parts)
        return False
    # Both whole supplied solids must still occupy the manufacturer's relative
    # pose. A shifted external part cannot become "internal" through a label.
    raw_poses = []
    for name, entry in zip((owner, member), entries):
        if not entry.placement or entry.validation.status != 'passed':
            checker.err('rated_joint_source_unverified', f'{interface.id}: supplied member placement is unverified', parts)
            return False
        frame = Frame.from_placement(Placement.model_validate(entry.placement))
        local = np.eye(4)
        local[:3, :3], local[:3, 3] = frame.rotation, frame.origin
        raw_poses.append(checker.project.assembly.poses[name] @ local)
    delta = np.linalg.inv(raw_poses[0]) @ raw_poses[1]
    distance = float(np.linalg.norm(delta[:3, 3]))
    angle = math.acos(float(np.clip((np.trace(delta[:3, :3]) - 1) / 2, -1, 1)))
    if distance > OFFSET_TOL or angle > ANGLE_TOL:
        checker.err('rated_joint_source_pose_mismatch', f'{interface.id}: supplied members moved from their factory-relative pose',
                    parts, distance=distance, angle=angle)
        return False
    needed = {'torque', 'angular_speed'} if rating.motion == 'rotational' else {'force', 'linear_speed'}
    if (not rating.source.strip() or not needed <= rating.ratings.keys()
            or any(not math.isfinite(v) or v <= 0 for v in rating.ratings.values())):
        checker.err('rated_joint_rating_missing', f'{interface.id}: manufacturer source and positive {sorted(needed)} ratings are required', parts)
        return False
    if (not request.source.strip() or rating.ratings.keys() != request.demand.keys()
            or any(not math.isfinite(v) or v < 0 for v in request.demand.values())):
        checker.err('rated_joint_demand_missing', f'{interface.id}: sourced design demand is required for every rated quantity', parts)
        return False
    exceeded = {key: {'demand': request.demand[key], 'rating': value} for key, value in rating.ratings.items()
                if request.demand[key] > value}
    if exceeded:
        checker.err('rated_joint_overload', f'{interface.id}: design demand exceeds the manufacturer rating', parts, exceeded=exceeded)
        return False
    volume = checker.intersection(*parts)
    point, axis = checker._axis(interface)
    checker.report.allowed[_key(*parts)] = f'rated internal joint {interface.id}'
    checker.report.interface_axes[interface.id] = {'point': point.tolist(), 'axis': axis.tolist(), 'source': rating.source}
    evidence = {'unit': owner, 'joint': request.joint, 'overlap_volume_m3': volume,
                'ratings': dict(rating.ratings), 'rating_source': rating.source,
                'demand': dict(request.demand), 'demand_source': request.source,
                'model_sha256': s0.sha256, 'verification': 'manufacturer_rated_internal_joint'}
    info.update(evidence)
    checker.result.add(Diagnostic.info('rated_internal_joint_verified',
        f'{interface.id}: purchased unit {owner}, vendor-model overlap {volume * 1e9:.3f} mm³; '
        f'demand {request.demand} within ratings {rating.ratings}; source {rating.source}', subjects=parts, data=evidence))
    return True
