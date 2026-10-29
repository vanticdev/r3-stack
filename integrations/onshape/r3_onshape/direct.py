"""Direct CAD: build the robot in an Onshape Part Studio with standard features.

``build_robot(api, doc, project, result)`` turns every :class:`PartPlan` into
sketches on default/offset planes, extrudes, revolves and Hole features, the
way a person models in the Onshape UI, then names each part and assigns its
material (fabricated: real density, so mass comes from geometry) or a labeled
catalog mass override (purchased). No FeatureScript is authored or evaluated;
:mod:`r3_onshape.policy` rejects it at the transport.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from dataclasses import asdict, is_dataclass
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from . import features as fx
from .client import OnshapeError, Transport
from .geometry import Hole, PartPlan, Plane, Prism, Revolve, unit
from .plan import build_plans

GENERATOR_VERSION = "direct-cad-1.0"
METADATA = {"name": "57f3fb8efa3416c06701d60d", "description": "57f3fb8efa3416c06701d60e", "appearance": "57f3fb8efa3416c06701d60c",
            "material": "57f3fb8efa3416c06701d615", "mass": "57f3fb8efa3416c06701d626", "part_number": "57f3fb8efa3416c06701d60f"}


class BuildError(RuntimeError):
    def __init__(self, part: str, op: str, status: str, detail: str = "") -> None:
        super().__init__(f"{part}: feature {op!r} regenerated with {status} {detail}".strip())
        self.part, self.op, self.status = part, op, status


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, np.ndarray):
        return [round(float(v), 9) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 9)
    return value


def input_digest(project: Project) -> str:
    """Cache key: the complete generated plans (every feature), the generator and
    validator versions, the mechanical spec, and every catalog entry used."""
    from .fit import VALIDATOR_VERSION

    plans = build_plans(project)
    spec = project.spec
    used_types = sorted({c.type for c in spec.components.values()})
    used_fasteners = sorted({f.type for f in spec.mechanical.fasteners} | {f.nut for f in spec.mechanical.fasteners if f.nut})
    payload = {
        "generator": GENERATOR_VERSION,
        "validator": VALIDATOR_VERSION,
        "plans": {name: _jsonable(plan) for name, plan in sorted(plans.items())},
        "components": {cid: c.model_dump(mode="json") for cid, c in sorted(spec.components.items())},
        "mechanical": spec.mechanical.model_dump(mode="json"),
        "software_steering": {k: spec.software.config.get(k) for k in ("steering_pulse_us", "max_steer_rad")},
        "catalog": {t: project.catalog.component(t).model_dump(mode="json") for t in used_types},
        "fasteners": {t: project.catalog.fastener(t).model_dump(mode="json") for t in used_fasteners},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _hex_rgb(color: str) -> dict[str, int]:
    return {"red": int(color[1:3], 16), "green": int(color[3:5], 16), "blue": int(color[5:7], 16)}


class StudioBuilder:
    """Creates features in one Part Studio and tracks part ids."""

    def __init__(self, api: Transport, did: str, wid: str, eid: str) -> None:
        self.api = api
        self.did, self.wid, self.eid = did, wid, eid
        self.base = f"/partstudios/d/{did}/w/{wid}/e/{eid}"
        self._planes: dict[tuple[str, float], dict[str, Any]] = {}
        self.part_ids: dict[str, str] = {}
        self.known_ids: set[str] = set()
        self.warnings: list[tuple[str, str]] = []
        self.calls = 0

    def add(self, feature: dict[str, Any], part: str = "") -> str:
        response = self.api.json("POST", self.base + "/features", body={"btType": "BTFeatureDefinitionCall-1406", "feature": feature})
        self.calls += 1
        status = (response.get("featureState") or {}).get("featureStatus", "OK")
        fid = response["feature"]["featureId"]
        if status == "ERROR":
            raise BuildError(part, feature.get("name", ""), status, f"(feature {fid})")
        if status == "WARNING":
            self.warnings.append((part, feature.get("name", "")))
        return fid

    def plane_query(self, plane: Plane) -> dict[str, Any]:
        if abs(plane.offset) < 1e-12:
            return fx.default_plane_query(plane)
        key = (plane.base, round(plane.offset, 9))
        if key not in self._planes:
            label = {"TOP": "z", "FRONT": "y", "RIGHT": "x"}[plane.base]
            value = plane.offset * (-1 if plane.base == "FRONT" else 1)
            fid = self.add(fx.cplane(f"Plane {label}={value * 1000:g} mm", plane))
            self._planes[key] = fx.created_by(fid, "FACE", "SHEET")
        return self._planes[key]

    def _scope(self, part: str) -> list[dict[str, Any]]:
        return [fx.det(self.part_ids[part])]

    def _list_parts(self) -> dict[str, str]:
        parts = self.api.json("GET", f"/parts/d/{self.did}/w/{self.wid}/e/{self.eid}")
        self.calls += 1
        return {p["partId"]: p.get("name", "") for p in parts}

    # ---------------------------------------------------------------- operations
    def _prism(self, plan: PartPlan, op: Prism, new: bool) -> None:
        sketch_id = self.add(fx.sketch(f"{plan.name}: {op.name} sketch", self.plane_query(op.plane), op.loops), plan.name)
        mode = "new" if new else op.op
        self.add(fx.extrude(f"{plan.name}: {op.name}", sketch_id, op.depth, op.direction < 0, mode,
                            None if new else self._scope(plan.name)), plan.name)

    def _revolve(self, plan: PartPlan, op: Revolve, new: bool) -> None:
        axis = unit(op.axis)
        radial = op.radial_dir()
        radial = unit(radial - (radial @ axis) * axis)
        plane = Plane.containing(op.point, axis, radial)
        matrix = np.column_stack([plane.to2d(radial), plane.to2d(axis)])
        loop = op.loop_rt().transformed(matrix, plane.to2d(op.point))
        query = self.plane_query(plane)
        sketch_id = self.add(fx.sketch(f"{plan.name}: {op.name} profile", query, [loop]), plan.name)
        start, end = plane.to2d(op.point), plane.to2d(np.asarray(op.point) + axis * 0.01)
        from .geometry import Loop, Segment

        axis_loop = Loop(segments=(Segment("line", tuple(start), tuple(end)),))
        axis_id = self.add(fx.sketch(f"{plan.name}: {op.name} axis", query, [axis_loop]), plan.name)
        mode = "new" if new else op.op
        self.add(fx.revolve(f"{plan.name}: {op.name}", sketch_id, axis_id, mode, None if new else self._scope(plan.name)), plan.name)

    def _holes(self, plan: PartPlan, group: list[Hole]) -> None:
        first = group[0]
        axis = unit(first.axis)
        plane = Plane.through(first.point, axis)
        opposite = bool(axis @ plane.normal > 0)  # Hole drills against the plane normal by default
        sketch_id = self.add(fx.sketch(f"{plan.name}: {first.name} points", self.plane_query(plane), [],
                                       [plane.to2d(h.point) for h in group]), plan.name)
        names = ", ".join(h.name for h in group)
        self.add(fx.hole(f"{plan.name}: {names}"[:120], sketch_id, first.style, first.diameter, first.depth, opposite,
                         self._scope(plan.name), first.thread, first.tapped_depth, 0.0, first.fit), plan.name)

    def build_part(self, plan: PartPlan) -> str:
        ops = list(plan.ops)
        if not ops or isinstance(ops[0], Hole) or ops[0].op == "remove":
            raise BuildError(plan.name, "first", "ERROR", "(first operation must add material)")
        first = ops.pop(0)
        (self._prism if isinstance(first, Prism) else self._revolve)(plan, first, True)  # type: ignore[arg-type]
        listed = self._list_parts()
        new_ids = [pid for pid in listed if pid not in self.known_ids]
        self.known_ids.update(listed)
        if len(new_ids) != 1:
            raise BuildError(plan.name, first.name, "ERROR", f"(created {len(new_ids)} bodies)")
        self.part_ids[plan.name] = new_ids[0]
        holes: dict[tuple, list[Hole]] = {}
        for op in ops:
            if isinstance(op, Hole):
                axis = unit(op.axis)
                plane = Plane.through(op.point, axis)
                key = (plane, tuple(np.round(axis, 9)), op.style, round(op.diameter, 9), op.depth and round(op.depth, 9), op.thread,
                       op.tapped_depth and round(op.tapped_depth, 9), op.fit)
                holes.setdefault(key, []).append(op)
            elif isinstance(op, Prism):
                self._prism(plan, op, False)
            else:
                self._revolve(plan, op, False)
        for group in holes.values():
            self._holes(plan, group)
        return new_ids[0]

    def set_metadata(self, plan: PartPlan) -> None:
        pid = self.part_ids[plan.name]
        props: list[dict[str, Any]] = [
            {"propertyId": METADATA["name"], "value": plan.name},
            {"propertyId": METADATA["appearance"], "value": {"color": _hex_rgb(plan.color), "opacity": 255}},
        ]
        if plan.mass is not None:
            props.append({"propertyId": METADATA["mass"], "value": f"{plan.mass:.6g} kg"})
            props.append({"propertyId": METADATA["description"], "value": f"Purchased envelope; mass = catalog ({plan.mass * 1000:.2f} g). "
                                                                           f"Material: {plan.material}"})
        else:
            material = {"id": plan.material, "displayName": plan.material, "libraryName": "R3 materials",
                        "properties": [{"name": "DENS", "displayName": "Density", "type": "REAL", "category": "Physical",
                                        "value": plan.density, "units": "kg/m^3", "description": "Density"}]}
            props.append({"propertyId": METADATA["material"], "value": material})
            props.append({"propertyId": METADATA["description"], "value": f"{plan.kind}; mass from geometry x {plan.density:g} kg/m^3"})
        # Part ids may contain '/' or '+': they must be percent-encoded in the path.
        self.api.json("POST", f"/metadata/d/{self.did}/w/{self.wid}/e/{self.eid}/p/{urllib.parse.quote(pid, safe='')}",
                      body={"jsonType": "metadata-part", "partId": pid, "properties": props})
        self.calls += 1


def build_robot(api: Transport, doc: Any, project: Project, result: StageResult,
                plans: dict[str, PartPlan] | None = None) -> dict[str, dict[str, Any]]:
    """Model every part in ``doc``'s Part Studio. Returns per-part metadata used by
    verification (kind, catalog mass, density, material, owner, part id)."""
    plans = plans if plans is not None else build_plans(project)
    builder = StudioBuilder(api, doc.did, doc.wid, doc.part_studio)
    started = time.time()
    meta: dict[str, dict[str, Any]] = {}
    for name, plan in plans.items():
        try:
            builder.build_part(plan)
            builder.set_metadata(plan)
        except BuildError as error:
            result.add(Diagnostic.error("cad_feature_failed", str(error), subjects=[name],
                                        data={"part": name, "operation": error.op, "status": error.status}))
            continue
        except OnshapeError as error:
            result.add(Diagnostic.error("onshape_request_failed", f"{name}: {error}", subjects=[name], data={"status": error.status}))
            continue
        meta[name] = {"kind": plan.kind, "fabricated": plan.kind != "purchased", "catalog_mass": plan.mass, "density": plan.density,
                      "material": plan.material, "owner": plan.owner, "mass_source": plan.mass_source,
                      "part_id": builder.part_ids[name], "operations": len(plan.ops)}
    for part, op in builder.warnings:
        result.add(Diagnostic.info("cad_feature_warning", f"{part}: feature {op!r} regenerated with a warning", subjects=[part]))
    result.metrics.update({"cad_features_posted": builder.calls, "cad_build_seconds": round(time.time() - started, 1),
                           "cad_parts_built": len(meta)})
    return meta
