"""Mandatory mechanical scenarios, generated for every robot.

The restrained base in gravity/vibration cases represents a test fixture; it
never holds any other part. Drops/impacts use the free robot and MuJoCo contact.
Random vibration follows MIL-STD-810H Method 514.8 Annex C's tailored wheeled
vehicle approach. Default PSD/amplitude/duration are explicitly estimated small
vehicle tailoring, not a claim of MIL or ISO qualification.
"""

from __future__ import annotations

from itertools import product
import math
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np

from r3_model.spec import Scenario

FACES = {
    "upright": [0, 0, 1],
    "inverted": [0, 0, -1],
    "left": [0, -1, 0],
    "right": [0, 1, 0],
    "front": [-1, 0, 0],
    "rear": [1, 0, 0],
}


def is_mobile(project: Any) -> bool:
    """Base translation is declared, never inferred from arm/joint motion."""
    if project.spec.robot.mobility == "stationary" or project.spec.simulation.base_mount == "fixed":
        return False
    if project.spec.robot.mobility == "mobile" or project.spec.simulation.base_mount == "free":
        return True
    return bool((project.spec.requirements.top_speed or 0) > 0 or (project.spec.requirements.impact_speed or 0) > 0)


def required_scenarios(project: Any) -> list[Scenario]:
    req = project.spec.requirements
    result = []
    for face, normal in FACES.items():
        result.append(
            Scenario(
                id=f"gravity_{face}",
                description=f"Gravity with {face} orientation; root restrained by a test fixture.",
                duration=2,
                initial={"joint_load": {"kind": "gravity", "normal": normal}},
            )
        )
    directions = {
        **FACES,
        **{"corner_" + "".join("p" if x > 0 else "n" for x in v): [-x for x in v] for v in product((-1, 1), repeat=3)},
    }
    for face, normal in directions.items():
        result.append(
            Scenario(
                id=f"drop_{face}",
                description=f"Drop onto {face}, {req.drop_height:g} m clearance.",
                duration=math.sqrt(2 * req.drop_height / project.spec.simulation.environment.gravity) + 2,
                initial={
                    "joint_load": {
                        "kind": "drop",
                        "normal": normal,
                        "height": float(req.drop_height),
                    }
                },
            )
        )
    speed = req.impact_speed if req.impact_speed is not None else req.top_speed
    result.append(
        Scenario(
            id="wall_impact",
            description="Head-on wall impact at requirements.impact_speed (default top_speed).",
            duration=2,
            initial={
                "joint_load": {"kind": "impact" if is_mobile(project) else "impact_skipped", "speed": float(speed or 0)}
            },
        )
    )
    for axis in range(3):
        result.append(
            Scenario(
                id=f"vibration_{'xyz'[axis]}",
                description=f"Sustained road random vibration plus motor harmonic on {'xyz'[axis]}; {req.vibration.source}.",
                duration=req.vibration.duration,
                initial={"joint_load": {"kind": "vibration", "axis": axis}},
            )
        )
    return result


def all_scenarios(project: Any) -> list[Scenario]:
    required = required_scenarios(project)
    reserved = {s.id for s in required}
    # Authored scenarios cannot replace or disable mandatory load cases.
    duplicate = reserved & {s.id for s in project.spec.simulation.scenarios}
    if duplicate:
        raise ValueError("Reserved mandatory scenario IDs: " + ", ".join(sorted(duplicate)))
    return list(project.spec.simulation.scenarios) + required


def orientation(normal: list[float]) -> np.ndarray:
    """Rotation sending the indicated downward-facing robot direction to -world z.

    ``upright`` uses +robot z as the upward axis for readability; the face
    names define the gravity vector in robot coordinates rather than CAD names.
    """
    z = np.asarray(normal, dtype=float)
    z /= np.linalg.norm(z)
    x = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0.0, 1, 0])
    x -= x @ z * z
    x /= np.linalg.norm(x)
    return np.array([x, np.cross(z, x), z])


def configure_model(vm: Any, scenario: Scenario, project: Any) -> None:
    load = scenario.initial.get("joint_load") or {}
    kind = load.get("kind")
    xml = ET.fromstring(vm.xml)
    world = xml.find("worldbody")
    # Reconfiguration is idempotent: a drop releases the default stationary
    # fixture, while gravity/vibration replace it at the case's posed base.
    for body in list(world.findall("body")):
        if body.get("name") == "joint_fixture":
            world.remove(body)
    equality = xml.find("equality")
    if equality is not None:
        for eq in list(equality):
            if eq.get("name") == "joint_fixture_weld":
                equality.remove(eq)
    if kind in ("gravity", "vibration") or (not is_mobile(project) and kind != "drop"):
        base = next(body for body in world.findall("body") if body.get("name") == vm.root)
        ET.SubElement(
            world,
            "body",
            name="joint_fixture",
            mocap="true",
            pos=base.get("pos", "0 0 0"),
            quat=base.get("quat", "1 0 0 0"),
        )
        assumption = ("mechanical", "Base held by an ideal world/test fixture; drop cases release that fixture.")
        if assumption not in vm.assumptions:
            vm.assumptions.append(assumption)
        eq = xml.find("equality")
        if eq is None:
            eq = ET.SubElement(xml, "equality")
        ET.SubElement(
            eq,
            "weld",
            name="joint_fixture_weld",
            body1=vm.root,
            body2="joint_fixture",
            solref="0.002 1",
            solimp="0.99 0.999 0.001",
        )
    if kind == "impact":
        # Wall 0.1 m ahead of the foremost collision extent (refined at reset).
        ET.SubElement(
            world,
            "geom",
            name="joint_impact_wall",
            type="box",
            pos="1 0 2",
            size=".05 100 2",
            contype="0",
            conaffinity="3",
            friction=".2 .005 .0001",
        )
    vm.xml = ET.tostring(xml, encoding="unicode")


class LoadCase:
    def __init__(self, vehicle: Any, scenario: Scenario, project: Any, seed: int) -> None:
        import mujoco

        self.vehicle = vehicle
        self.load = scenario.initial.get("joint_load") or {}
        self.displacement = None
        if not self.load:
            return
        if self.load["kind"] == "impact_skipped":
            from r3_model.diagnostics import Diagnostic

            vehicle.joints.diagnostics.append(
                Diagnostic.warning(
                    "joint_impact_not_applicable",
                    "Wall impact skipped for a stationary base; gravity, drops and vibration still apply.",
                    subjects=[scenario.id],
                    data={"skipped": 1},
                )
            )
            return
        m, d = vehicle.model, vehicle.data
        r = orientation(self.load.get("normal", [0, 0, 1]))
        quat = np.zeros(4)
        mujoco.mju_mat2Quat(quat, r.ravel())
        free = [j for j in range(m.njnt) if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE]
        for j in free:
            q = int(m.jnt_qposadr[j])
            p = d.qpos[q : q + 3].copy()
            old = d.qpos[q + 3 : q + 7].copy()
            d.qpos[q : q + 3] = r @ p
            mujoco.mju_mulQuat(d.qpos[q + 3 : q + 7], quat, old)
        mujoco.mj_forward(m, d)
        rigid = [g for g in range(m.ngeom) if m.geom_contype[g] & 1 and m.geom_bodyid[g] != 0]

        def bound(g: int, axis: int) -> float:
            shape = m.geom_type[g]
            size = m.geom_size[g]
            rot = np.abs(d.geom_xmat[g].reshape(3, 3)[axis])
            if shape == mujoco.mjtGeom.mjGEOM_BOX:
                return float(rot @ size)
            return float(m.geom_rbound[g])

        bottom = min((d.geom_xpos[g, 2] - bound(g, 2) for g in rigid), default=0)
        shift = np.array([0.0, 0.0, float(self.load.get("height", 0.1)) - bottom])
        for j in free:
            q = int(m.jnt_qposadr[j])
            d.qpos[q : q + 3] += shift
            if self.load["kind"] == "impact":
                d.qvel[m.jnt_dofadr[j]] = float(self.load["speed"])
        mujoco.mj_forward(m, d)
        if self.load["kind"] in ("gravity", "vibration"):
            bid = m.body("joint_fixture").id
            self.mocap = int(m.body_mocapid[bid])
            d.mocap_pos[self.mocap] = d.xpos[vehicle.root_body]
            d.mocap_quat[self.mocap] = d.xquat[vehicle.root_body]
            # The fixture weld's reference is reset to the posed robot.
            eq = m.equality("joint_fixture_weld").id
            m.eq_data[eq, 3:10] = [0, 0, 0, 1, 0, 0, 0]
            self.origin = d.mocap_pos[self.mocap].copy()
        if self.load["kind"] == "impact":
            speed = float(self.load["speed"])
            if speed <= 0:
                from r3_model.diagnostics import Diagnostic

                vehicle.joints.diagnostics.append(
                    Diagnostic.error(
                        "impact_speed_missing",
                        "Set requirements.top_speed or requirements.impact_speed to a positive speed.",
                        subjects=["wall_impact"],
                        data={"speed_mps": speed},
                    )
                )
            front = max((d.geom_xpos[g, 0] + bound(g, 0) for g in rigid), default=0)
            m.geom_pos[m.geom("joint_impact_wall").id, 0] = front + 0.15
        if self.load["kind"] == "vibration":
            profile = project.spec.requirements.vibration
            dt = float(m.opt.timestep)
            n = int(math.ceil(float(scenario.duration) / dt)) + 1
            freq = np.fft.rfftfreq(n, dt)
            if max(profile.psd[-1][0], profile.motor_frequency) >= 0.5 / dt:
                raise ValueError("Vibration profile exceeds timestep Nyquist frequency")
            density = np.interp(
                freq,
                np.asarray(profile.psd)[:, 0],
                np.asarray(profile.psd)[:, 1],
                left=0,
                right=0,
            )
            # E[|FFT|²] = PSD*n/(2*dt); random phases preserve specified PSD.
            rng = np.random.default_rng(seed)
            spectrum = np.sqrt(density * n / (2 * dt)) * np.exp(1j * rng.uniform(0, 2 * np.pi, len(freq)))
            omega = 2 * np.pi * freq
            displacement = np.zeros_like(spectrum)
            displacement[1:] = -spectrum[1:] / omega[1:] ** 2
            self.displacement = np.fft.irfft(displacement, n=n)
            t = np.arange(n) * dt
            self.displacement -= (
                profile.motor_acceleration
                / (2 * np.pi * profile.motor_frequency) ** 2
                * np.sin(2 * np.pi * profile.motor_frequency * t)
            )
            self.displacement -= self.displacement[0]
            # Smooth fixture start/stop avoids an artificial velocity impulse.
            ramp = max(float(profile.ramp), dt)
            envelope = np.sin(0.5 * np.pi * np.minimum(t / ramp, 1)) ** 2
            envelope *= np.sin(0.5 * np.pi * np.minimum((t[-1] - t) / ramp, 1)) ** 2
            self.displacement *= envelope
            from r3_model.diagnostics import Diagnostic

            if profile.estimated:
                vehicle.joints.diagnostics.append(
                    Diagnostic.warning(
                        "joint_vibration_estimated",
                        profile.source,
                        subjects=[scenario.id],
                        data={"duration_s": float(scenario.duration)},
                    )
                )
        mujoco.mj_forward(m, d)
        # Reset nominal relative poses after rotating the entire assembly.
        vehicle.reset_assembly_reference()

    def apply(self) -> None:
        if self.displacement is not None:
            d = self.vehicle.data
            i = min(
                int(round(d.time / self.vehicle.model.opt.timestep)),
                len(self.displacement) - 1,
            )
            d.mocap_pos[self.mocap] = self.origin
            d.mocap_pos[self.mocap, int(self.load["axis"])] += self.displacement[i]
