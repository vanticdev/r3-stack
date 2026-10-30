"""Recording contract for routed conductors driven by MuJoCo cable nodes.

Only display geometry is reconstructed here. All displacements come from the
elastic cable plugin; the detailed routed shape adds no independent dynamics.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def cable_metadata(monitor: Any) -> dict[str, Any]:
    """Freeze wire identities, dimensions and authored paths alongside qpos."""
    import mujoco
    from r3_wiring.bodies import ROOT
    from r3_wiring.geometry import project_to_polyline

    root = mujoco.mj_id2name(monitor.model, mujoco.mjtObj.mjOBJ_BODY, monitor.root_body)
    bundles = {}
    for bundle in monitor.plan.bundles:
        nodes = np.vstack([s.vertices if i == 0 else s.vertices[1:] for i, s in enumerate(bundle.spans)])
        core = np.asarray(monitor.plan.conductors[bundle.wires[0]]["core_path"])
        arc = [project_to_polyline(core, q)[1] for q in nodes]
        bundles[bundle.id] = {"spans": [s.prefix.rstrip("_") for s in bundle.spans],
                              "rest_nodes": nodes.tolist(), "node_arc": np.asarray(arc).tolist()}
    wires = {w: {**d, "end_bodies": [root if b == ROOT else b for b in d["end_bodies"]]}
             for w, d in monitor.plan.conductors.items()}
    return {"version": 1, "root_body": root, "bundles": bundles, "wires": wires}


class ConductorPlayback:
    """Deform each display path by interpolated physical node displacements.

    Connector fan-outs use their end body's rigid transform. The bundle core
    retains its routed detail between physical nodes, including conductor offsets.
    """

    def __init__(self, model: Any, data: Any, metadata: dict[str, Any]) -> None:
        import mujoco
        from r3_wiring.geometry import project_to_polyline

        self.metadata = metadata
        self.root = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, metadata["root_body"])
        self.rest_pos = data.xpos.copy()
        self.rest_rot = data.xmat.reshape(-1, 3, 3).copy()
        self.rigid = {}
        self.paths = {}
        for wire, info in metadata["wires"].items():
            path = np.asarray(info["display_path"], dtype=float)
            core = np.asarray(info["core_path"], dtype=float)
            arc = np.asarray([project_to_polyline(core, q)[1] for q in path])
            # display_path can use fewer fan-out samples than routes.json. Locate
            # its two bundle entries geometrically, preserving all pin vertices.
            head = int(np.argmin(np.linalg.norm(path - core[0], axis=1)))
            tail = int(np.argmin(np.linalg.norm(path - core[-1], axis=1)))
            self.paths[wire] = path, arc, head, tail
            self.rigid[wire] = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b) for b in info["end_bodies"]]

    def positions(self, data: Any, span_points: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        root_rotation = data.xmat[self.root].reshape(3, 3)
        root_position = data.xpos[self.root]
        displacement = {}
        for bundle, info in self.metadata["bundles"].items():
            nodes = np.vstack([span_points[s] if i == 0 else span_points[s][1:]
                               for i, s in enumerate(info["spans"])])
            displacement[bundle] = (nodes - root_position) @ root_rotation - np.asarray(info["rest_nodes"])
        result = {}
        for wire, info in self.metadata["wires"].items():
            path, arc, head, tail = self.paths[wire]
            node_arc = self.metadata["bundles"][info["bundle"]]["node_arc"]
            delta = displacement[info["bundle"]]
            local = path + np.column_stack([np.interp(arc, node_arc, delta[:, axis]) for axis in range(3)])
            world = local @ root_rotation.T + root_position
            for region, body in zip((slice(0, head), slice(tail + 1, None)), self.rigid[wire]):
                if body < 0:
                    continue
                # Authored geometry is in the robot frame, not the qpos0 world
                # frame (which may include a ride-height translation).
                nominal = path[region] @ self.rest_rot[self.root].T + self.rest_pos[self.root]
                local_end = (nominal - self.rest_pos[body]) @ self.rest_rot[body]
                world[region] = local_end @ data.xmat[body].reshape(3, 3).T + data.xpos[body]
            result[wire] = world
        return result
