"""Native MuJoCo joint-coordinate motion grids checked against pulled STEP solids."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.units import to_si

from .fit import VOLUME_TOL, FitReport, Link, rotor_crossing_links, unit
from .model import GeometryModel

MARGIN = 5e-4  # m, clearance margin moving vs static
SWEEP_SAMPLES = 11
SPIN_ANGLES = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0]














def run_motion(project: Project, model: GeometryModel | None, fit: FitReport, result: StageResult) -> dict[str, Any]:
    """Sample pulled mate coordinates with MuJoCo forward kinematics.

    Uses the same STEP solids, volume tolerance, spin samples and clearance
    margin as the existing checks. No Python linkage solver is involved.
    """
    import itertools
    import xml.etree.ElementTree as ET
    import mujoco
    from r3_model.pulled import PullError
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_model.pulled_view import apply_pulled, require_mass
    from .step_geometry import geometry_from_pulled

    try:
        pulled = load_pulled(project.root)
        assembly = apply_pulled(project, pulled)
        require_mass(pulled)
        model = geometry_from_pulled(pulled)
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        return {"samples": [], "failures": []}
    result.extend(assembly.diagnostics)
    if assembly.errors:
        return {"samples": [], "failures": []}
    if assembly.loops:
        result.add(Diagnostic.error("motion_closed_loop_unverified",
            "Closed-loop mate sweep needs native constrained trajectory sampling; the former authored steering solver is not geometry evidence",
            subjects=[entry["join"] for entry in assembly.loops]))
        return {"samples": [], "failures": []}
    mj = ET.Element("mujoco")
    ET.SubElement(mj, "compiler", angle="radian")
    world = ET.SubElement(mj, "worldbody")
    bodies, origins = {}, {}
    children = {j.child: j for j in assembly.joints}
    def add(name):
        if name in bodies:
            return bodies[name]
        joint = children.get(name)
        parent = add(joint.parent) if joint else world
        origin = np.asarray(joint.origin) if joint else pulled.poses[name][:3, 3]
        offset = origin - (origins[joint.parent] if joint else 0)
        body = ET.SubElement(parent, "body", name=name, pos=" ".join(map(str, offset)))
        bodies[name], origins[name] = body, origin
        part = pulled.parts[name]
        r = pulled.poses[name][:3, :3]
        com = r @ part.com + pulled.poses[name][:3, 3] - origin
        inertia = r @ np.asarray(part.inertia).reshape(3, 3) @ r.T
        ET.SubElement(body, "inertial", mass=str(part.mass), pos=" ".join(map(str, com)),
            fullinertia=" ".join(str(inertia[i,j]) for i,j in [(0,0),(1,1),(2,2),(0,1),(0,2),(1,2)]))
        if joint and joint.type != "fixed":
            ET.SubElement(body, "joint", name=joint.id, type="slide" if joint.type == "prismatic" else "hinge",
                axis=" ".join(map(str, joint.axis)))
        return body
    for name in pulled.parts:
        add(name)
    physics = mujoco.MjModel.from_xml_string(ET.tostring(mj, encoding="unicode"))
    state = mujoco.MjData(physics)
    moving = [j for j in assembly.joints if j.type != "fixed"]
    grids = []
    for joint in moving:
        if joint.type == "continuous":
            grids.append(np.radians(SPIN_ANGLES))
        elif joint.limits is not None:
            grids.append(np.linspace(*joint.limits, SWEEP_SAMPLES))
        else:
            result.add(Diagnostic.error("motion_limits_missing", f"{joint.id}: pulled mate has no travel limits", subjects=[joint.id]))
            return {"samples": [], "failures": []}
    partners = {frozenset((link.a, link.b)) for link in fit.links}
    failures, samples = [], []
    for values in itertools.product(*grids):
        sample = dict(zip([j.id for j in moving], map(float, values)))
        for joint, value in zip(moving, values):
            jid = mujoco.mj_name2id(physics, mujoco.mjtObj.mjOBJ_JOINT, joint.id)
            state.qpos[physics.jnt_qposadr[jid]] = value
        mujoco.mj_forward(physics, state)
        solids = {}
        for name in pulled.parts:
            bid = mujoco.mj_name2id(physics, mujoco.mjtObj.mjOBJ_BODY, name)
            rot = state.xmat[bid].reshape(3, 3)
            delta = np.column_stack([rot, state.xpos[bid] - rot @ origins[name]])
            solids[name] = model[name].manifold().transform(delta)
        for a, b in itertools.combinations(solids, 2):
            if frozenset((a, b)) in partners:
                continue
            volume = (solids[a] ^ solids[b]).volume()
            gap = solids[a].min_gap(solids[b], MARGIN * 4) if volume <= VOLUME_TOL else 0.0
            if volume > VOLUME_TOL or gap < MARGIN:
                finding = {"moving": a, "other": b, "volume_mm3": volume * 1e9, "gap_mm": gap * 1e3, "sample": sample}
                failures.append(finding)
        samples.append(sample)
    if failures:
        result.add(Diagnostic.error("motion_interference", f"Pulled mate sweep found {len(failures)} interference/clearance violations",
            subjects=sorted({p for f in failures for p in (f["moving"], f["other"])}),
            data={"failures": failures, "margin_m": MARGIN, "volume_tolerance": VOLUME_TOL}))
    result.assume("mechanical", "Pulled mate coordinate grid evaluated by MuJoCo; STEP boolean solids retain the fit volume tolerance and motion margin.")
    return {"samples": samples, "failures": failures, "margin_m": MARGIN, "solver": "mujoco"}
