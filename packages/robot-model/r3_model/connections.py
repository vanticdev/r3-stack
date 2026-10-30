"""Load-bearing connections compiled from assembly fasteners and fit interfaces.

Equations: Shigley, Mechanical Engineering Design, ch. 8 (T=KFd,
metric stress area, thread shear and bearing); NASA RP-1228 (1990),
https://ntrs.nasa.gov/citations/19900009424; Lamé elastic interference fit;
Euler-Bernoulli cantilever snap fit. These are capacity checks, not dynamics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from .diagnostics import Diagnostic
from .project import Project
from .units import to_si


@lru_cache(maxsize=128)
def _bearing_mesh(path: str, modified_ns: int) -> Any:
    import trimesh

    return trimesh.load_mesh(path, process=False)


def bearing_thickness(
    project: Project,
    cad_parts: dict[str, Any],
    part: str,
    seat: np.ndarray,
    axis: np.ndarray,
    hole_d: float,
    bearing_d: float,
) -> float | None:
    """Measure local wall thickness around a bearing annulus using CAD rays."""
    file = (cad_parts.get(part) or {}).get("mesh")
    if not file or bearing_d <= hole_d:
        return None
    path = project.root / "build" / "cad" / file
    if not path.exists():
        return None
    try:
        mesh = _bearing_mesh(str(path), path.stat().st_mtime_ns)
        z = axis / np.linalg.norm(axis)
        helper = np.eye(3)[int(np.argmin(np.abs(z)))]
        u = np.cross(z, helper)
        u /= np.linalg.norm(u)
        v = np.cross(z, u)
        radius = (hole_d + bearing_d) / 4
        points = seat + radius * np.asarray([u, -u, v, -v]) - z * 1e-6
        locations, rays, _ = mesh.ray.intersects_location(points, np.tile(z, (4, 1)), multiple_hits=True)
        thickness = []
        for i in range(4):
            distances = np.sort((locations[rays == i] - points[i]) @ z)
            distances = np.unique(np.round(distances[distances >= 0], 9))
            if len(distances) >= 2 and distances[1] - distances[0] > 1e-6:
                thickness.append(float(distances[1] - distances[0]))
        return min(thickness) if thickness else None
    except (ImportError, ValueError, RuntimeError):
        return None


def screw_capacities(
    d: float,
    pitch: float,
    torque: float,
    k: float,
    mu: float,
    proof: float,
    engagement: float,
    female_shear: float,
    bearing_diameter: float,
    thickness: float,
    plate_shear: float,
    interfaces: int = 1,
) -> dict[str, float]:
    """SI. Shigley ch. 8 / NASA RP-1228, conservative separation at full preload.

    Thread stripping uses half the pitch-cylinder surface; pull-through uses
    punching shear around the bearing perimeter. Weakest printed direction is
    selected by the caller, without assuming that infill equals solid stock.
    """
    area = math.pi / 4 * (d - 0.9382 * pitch) ** 2
    preload = torque / (k * d) if k > 0 and d > 0 else 0.0
    proof_load = area * proof
    strip = math.pi * max(0.0, d - 0.64952 * pitch) * engagement * 0.5 * female_shear
    pull = math.pi * bearing_diameter * thickness * plate_shear
    axial = min(preload, proof_load, strip, pull)
    return dict(
        preload=preload,
        slip=preload * mu * interfaces,
        axial=axial,
        shear=area * proof / math.sqrt(3),
        proof_load=proof_load,
        thread_strip=strip,
        pull_through=pull,
    )


def interference_capacity(
    d: float,
    outer_d: float,
    length: float,
    interference: float,
    shaft_e: float,
    hub_e: float,
    shaft_nu: float,
    hub_nu: float,
    mu: float,
) -> tuple[float, float]:
    """Lamé, solid shaft and thick hub, open ends: diametral interference (m).

    delta/d = p [(1-nu_s)/E_s + ((D²+d²)/(D²-d²)+nu_h)/E_h].
    Axial retention = mu p pi d L; torque = retention d/2.
    """
    if min(d, length, interference, shaft_e, hub_e) <= 0 or outer_d <= d:
        return 0.0, 0.0
    compliance = (1 - shaft_nu) / shaft_e + ((outer_d**2 + d**2) / (outer_d**2 - d**2) + hub_nu) / hub_e
    pressure = interference / (d * compliance)
    return pressure, mu * pressure * math.pi * d * length


def snap_capacity(
    e: float,
    width: float,
    thickness: float,
    length: float,
    deflection: float,
    strength: float,
) -> float:
    """Cantilever end load min(3EI delta/L³, sigma b t²/(6L))."""
    if min(e, width, thickness, length, deflection, strength) <= 0:
        return 0.0
    return min(
        e * width * thickness**3 * deflection / (4 * length**3),
        strength * width * thickness**2 / (6 * length),
    )


@dataclass
class Connection:
    id: str
    a: str
    b: str
    kind: str
    axis: list[float]
    point: list[float]
    axial: float
    shear: float
    torque: float
    bending: float
    slip: float = 0.0
    preload: float = 0.0
    locked: bool = False
    tolerance: float = 0.0002
    loosening_distance: float = 0.0
    cause: str = ""
    coefficients: dict[str, dict[str, Any]] = field(default_factory=dict)
    details: dict[str, float] = field(default_factory=dict)
    group: str | None = None
    capacity_known: bool = True
    missing_data: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        axis = np.asarray(self.axis, dtype=float)
        norm = float(np.linalg.norm(axis))
        if norm == 0:
            raise ValueError(f"connection {self.id}: zero axis")
        self.axis = (axis / norm).tolist()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if not self.capacity_known:
            # Internal zeros disable an unqualified constraint; they are not
            # measured zero strengths. Preserve positive, independently known
            # bounds in details, while serializing unknown results as null.
            for name in ("axial", "shear", "torque", "bending", "slip", "preload"):
                result[name] = None
            result["details"] = {k: v if v != 0 else None for k, v in self.details.items()}
        return result


@dataclass
class ConnectionModel:
    connections: list[Connection] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    estimated: dict[str, dict[str, Any]] = field(default_factory=dict)


def compile_connections(project: Project) -> ConnectionModel:
    """No robot-specific rules. Missing data is a blocking unknown-capacity error.

    ``interface.params.physics`` accepts sourced SI values (value/status/source).
    Catalog defaults are estimates and are listed individually in diagnostics.
    A mount declaration establishes a nominal pose, never an attachment.
    """
    model = ConnectionModel()
    spec, cat = project.spec, project.catalog
    defaults = cat.joint_defaults
    cad = project.read_build_json("cad/cad_model.json") or {}
    fit = cad.get("fit", {})
    cad_parts = cad.get("parts", {})
    assembly = project.assembly

    def material(cid: str) -> Any:
        if cid not in spec.components or spec.components[cid].type not in cat.components:
            return None
        mech = cat.component(spec.components[cid].type).mechanical
        override = spec.fabrication.parts.get(cid, {}).get("material")
        if override:
            return cat.material(override)
        found = cat.material(mech.material or "")
        if found is not None:
            return found
        if mech.fabricated and (not mech.material or str(mech.material).lower() in {"plastic", "printed", "fdm"}):
            return cat.material(spec.fabrication.material or "")
        return None

    def dims(cid: str) -> list[float]:
        if cid not in spec.components or spec.components[cid].type not in cat.components:
            return []
        return [float(v) for v in cat.component(spec.components[cid].type).mechanical.size]

    def value(
        inputs: dict[str, Any],
        key: str,
        used: dict[str, Any],
        fallback: str | None = None,
    ) -> float:
        raw = inputs[key] if key in inputs else defaults.get(fallback or key)
        if raw is None:
            used[key] = dict(value=None, status="missing", source="Missing joint input; capacity unqualified")
            return 0.0
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        try:
            if (not isinstance(raw, dict) or not raw.get("source")
                    or raw.get("status") not in ("estimated", "standard", "datasheet", "measured")):
                raise ValueError("requires value, status and source")
            number = float(to_si(raw["value"]))
            if not math.isfinite(number) or number < 0:
                raise ValueError("must be finite and nonnegative")
        except (ValueError, TypeError, KeyError) as error:
            model.diagnostics.append(Diagnostic.error(
                "joint_coefficient_invalid", f"{key}: {error}", subjects=[key]))
            used[key] = dict(value=None, status="missing", source="Rejected invalid coefficient; capacity unqualified")
            return 0.0
        used[key] = {**raw, "value": number}
        return number

    def estimate(used: dict[str, Any], key: str, number: float, source: str) -> float:
        used[key] = dict(value=number, status="estimated", source=source)
        return number

    def add(c: Connection) -> None:
        if c.a == c.b:
            return
        model.connections.append(c)
        for k, v in c.coefficients.items():
            if v["status"] == "estimated":
                model.estimated[f"{c.id}.{k}"] = v
        if not c.capacity_known:
            model.diagnostics.append(Diagnostic.error(
                "joint_capacity_unknown",
                f"{c.id}: capacity unknown; missing data: {', '.join(c.missing_data)}",
                subjects=[c.id, c.a, c.b],
                data={"missing_data": c.missing_data, "capacity_known": False},
            ))
        elif c.kind != "resting" and (c.axial <= 0 or c.shear <= 0):
            model.diagnostics.append(
                Diagnostic.error(
                    "joint_capacity_zero",
                    f"{c.id}: holds {c.axial:g} N axial, {c.shear:g} N shear: {c.cause or 'missing capacity input'}",
                    subjects=[c.id, c.a, c.b],
                    data={"axial_n": c.axial, "shear_n": c.shear},
                )
            )

    for f in spec.mechanical.fasteners:
        ft = cat.fastener(f.type)
        used: dict[str, Any] = {}
        inp = ft.joint_physics
        k, mu, proof = (value(inp, n, used) for n in ("nut_factor", "friction", "proof_stress"))
        torque = float(f.torque) if f.torque is not None else value(inp, "torque", used)
        if f.torque is not None:
            used["torque"] = dict(value=torque, status="estimated", source="Authored assembly torque target; assembly process not qualified")
        used["fastener_proof_stress"] = used.pop("proof_stress")
        threaded = bool(f.thread_into or f.nut)
        female = material(f.thread_into or "")
        # A purchased nut has a proof-qualified female thread. Unknown host
        # material cannot silently inherit steel strength.
        female_shear = (
            (value(cat.fastener(f.nut).joint_physics, "proof_stress", used) if f.nut else proof) / math.sqrt(3)
            if f.nut or ft.kind == "nut"
            else (value(female.joint_physics, "shear_strength", used) if female else 0.0)
        )
        if "proof_stress" in used:
            used["nut_proof_stress"] = used.pop("proof_stress")
        stack = list(f.clamps)
        if f.thread_into and ft.kind != "nut":
            stack.append(f.thread_into)
        if "shear_strength" in used:
            used["female_shear_strength"] = used.pop("shear_strength")
        plate = material(stack[0]) if stack else female
        plate_shear = value(plate.joint_physics, "shear_strength", used) if plate else 0.0
        if "shear_strength" in used:
            used["plate_shear_strength"] = used.pop("shear_strength")
        thickness = min(dims(stack[0])) if stack else ft.d
        thickness = estimate(
            used,
            "bearing_thickness",
            thickness,
            "Catalog minimum part dimension; replace with CAD local bearing thickness",
        )
        measured = (fit.get("fasteners") or {}).get(f.id, {})
        engagement = measured.get("engagement_mm", 0) * 0.001
        if measured.get("engagement_mm") is None:
            grip = float(f.grip or sum(min(dims(c)) for c in f.clamps if dims(c)))
            engagement = max(
                0.0,
                min(
                    float(f.length or ft.thread_length or ft.height or 0) - grip,
                    min(dims(f.thread_into)) if dims(f.thread_into or "") else float(ft.height or ft.d),
                ),
            )
            engagement = estimate(
                used,
                "engagement",
                engagement,
                "Catalog envelope and screw length estimate; no CAD thread measurement",
            )
        if f.nut:
            engagement = min(engagement, float(cat.fastener(f.nut).height or 0))
        bearing_d = float(ft.head_diameter or ft.collar_diameter or ft.across_flats or ft.d)
        measured_thickness = bearing_thickness(
            project,
            cad_parts,
            stack[0] if stack else f.thread_into or "",
            np.asarray(f.position, float),
            np.asarray(f.axis, float),
            float(ft.d),
            bearing_d,
        )
        if measured_thickness is not None:
            thickness = measured_thickness
            used["bearing_thickness"] = dict(
                value=thickness, status="measured", source="CAD mesh bearing-annulus ray thickness"
            )
        cap = screw_capacities(
            ft.d,
            ft.pitch,
            torque if threaded else 0,
            k,
            mu,
            proof,
            engagement,
            female_shear,
            bearing_d,
            thickness,
            plate_shear,
            max(1, len(stack) - 1),
        )
        if f.nut and stack:
            nt = cat.fastener(f.nut)
            last = stack[-1]
            last_material = material(last)
            nut_shear = value(last_material.joint_physics, "shear_strength", used) if last_material else 0.0
            if "shear_strength" in used:
                used["nut_plate_shear_strength"] = used.pop("shear_strength")
            nut_d = float(nt.across_flats or nt.d)
            nut_t = bearing_thickness(
                project,
                cad_parts,
                last,
                np.asarray(f.position) + np.asarray(f.axis) * float(f.grip or 0),
                -np.asarray(f.axis),
                float(ft.d),
                nut_d,
            )
            if nut_t is None:
                nut_t = estimate(
                    used,
                    "nut_bearing_thickness",
                    min(dims(last)),
                    "Catalog minimum nut-side plate dimension; local CAD thickness unavailable",
                )
            else:
                used["nut_bearing_thickness"] = dict(
                    value=nut_t, status="measured", source="CAD mesh nut-bearing annulus ray thickness"
                )
            cap["nut_pull_through"] = math.pi * nut_d * nut_t * nut_shear
            cap["pull_through"] = min(cap["pull_through"], cap["nut_pull_through"])
            cap["axial"] = min(cap["axial"], cap["nut_pull_through"])
        overload = (
            f.preloaded
            and all(cap[k] > 0 for k in ("proof_load", "thread_strip", "pull_through"))
            and cap["preload"] > min(cap[k] for k in ("proof_load", "thread_strip", "pull_through"))
        )
        if overload:
            capacity = min(cap[k] for k in ("proof_load", "thread_strip", "pull_through"))
            model.diagnostics.append(
                Diagnostic.error(
                    "joint_assembly_overload",
                    f"{f.id}: assembly preload needs {cap['preload']:.6g} N, holds {capacity:.6g} N (proof/thread/pull-through)",
                    subjects=[f.id],
                    data={"preload_n": cap["preload"], "capacity_n": capacity},
                )
            )
            cap["requested_preload"] = cap["preload"]
            cap.update(preload=0.0, slip=0.0, axial=0.0)
        if measured.get("ok") is False:
            threaded = False
        if not threaded:
            cap.update(preload=0.0, slip=0.0, axial=0.0)
        # Retained pivots have no clamp preload but do have proof/strip retention.
        if not f.preloaded and threaded:
            cap.update(preload=0.0, slip=0.0, axial=min(cap["proof_load"], cap["thread_strip"], cap["pull_through"]))
        locking_features = {"nyloc", "threadlocker", "lock_washer", "locking_cap"}
        locked = (
            f.locking in locking_features
            or ft.locking in locking_features
            or (bool(f.nut) and cat.fastener(f.nut).locking in locking_features)
        )
        distance = value(inp, "loosening_distance", used)
        tolerance = value({}, "fit_tolerance", used)
        pairs = [(f.part, c) for c in stack]
        if ft.kind == "nut" and f.thread_into:
            pairs.append((f.part, f.thread_into))
        if f.nut_part:
            pairs.append((f.part, f.nut_part))
        cause = (
            "assembly preload exceeds proof/thread/pull-through capacity"
            if overload
            else "CAD fit rejected fastener geometry"
            if measured.get("ok") is False
            else "no nut or tapped hole"
            if not threaded
            else f"no female-thread strength for {f.thread_into}"
            if female_shear <= 0
            else f"no bearing/pull-through strength for {stack[0] if stack else f.thread_into}"
            if plate_shear <= 0
            else "no engaged female thread"
            if engagement <= 0
            else "preload / proof / female thread / pull-through limit"
        )
        missing = [name for name, coefficient in used.items() if coefficient["status"] == "missing"]
        if threaded:
            if female_shear <= 0 and not (f.nut or ft.kind == "nut"):
                missing.append(f"female-thread material strength: {f.thread_into}")
            if plate_shear <= 0 and plate is None:
                missing.append(f"bearing/pull-through material strength: {stack[0] if stack else f.thread_into}")
            if f.nut and stack and material(stack[-1]) is None:
                missing.append(f"nut-side pull-through material strength: {stack[-1]}")
        # A demonstrably absent retainer or rejected/empty engagement is a
        # construction failure even when unrelated catalog inputs are missing.
        if not threaded or engagement <= 0:
            missing = []
        for n, (a, b) in enumerate(dict.fromkeys(pairs)):
            # The outer race of an internally rotating bearing is not rigid to
            # the fastener/shaft on its inner race.
            if any(
                i.kind == "revolute_fit"
                and i.params.get("internal_rotation")
                and b == i.parts[1]
                and i.parts[0] in stack + [f.thread_into]
                for i in spec.mechanical.interfaces
            ):
                continue
            add(
                Connection(
                    f.id if len(pairs) == 1 else f"{f.id}:{n + 1}",
                    a,
                    b,
                    "screw",
                    list(f.axis),
                    list(f.position),
                    cap["axial"],
                    cap["shear"],
                    (cap["slip"] if f.preloaded else cap["shear"]) * bearing_d / 2,
                    cap["axial"] * bearing_d / 2,
                    cap["slip"],
                    cap["preload"],
                    locked,
                    tolerance,
                    distance,
                    cause,
                    dict(used),
                    cap,
                    group=f.id,
                    capacity_known=not missing,
                    missing_data=list(dict.fromkeys(missing)),
                )
            )

    for i in spec.mechanical.interfaces:
        if i.kind == "magnetic_target":
            continue  # sensing geometry, not a mechanical attachment
        used = {}
        inp = i.params.get("physics", {})
        axis = np.asarray(i.axis_dir or [0, 0, 1], float)
        axis /= np.linalg.norm(axis)
        a, b = i.parts
        point = list(i.axis_point or project.graph.component_pose(a)[0])
        mu = value(inp, "friction", used)
        tolerance = value(inp, "fit_tolerance", used)
        clear = i.params.get("clearance")
        if clear:
            tolerance = max(abs(float(to_si(v))) for v in clear)
        sa, sb = dims(a), dims(b)
        resolved = assembly.valid.get(i.join or i.id) if assembly else None
        shaft_d = None
        if resolved:
            shaft = next(
                (side for side in resolved if side.kind in ("shaft", "spline", "ball_stud")),
                None,
            )
            if shaft:
                shaft_d = shaft.quantity("d") or shaft.quantity("diameter")

        # Contact footprint is conservative when no qualified CAD patch exists.
        measured_fit = (fit.get("interfaces") or {}).get(i.id, {})
        area = value(inp, "area", used)
        if not area and measured_fit.get("contact_mm2") is not None:
            area = float(measured_fit["contact_mm2"]) * 1e-6
            used["area"] = dict(value=area, status="measured", source="CAD fit measured contact area")
        if not area and sa and sb and "contact_mm2" not in measured_fit:
            area = estimate(
                used,
                "area",
                min(math.prod(sorted(sa)[-2:]), math.prod(sorted(sb)[-2:])),
                "Smaller mating envelope footprint; replace with measured adhesive/contact area",
            )
        radius = value(inp, "radius", used)
        if not radius and measured_fit.get("shaft_d_mm"):
            radius = float(measured_fit["shaft_d_mm"]) * 0.0005
            used["radius"] = dict(
                value=radius,
                status="measured",
                source="CAD fit measured shaft diameter",
            )
        if not radius and shaft_d:
            radius = shaft_d / 2
            used["radius"] = dict(
                value=radius,
                status="datasheet",
                source="Compiled catalog shaft interface nominal diameter",
            )
        if not radius:
            radius = estimate(
                used,
                "radius",
                min(sorted(sa)[-2:] + sorted(sb)[-2:]) / 2 if sa and sb else 0,
                "Minimum in-plane catalog envelope half-dimension; replace with interface radius",
            )
        axial = shear = torsion = bending = 0.0
        details = {}
        missing = []
        required = []
        if i.kind == "resting":
            pass
        elif i.kind == "adhesive":
            required = ["area", "shear_strength", "peel_strength", "radius"]
            shear = value(inp, "shear_strength", used, "adhesive_shear") * area
            axial = value(inp, "peel_strength", used, "adhesive_peel") * area
            torsion, bending = shear * radius, axial * radius
        elif i.kind == "strap":
            required = ["rated_tension", "radius"]
            axial = shear = value(inp, "rated_tension", used, "strap_tension")
            torsion = bending = axial * radius
        elif i.kind == "connector_retention":
            required = ["retention_force", "unmating_force", "shear_force", "radius"]
            axial = min(
                value(inp, "retention_force", used, "connector_retention"),
                value(inp, "unmating_force", used, "connector_retention"),
            )
            shear = value(inp, "shear_force", used, "connector_retention")
            torsion, bending = shear * radius, axial * radius
        elif i.kind == "ball_joint":
            required = ["retention_force"]
            axial = shear = value(inp, "retention_force", used, "ball_retention")
            torsion = bending = 0.0  # ball connection carries no moment
        elif i.kind == "snap_fit":
            required = ["youngs_modulus", "width", "thickness", "length", "deflection", "strength", "radius"]
            axial = snap_capacity(
                *(
                    value(inp, name, used)
                    for name in (
                        "youngs_modulus",
                        "width",
                        "thickness",
                        "length",
                        "deflection",
                        "strength",
                    )
                )
            )
            shear = axial
            torsion = bending = axial * radius
        elif i.kind == "press_fit":
            required = ["friction", "radius", "outer_diameter"]
            ma, mb = material(a), material(b)
            interference = i.params.get("interference", [0, 0])
            delta = min(to_si(v) for v in interference) if isinstance(interference, list) else to_si(interference)
            length = to_si(i.params.get("min_engagement", 0))
            if measured_fit.get("engagement_mm") is not None:
                length = float(measured_fit["engagement_mm"]) * 0.001
            if measured_fit.get("bore_d_mm") is not None:
                delta = 2 * radius - float(measured_fit["bore_d_mm"]) * 0.001
            d = 2 * radius
            outer = value(inp, "outer_diameter", used)
            if not outer:
                outer = estimate(
                    used,
                    "outer_diameter",
                    max(sb) if sb else 0,
                    "Catalog hub envelope estimate; qualify local hub wall",
                )
            if ma and mb:
                for prefix, m in (("shaft", ma), ("hub", mb)):
                    estimate(
                        used,
                        prefix + "_modulus",
                        float(m.youngs_modulus),
                        m.provenance.source,
                    )
                    estimate(
                        used,
                        prefix + "_poisson",
                        float(m.poisson_ratio),
                        m.provenance.source,
                    )
                pressure, axial = interference_capacity(
                    d,
                    outer,
                    length,
                    delta,
                    ma.youngs_modulus,
                    mb.youngs_modulus,
                    ma.poisson_ratio,
                    mb.poisson_ratio,
                    mu,
                )
                # Elastic Lamé pressure must also stay below material crushing
                # strength; otherwise an elastic fit has no defensible capacity.
                hoop_factor = (outer**2 + d**2) / (outer**2 - d**2) if outer > d else math.inf
                if pressure * hoop_factor > min(ma.z_strength, mb.z_strength):
                    model.diagnostics.append(
                        Diagnostic.error(
                            "joint_fit_yield",
                            f"{i.id}: interference pressure exceeds weakest material strength",
                            subjects=[i.id, a, b],
                            data={
                                "pressure_pa": pressure,
                                "hoop_stress_pa": pressure * hoop_factor,
                                "strength_pa": min(ma.z_strength, mb.z_strength),
                            },
                        )
                    )
                details["pressure_pa"] = pressure
                shear = min(ma.z_strength, mb.z_strength) / math.sqrt(3) * d * length
                torsion, bending = axial * radius, shear * length / 2
            else:
                missing.append("shaft/hub material elastic and strength data")
        elif i.kind in {"revolute_fit", "drive_flat", "spline"}:
            # Radial bearing/flat load from projected bearing area and weakest
            # material; axial retention comes ONLY from a real retainer.
            ma, mb = material(a), material(b)
            if ma and mb:
                strength = min(ma.z_strength, mb.z_strength)
                estimate(
                    used,
                    "bearing_strength",
                    strength,
                    "Weakest material TDS XY/Z ultimate; bearing approximation",
                )
                shear = strength * 2 * radius * to_si(i.params.get("min_engagement", 0))
            elif "shear_force" not in inp:
                missing.append("bearing material strength")
            retainers = set(i.params.get("retainers", []))
            retained = [c for c in model.connections if retainers & {c.a, c.b}]
            axial = min((c.axial for c in retained), default=0.0)
            # A clamp stack on this shaft is a valid retention path.
            if not retainers:
                retained = [c for c in model.connections if c.kind == "screw" and {a, b} & {c.a, c.b}]
                axial = min(
                    (c.axial for c in retained),
                    default=0.0,
                )
            if "retention_force" in inp:
                axial = value(inp, "retention_force", used)
                required.append("retention_force")
            elif not retained:
                missing.append("axial retention rating or qualified retainer path")
            elif any(not c.capacity_known for c in retained):
                missing.append("retainer path capacity: " + ", ".join(c.id for c in retained if not c.capacity_known))
            if "shear_force" in inp:
                shear = value(inp, "shear_force", used)
                required.append("shear_force")
            torsion = 0.0 if i.kind == "revolute_fit" else shear * radius
            bending = shear * radius
        elif i.kind != "resting":
            missing.append(f"capacity model for interface kind {i.kind}")
        missing.extend(name for name in required if used.get(name, {}).get("status") == "missing")
        add(
            Connection(
                i.id,
                a,
                b,
                i.kind,
                axis.tolist(),
                point,
                axial,
                shear,
                torsion,
                bending,
                tolerance=tolerance,
                cause="no qualified retention/material data"
                if min(axial, shear) <= 0
                else "catalog/interface capacity",
                coefficients=used,
                details=details,
                capacity_known=not missing,
                missing_data=list(dict.fromkeys(missing)),
            )
        )
        # A strap only holds its load when it is also anchored through a support.
        if i.kind == "strap":
            for support in i.params.get("through", []):
                add(
                    Connection(
                        f"{i.id}:anchor:{support}",
                        a,
                        support,
                        "strap",
                        axis.tolist(),
                        point,
                        axial,
                        shear,
                        torsion,
                        bending,
                        tolerance=tolerance,
                        coefficients=dict(used),
                        cause="strap support tension",
                    )
                )
    supported = {part for c in model.connections if c.kind != "resting" for part in (c.a, c.b)}
    moving = {j.child for j in spec.mechanical.joints if j.type != "fixed"}
    roots = [cid for cid, comp in spec.components.items() if comp.mount == "world" and cid not in moving]
    root = next(
        (cid for cid in roots if spec.components[cid].type in cat.components and cat.component(spec.components[cid].type).kind == "structure"),
        roots[0] if roots else None,
    )
    for cid, comp in spec.components.items():
        if cid == root or cid in supported or cid in moving:
            continue
        parent = comp.mount if comp.mount != "world" else root
        if parent:
            used = {}
            tolerance = value({}, "fit_tolerance", used)
            add(
                Connection(
                    f"resting:{cid}",
                    parent,
                    cid,
                    "resting",
                    [0, 0, 1],
                    project.graph.component_pose(cid)[0].tolist(),
                    0,
                    0,
                    0,
                    0,
                    tolerance=tolerance,
                    coefficients=used,
                    cause="gravity and contacts only",
                )
            )
    if model.estimated:
        model.diagnostics.append(
            Diagnostic.warning(
                "joint_inputs_estimated",
                "Estimated joint inputs: " + ", ".join(sorted(model.estimated)),
                subjects=sorted(model.estimated),
                data={"estimated_count": len(model.estimated)},
            )
        )
    return model
