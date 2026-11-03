"""MuJoCo rigid surface contacts for unchanged, open native tessellations."""
from __future__ import annotations

from typing import Any
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def emit_surface_file(body: ET.Element, name: str, path: Path, origin: np.ndarray,
                      friction: str, contype: str = "1") -> None:
    """Load unchanged world-frame STL triangles on the existing massive body.

    A rigid flex preserves cavities without adding faces, mass, collision
    thickness or degrees of freedom. The position only changes coordinates
    from the native world frame to this body frame.
    """
    flex = ET.SubElement(body, "flexcomp", name=f"joint_surface:{name}", type="mesh",
                         file=str(path), rigid="true", radius="0", rgba="0 0 0 0",
                         pos=" ".join(format(float(v), '.17g') for v in -origin))
    ET.SubElement(flex, "contact", contype=contype, conaffinity="1", condim="3",
                  friction=friction, solref="0.002 1", selfcollide="none")


def _collision_targets(model: Any) -> dict[int, list[int]]:
    # Keep the body target even if it currently has only visual geoms: cable
    # geoms and fixture masks can also belong to that same native body.
    targets = {b: [b] for b in range(model.nbody)}
    for f in range(model.nflex):
        start, count = int(model.flex_vertadr[f]), int(model.flex_vertnum[f])
        if model.flex_rigid[f] and count:
            owners = model.flex_vertbodyid[start:start + count]
            if np.all(owners == owners[0]):
                targets[int(owners[0])].append(model.nbody + f)
    return targets


def collision_signatures(model: Any, a: int, b: int,
                         targets: dict[int, list[int]] | None = None) -> list[int]:
    """Expand a body exclusion to its geom and rigid-flex collision targets.

    MuJoCo's broadphase indexes flexes after bodies. Its public model exclusion
    array uses the packed bodyflex pair signature; see engine_collision_driver.c.
    A native part has at most one rigid surface, plus any attached cable geoms.
    """
    targets = _collision_targets(model) if targets is None else targets
    return sorted({(min(x, y) << 16) + max(x, y)
                   for x in targets[a] for y in targets[b] if x != y})


class ContactExclusions:
    """Retained body contacts, initialized before the first collision solve."""

    def __init__(self, model: Any, bindings: list[dict[str, Any]]) -> None:
        self.model = model
        targets = _collision_targets(model)
        self.rigid_edges = [(model.body(a).id, model.body(b).id, model.equality(eq).id)
                            for a, b, eq in (bindings[0].get('rigid_edges', []) if bindings else [])]
        self.groups = []
        for group in bindings[0].get('contact_groups', []) if bindings else []:
            slots = group.get('slots', [group['name']])
            signatures = [int(model.exclude_signature[model.exclude(group['name']).id])]
            if group.get('body_pair'):
                a, b = (model.body(n).id for n in group['body_pair'])
                signatures = collision_signatures(model, a, b, targets)
            if len(signatures) > len(slots):
                raise ValueError('Native surface collision exclusion exceeds reserved targets')
            signatures += [0] * (len(slots) - len(signatures))
            self.groups.append((signatures,
                [[model.equality(n).id for n in path] for path in group['paths']],
                tuple(model.body(n).id for n in group['rigid_pair']) if group.get('rigid_pair') else None))
        self.other = [int(model.exclude_signature[i]) for i in range(model.nexclude)
                      if not model.exclude(i).name.startswith('joint_exclude:')]

    def refresh(self, active: Any) -> None:
        if not self.groups:
            return
        from .joints import _rigid_components
        rigid = _rigid_components([(a, b, eq) for a, b, eq in self.rigid_edges if active[eq]])
        signatures = self.other + [signature if (
            any(all(active[e] for e in path) for path in paths)
            or (pair and pair[0] in rigid and rigid[pair[0]] == rigid.get(pair[1]))) else 0
            for slots, paths, pair in self.groups for signature in slots]
        self.model.exclude_signature[:] = sorted(signatures)


def surface_supports(model: Any, data: Any, axis: int, *, maximum: bool = False) -> list[float]:
    """Exact vertex support coordinates of native rigid surfaces."""
    result = []
    for f in range(model.nflex):
        if not model.flex_rigid[f] or not model.flex_contype[f] & 1:
            continue
        start, count = int(model.flex_vertadr[f]), int(model.flex_vertnum[f])
        values = data.flexvert_xpos[start:start + count, axis]
        if len(values):
            result.append(float(values.max() + model.flex_radius[f]) if maximum
                          else float(values.min() - model.flex_radius[f]))
    return result


def surface_plane_distances(model: Any, data: Any, plane: int) -> list[float]:
    """Signed triangle-surface support distance to a native MuJoCo plane."""
    normal = data.geom_xmat[plane].reshape(3, 3)[:, 2]
    origin = data.geom_xpos[plane]
    result = []
    for f in range(model.nflex):
        if not model.flex_rigid[f] or not model.flex_contype[f] & 1:
            continue
        start, count = int(model.flex_vertadr[f]), int(model.flex_vertnum[f])
        vertices = data.flexvert_xpos[start:start + count]
        if len(vertices):
            result.append(float(((vertices - origin) @ normal).min() - model.flex_radius[f]))
    return result
