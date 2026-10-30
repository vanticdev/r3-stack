"""Lightweight declared mechanism roles shared by physics and load estimation."""
from __future__ import annotations

from r3_model.project import Project
from r3_model.spec import Joint


def wheel_joints(project: Project) -> dict[str, Joint]:
    """Wheels may be rigidly mounted to the component owning a spin joint.

    Stop at the first moving body, as the native physics builder does. A
    steering joint alone does not make a freely spinning wheel.
    """
    project.assembly  # compile authored joins before inspecting their joints
    by_child = {joint.child: joint for joint in project.spec.mechanical.joints}
    result = {}
    for cid, component in project.spec.components.items():
        if project.catalog.component(component.type).kind != "wheel":
            continue
        current, seen = cid, set()
        while current in project.spec.components and current not in seen:
            seen.add(current)
            if current in by_child and by_child[current].type != "fixed":
                if by_child[current].type == "continuous":
                    result[cid] = by_child[current]
                break
            current = project.spec.components[current].mount
    return result
