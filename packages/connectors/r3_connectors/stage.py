"""Connector and pin stage: models, exact pin positions, mated pairs and renders.

    python -m r3_connectors <project dir> [--no-render]

Reads catalog pin metadata and verified native pulled component meshes,
and, when present, the wiring
stage's ``routes.json``/``routing_detail.json``. Writes ``build/connectors/``:

* ``models/components/<instance hash>.glb``: pulled component meshes in their part frames;
* ``connectors.json``: robot-frame frames of every component connector and harness
  half, every pin (contact, tip, wire entry, signal, net, bindings), mated pairs
  with their alignment, and the wire endpoints checked against their pins;
* ``*_closeup.png``: reviewer renders of available connector geometry;
* ``result.json``.
"""

from __future__ import annotations

import json
import hashlib
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .display import display_paths, overlaps
from .frames import Frame
from .models import export_glb, glb_digest
from .pins import component_connector_frame, instance_frame, mating_site_frame

STAGE = "connectors"
PIN_TOLERANCE = 1e-4  # wire endpoint to pin, m
DISPLAY_MERGED_FRACTION = 0.25  # share of a conductor core drawn inside its neighbour before a warning
MATE_LATERAL_LIMIT = 1e-4  # m
MATE_ANGLE_LIMIT = math.radians(1.0)
MATE_GAP_LIMIT = 5e-4  # axial gap between mating faces before it is reported, m


def _r(v: Any, nd: int = 7) -> Any:
    if isinstance(v, (list, tuple, np.ndarray)):
        return [_r(x, nd) for x in v]
    return round(float(v), nd)


def _load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


class ConnectorStage:
    def __init__(self, project: Project, result: StageResult) -> None:
        self.project = project
        self.spec = project.spec
        self.catalog = project.catalog
        self.graph = project.graph
        self.result = result
        self.out = project.build_dir(STAGE)
        wiring = project.build_dir("wiring")
        self.routes = _load(wiring / "routes.json")
        self.detail = _load(wiring / "routing_detail.json")
        self.scenes: dict[str, trimesh.Scene] = {}
        self.display: dict[str, np.ndarray] = {}
        self.component_scenes: dict[str, trimesh.Scene] = {}
        self._mesh_bodies: dict[str, list[trimesh.Trimesh]] = {}
        from r3_model.spec_files import validate_pulled_spec as load_pulled
        from r3_model.pulled_view import apply_pulled
        self.pulled = load_pulled(project.root)
        apply_pulled(project, self.pulled)
        self.graph = project.graph
        self.data: dict[str, Any] = {"units": "SI (m), robot frame", "types": {}, "components": {}, "connectors": {},
                                     "mated_pairs": [], "wires": {}, "checks": {}}

    # ------------------------------------------------------------- models
    def used_types(self) -> set[str]:
        types = set()
        for cid, comp in self.spec.components.items():
            types.update(i.type for i in self.catalog.component(comp.type).connectors.values())
        for harness in self.spec.electrical.harnesses:
            types.update(c.type for c in harness.connectors.values())
        return {t for t in types if t in self.catalog.connectors and self.catalog.connector(t).geometry is not None}

    def connector_models(self) -> None:
        for type_id in sorted(self.used_types()):
            ctype = self.catalog.connector(type_id)
            g = ctype.geometry
            entry: dict[str, Any] = {"family": ctype.family, "gender": ctype.gender, "mates_with": ctype.mates_with,
                                     "source": g.model.source, "model_path": g.model.path, "license": g.model.license,
                                     "provenance": g.model.provenance.model_dump(), "footprint": g.footprint,
                                     "pin_shape": g.pin_shape, "pin_size": g.pin_size, "color": g.color,
                                     "contact_color": g.contact_color, "envelope": _r(g.envelope or []),
                                     "depth": g.depth, "reach": g.reach,
                                     "pins": {n: {"position": _r(p.position), "axis": _r(p.axis), "tip": p.tip,
                                                  "pad": None if p.pad is None else _r(p.pad),
                                                  "wire_entry": None if p.wire_entry is None else _r(p.wire_entry)}
                                              for n, p in g.pins.items()}}
            # Type geometry describes pins and ratings, never physical CAD.
            entry["glb"] = None
            self.data["types"][type_id] = entry

    def component_models(self) -> None:
        for cid, comp in self.spec.components.items():
            part = self.pulled.parts.get(cid)
            if part is None:
                continue
            scene = trimesh.load(part.stl, force="scene")
            self.component_scenes[cid] = scene
            path = self.out / "models" / "components" / f"{hashlib.sha256(cid.encode()).hexdigest()}.glb"
            export_glb(scene, path)
            pose = self.pulled.poses[cid]
            self.data["components"][cid] = {"type": comp.type, "kind": self.catalog.component(comp.type).kind,
                "name": cid, "source": "pulled", "frame": Frame(pose[:3, :3], pose[:3, 3]).to_json(),
                "glb": str(path.relative_to(self.out)), "glb_sha256": glb_digest(path)}

    def _bindings(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name, binding in self.spec.software.bindings.items():
            for pin in binding.pins:
                out.setdefault(pin, []).append(f"{name} ({binding.interface})")
        return out

    def _net_peers(self, ref: str) -> list[str]:
        try:
            nodes = self.graph.component_pins_on_net(ref)
        except KeyError:
            return []
        own = ref.split(".")[0]
        return sorted({n[2:] for n in nodes if not n[2:].startswith(own + ".")})

    def component_connectors(self) -> None:
        bindings = self._bindings()
        for cid, comp in self.spec.components.items():
            ctype = self.catalog.component(comp.type)
            for name, inst in ctype.connectors.items():
                ref = f"{cid}.{name}"
                frame = component_connector_frame(self.project, ref)
                g = self.catalog.connector(inst.type).geometry if inst.type in self.catalog.connectors else None
                pins: dict[str, Any] = {}
                numbers = list(g.pins) if g is not None else list(inst.pins)
                for number in numbers:
                    signal = inst.pins.get(number, "")
                    entry: dict[str, Any] = {"signal": signal, "bindings": bindings.get(f"{ref}.{number}", []),
                                             "net": self._net_peers(f"{cid}.{signal}") if signal else []}
                    if g is not None:
                        p = g.pins[number]
                        entry.update({"contact": _r(frame.point(p.position)), "axis": _r(frame.vector(p.axis)),
                                      "tip": _r(frame.point(np.asarray(p.position) + np.asarray(p.axis) * p.tip))})
                        if p.pad is not None:
                            entry["pad"] = _r(frame.point(p.pad))
                    pins[number] = entry
                self.data["connectors"][ref] = {
                    "owner": "component", "component": cid, "name": name, "type": inst.type, "frame": frame.to_json(),
                    "embedded": inst.embedded, "source": inst.source, "pins": pins,
                    "glb": self.data["types"].get(inst.type, {}).get("glb")}

    def _harness_frame(self, harness_id: str, name: str, conn: Any) -> tuple[Frame | None, str]:
        ref = f"{harness_id}.{name}"
        if conn.type != "integral":
            part = self.pulled.parts.get(ref)
            if part is None:
                self.result.add(Diagnostic.error("cad_pull_missing", f"{ref}: cable half missing from native pull", subjects=[ref]))
                return None, "unplaced"
            local = part.mate_connectors.get("mating", part.mate_connectors.get("origin"))
            if local is None:
                self.result.add(Diagnostic.error("cad_pull_invalid", f"{ref}: missing native mating/origin connector", subjects=[ref]))
                return None, "unplaced"
            matrix = self.pulled.poses[ref] @ local
            frame = Frame(matrix[:3, :3], matrix[:3, 3])
            node = (self.detail or {}).get("nodes", {}).get(ref)
            if node and node.get("frame"):
                f = node["frame"]
                stale = Frame.from_axes(f["origin"], f["x"], f["z"])
                if not np.allclose(stale.matrix(), matrix, atol=1e-7, rtol=0):
                    self.result.add(Diagnostic.error("connector_routes_stale", f"{ref}: routing frame differs from native pull; rerun wiring", subjects=[ref]))
            return frame, "pulled"
        if conn.mates and conn.mates in self.graph.endpoints and self.graph.endpoints[conn.mates].owner_kind == "component":
            return mating_site_frame(self.project, ref, conn), "pigtail"
        return None, "unplaced"

    def harness_connectors(self) -> None:
        for harness in self.spec.electrical.harnesses:
            for name, conn in harness.connectors.items():
                ref = f"{harness.id}.{name}"
                frame, kind = self._harness_frame(harness.id, name, conn)
                if frame is None:
                    continue
                g = self.catalog.connector(conn.type).geometry if conn.type in self.catalog.connectors else None
                wires = {}
                for wire in harness.wires:
                    for end, other in ((wire.from_, wire.to), (wire.to, wire.from_)):
                        if end.split(".")[0] == name and "." in end:
                            wires[end.split(".", 1)[1]] = {"wire": wire.id, "color": wire.color, "signal": wire.signal,
                                                           "awg": wire.awg, "to": other}
                pins: dict[str, Any] = {}
                for number in (list(g.pins) if g is not None else sorted(wires)):
                    entry = dict(wires.get(number, {}))
                    if g is not None:
                        p = g.pins[number]
                        entry["contact"] = _r(frame.point(p.position))
                        if p.wire_entry is not None:
                            entry["wire_entry"] = _r(frame.point(p.wire_entry))
                            entry["wire_axis"] = _r(frame.vector(p.wire_axis or [0, 0, -1]))
                    pins[number] = entry
                self.data["connectors"][ref] = {"owner": "harness", "harness": harness.id, "name": name, "type": conn.type,
                                                "mates": conn.mates, "placement": kind, "frame": frame.to_json(),
                                                "pins": pins, "glb": self.data["types"].get(conn.type, {}).get("glb")}
                if conn.at is not None:
                    site = self.graph.mating_sites[ref]
                    self.data["connectors"][ref].update({"at": conn.at, "site": site.id, "pin_map": site.pin_map})

    # ------------------------------------------------------------- checks
    def _frame(self, ref: str) -> Frame | None:
        c = self.data["connectors"].get(ref)
        if c is None:
            return None
        f = c["frame"]
        return Frame.from_axes(f["origin"], f["x"], f["z"])

    def _pair(self, a: str, b: str, kind: str) -> None:
        self.mesh_pair(a, b, kind)
        fa, fb = self._frame(a), self._frame(b)
        ta, tb = self.data["connectors"][a]["type"], self.data["connectors"][b]["type"]
        if fa is None or fb is None or "integral" in (ta, tb):
            return
        ga, gb = self.catalog.connector(ta).geometry, self.catalog.connector(tb).geometry
        if ga is None or gb is None:
            return
        site = self.graph.mating_sites.get(b)
        partial = site is not None and site.id != "full"
        mapping = site.pin_map if partial else {n: n for n in gb.pins}
        if partial:
            position, rotation = self.graph.component_pose(a.partition(".")[0])
            fa = Frame(rotation, position) @ Frame.from_placement(site.frame)
        d = fb.origin - fa.origin
        axial = float(d @ fa.z)
        lateral = float(np.linalg.norm(d - axial * fa.z))
        tilt = math.acos(float(np.clip(-fa.z @ fb.z, -1.0, 1.0)))
        clock = math.acos(float(np.clip(fa.x @ fb.x, -1.0, 1.0)))
        pin_errors = {}
        for number in sorted((n for n in gb.pins if mapping.get(n) in ga.pins), key=int):
            # Header contacts remain in the complete connector frame; the
            # selected housing is centred on its partial site.
            board_frame = self._frame(a) if partial else fa
            pa, pb = board_frame.point(ga.pins[mapping[number]].position), fb.point(gb.pins[number].position)
            delta = pb - pa
            pin_errors[number] = float(np.linalg.norm(delta - (delta @ fa.z) * fa.z))
        missing = [] if partial else sorted(set(ga.pins) ^ set(gb.pins))
        # Insertion of the deeper-reaching contacts past the partner's mating face.
        insertion = max(ga.pins[n].tip for n in ga.pins) + max(gb.pins[n].tip for n in gb.pins) - axial
        entry = {"a": a, "b": b, "kind": kind, "types": [ta, tb], "axial_gap": _r(axial), "lateral_offset": _r(lateral),
                 "tilt_deg": _r(math.degrees(tilt), 4), "clocking_deg": _r(math.degrees(clock), 4),
                 "max_pin_lateral_error": _r(max(pin_errors.values(), default=0.0)), "contact_overlap": _r(insertion),
                 "pin_count_mismatch": missing, "center": _r(fa.origin)}
        self.data["mated_pairs"].append(entry)
        if partial:
            entry.update({"site": site.id, "pin_map": site.pin_map})
        data = {"lateral_offset": lateral, "axial_gap": axial, "tilt": tilt, "clocking": clock,
                "max_pin_lateral_error": entry["max_pin_lateral_error"], "limit_lateral": MATE_LATERAL_LIMIT,
                "limit_angle": MATE_ANGLE_LIMIT, "units": "m, rad"}
        if lateral > MATE_LATERAL_LIMIT or tilt > MATE_ANGLE_LIMIT or clock > MATE_ANGLE_LIMIT or \
                entry["max_pin_lateral_error"] > MATE_LATERAL_LIMIT or missing:
            self.result.add(Diagnostic.error(
                "connector_mate_misaligned",
                f"{a} / {b}: mating faces offset {lateral * 1e3:.2f} mm laterally, tilt {math.degrees(tilt):.2f} deg, "
                f"clocking {math.degrees(clock):.2f} deg, worst pin {entry['max_pin_lateral_error'] * 1e3:.2f} mm"
                + (f", pins without a partner {missing}" if missing else ""),
                subjects=[a, b], data=data))
        elif abs(axial) > MATE_GAP_LIMIT:
            self.result.add(Diagnostic.warning(
                "connector_mate_gap", f"{a} / {b}: mating faces {axial * 1e3:+.2f} mm apart (contacts overlap "
                                      f"{insertion * 1e3:.2f} mm)", subjects=[a, b], data=data))

    def mated_pairs(self) -> None:
        seen: set[tuple[str, str]] = set()
        for harness in self.spec.electrical.harnesses:
            for name, conn in harness.connectors.items():
                ref = f"{harness.id}.{name}"
                if conn.mates and conn.mates in self.data["connectors"] and ref in self.data["connectors"]:
                    key = tuple(sorted((ref, conn.mates)))
                    if key in seen:
                        continue
                    seen.add(key)
                    kind = "floating" if self.data["connectors"][conn.mates]["owner"] == "harness" else "harness-component"
                    self._pair(conn.mates, ref, kind)
        for mate in self.spec.electrical.direct_mates:
            if mate.a in self.data["connectors"] and mate.b in self.data["connectors"]:
                self._pair(mate.b, mate.a, "direct")
        direct = [p for p in self.data["mated_pairs"] if p["kind"] == "direct"]
        for pair in direct:
            self.result.metrics.setdefault("direct_mates", {})[f"{pair['a']}|{pair['b']}"] = {
                "axial_gap_mm": pair["axial_gap"] * 1e3, "lateral_offset_mm": pair["lateral_offset"] * 1e3,
                "contact_overlap_mm": pair["contact_overlap"] * 1e3}

    def mesh_pair(self, a: str, b: str, kind: str) -> None:
        """Check owner geometry plus the placed model of non-embedded connectors.

        Embedded connectors must exist in the owner mesh. Non-embedded ones
        have a separate model supplier; proxies cannot verify real contacts.
        """
        from .mesh_mates import check_pair, check_side, detect_supplied, scene_bodies

        checks = self.data["checks"].setdefault("mesh_mates", {})
        key = f"{a}|{b}"
        record: dict[str, Any] = {"a": a, "b": b, "kind": kind, "sides": {}}
        checks[key] = record
        detections = []
        diagnostics = []
        geometries = {t: c.geometry for t, c in self.catalog.connectors.items() if c.geometry is not None}
        for ref in (a, b):
            info = self.data["connectors"][ref]
            geometry = geometries.get(info["type"])
            frame = self._frame(ref)
            scene = None
            transform = np.eye(4)
            source = ""
            cid = info.get("component") if info["owner"] == "component" else ref
            if cid in self.pulled.parts:
                part = self.pulled.parts[cid]
                scene = trimesh.load(part.stl, force="scene")
                transform = self.pulled.poses[cid]
                source = f"pulled/{cid} (SHA-256 {part.sha256['stl']})"
            # Cache owner meshes separately from connector instances: two ports
            # on the same component have different placed supplemental models.
            bodies = []
            if scene is not None:
                owner_key = info.get("component", ref)
                if owner_key not in self._mesh_bodies:
                    self._mesh_bodies[owner_key] = scene_bodies(scene, transform)
                bodies = self._mesh_bodies[owner_key]
            supplied = []
            proxy_bodies = []
            supplier_missing = False
            # A placed catalog connector is metadata, not pulled CAD evidence.
            if (scene is None and not supplied and not proxy_bodies) or geometry is None or frame is None:
                reason = "No pulled owner/connector contact geometry available; pull the connector instance from Onshape."
                record["sides"][ref] = {"declared_type": info["type"], "status": "unverifiable", "reason": reason,
                                       "source": source}
                diagnostics.append(Diagnostic.warning("connector_unverifiable", f"{ref}: {reason}", subjects=[a, b, ref]))
                detections.append(None)
                continue
            detection = detect_supplied(bodies, supplied, frame, geometry, source, supplier_missing, proxy_bodies)
            record["sides"][ref] = detection.json()
            detections.append(detection)
        # Bare conductors are fabricated from the routed wire, not a sourced
        # plug. Verify each occupied pad against the actual route endpoint.
        for index, ref in enumerate((a, b)):
            info = self.data["connectors"][ref]
            geometry = geometries.get(info["type"])
            if geometry and geometry.pin_shape == "pad" and detections[index] is not None:
                partner = (a, b)[1-index]
                partner_info = self.data["connectors"][partner]
                partner_type = self.catalog.connectors[partner_info["type"]]
                if partner_info["owner"] == "harness" and partner_type.gender == "none":
                    from .mesh_mates import check_solder
                    measured, solder_diagnostics = check_solder(
                        ref, partner, self._frame(ref), detections[index], self.routes)
                    measured.update(kind=kind)
                    checks[key] = measured
                    self.result.extend(solder_diagnostics)
                    return
        if any(d is None for d in detections):
            # Still check an available part even if its partner has no source.
            for ref, detection in zip((a, b), detections):
                if detection is not None:
                    declared = self.data["connectors"][ref]["type"]
                    side, issues = check_side(ref, detection, geometries[declared], declared, geometries)
                    record["sides"][ref] = side
                    diagnostics.extend(issues)
            record["status"] = "failed" if any(d.severity.value == "error" for d in diagnostics) else "unverifiable"
            record["diagnostics"] = [d.model_dump(mode="json") for d in diagnostics]
            self.result.extend(diagnostics)
            return
        ta, tb = (self.data["connectors"][ref]["type"] for ref in (a, b))
        ga, gb = geometries[ta], geometries[tb]
        required = max(0., max(p.tip for p in ga.pins.values()) + max(p.tip for p in gb.pins.values()))
        from r3_model.units import to_si
        # Mechanical interfaces may be named differently from electrical ones.
        # Match both owners and the mating plane, not arbitrary owner joins.
        if self.spec.assembly:
            for join in self.spec.assembly.joins:
                if {join.a.split(".")[0], join.b.split(".")[0]} != {a.split(".")[0], b.split(".")[0]}:
                    continue
                if "min_insertion" in join.verify:
                    pairs = [(join.a, a), (join.b, b)] if join.a.split(".")[0] == a.split(".")[0] else [(join.a, b), (join.b, a)]
                    if all(np.linalg.norm(self.project.assembly.interface_world(interface)[:3, 3] -
                                          self._frame(connector).origin) <= MATE_LATERAL_LIMIT
                           for interface, connector in pairs):
                        required = to_si(join.verify["min_insertion"])
        measured, diagnostics = check_pair(a, b, self._frame(a), self._frame(b), *detections, ga, gb,
                                           (ta, tb), geometries, required)
        measured.update(kind=kind, status="failed" if any(d.severity.value == "error" for d in diagnostics) else
                        "unverifiable" if diagnostics else "passed")
        measured["diagnostics"] = [d.model_dump(mode="json") for d in diagnostics]
        checks[key] = measured
        self.result.extend(diagnostics)

    def wire_endpoints(self) -> None:
        if self.routes is None:
            self.result.add(Diagnostic.warning(
                "connector_routes_missing", "build/wiring/routes.json not found: run the wiring stage for pin-level wire "
                                            "endpoints and floating mated pairs", data={"routes": 0}))
            return
        worst = 0.0
        for wire_id, wire in self.routes["wires"].items():
            path = np.asarray(wire["path"], dtype=float)
            ends_out = []
            for index, end in enumerate(wire.get("ends") or [None, None]):
                point = path[0] if index == 0 else path[-1]
                if end is None:
                    ends_out.append(None)
                    continue
                conn = self.data["connectors"].get(end["connector"], {})
                pin = conn.get("pins", {}).get(end["pin"], {})
                expected = pin.get("wire_entry")
                if expected is None and conn.get("placement") == "pigtail":
                    expected = end["position"]  # pigtail roots are defined by the router's lead spacing
                error = float(np.linalg.norm(point - np.asarray(expected))) if expected is not None else math.inf
                worst = max(worst, error)
                ends_out.append({**end, "error": _r(error)})
                if error > PIN_TOLERANCE:
                    self.result.add(Diagnostic.error(
                        "wire_not_on_pin", f"{wire_id} ends {error * 1e3:.2f} mm from {end['connector']} pin {end['pin']}",
                        subjects=[wire_id, end["connector"]], data={"error": error, "limit": PIN_TOLERANCE, "units": "m"}))
            self.data["wires"][wire_id] = {"harness": wire["harness"], "ends": ends_out, "fanout": wire.get("fanout")}
        self.result.metrics["max_wire_pin_error_m"] = worst
        self.data["checks"]["max_wire_pin_error"] = worst
        diameters = {w.id: float(self.catalog.wire(w.awg).outer_diameter)
                     for h in self.spec.electrical.harnesses for w in h.wires}
        self.display = display_paths(self.routes, diameters)
        for wire_id, path in self.display.items():
            if wire_id in self.data["wires"]:
                self.data["wires"][wire_id]["display_path"] = _r(path, 6)
                self.data["wires"][wire_id]["od"] = diameters.get(wire_id)
        merged = overlaps(self.display, self.routes["wires"], diameters)
        self.data["checks"]["display_overlaps"] = [{**o, "min_distance": _r(o["min_distance"]),
                                                    "required": _r(o["required"]), "fraction": _r(o["fraction"], 3)}
                                                   for o in merged]
        self.result.metrics["display_overlap_pairs"] = len(merged)
        self.result.metrics["display_overlap_max_fraction"] = max((o["fraction"] for o in merged), default=0.0)
        for o in merged:
            if o["fraction"] > DISPLAY_MERGED_FRACTION or o["min_distance"] < 0.5 * o["required"]:
                self.result.add(Diagnostic.warning(
                    "wire_display_merged",
                    f"{o['a']} and {o['b']} are drawn merged over {o['fraction'] * 100:.0f}% of the core "
                    f"(closest {o['min_distance'] * 1e3:.2f} mm, conductor pitch {o['required'] * 1e3:.2f} mm)",
                    subjects=[o["a"], o["b"]], data={"fraction": o["fraction"], "min_distance": o["min_distance"],
                                                     "required": o["required"], "units": "m"},
                    hint="Display geometry only (checks use the bundle radius); a sharp bend or a crossover "
                         "inside the core keeps the cross-section from spreading there."))

    def verify_embedded_posts(self) -> None:
        """Compare every embedded post array to its owner mesh using the pair detector.

        Keep the existing evidence keys for readers of connectors.json. This is
        independent of whether a particular header is connected in this robot.
        """
        from scipy.optimize import linear_sum_assignment
        from scipy.spatial.distance import cdist
        from .mesh_mates import detect, scene_bodies

        for cid, comp in self.spec.components.items():
            ctype = self.catalog.component(comp.type)
            scene = self.component_scenes.get(cid)
            if scene is None:
                continue
            bodies = None
            for name, inst in ctype.connectors.items():
                g = self.catalog.connector(inst.type).geometry
                if g is None or g.pin_shape != "square_post" or not inst.embedded or max(p.tip for p in g.pins.values()) <= 0:
                    continue
                if bodies is None:
                    bodies = scene_bodies(scene)
                frame = instance_frame(inst)
                detection = detect(bodies, frame, g)
                expected = np.asarray([p.position[:2] for p in g.pins.values()])
                found = np.asarray([c.centre[:2] for c in detection.contacts])
                worst = None
                if len(found):
                    distances = cdist(expected, found)
                    rows, cols = linear_sum_assignment(distances)
                    worst = float(distances[rows, cols].max())
                tips = [frame.point(c.centre + [0, 0, c.high])[2] for c in detection.contacts]
                first_pin = next(iter(g.pins.values()))
                self.data["checks"][f"{cid}.{name}_model"] = {
                    "posts_found": len(found), "max_pin_error": worst,
                    "post_tip_z": float(np.median(tips)) if tips else None,
                    "pin_map_tip_z": float(frame.point(np.asarray(first_pin.position) +
                                                   np.asarray(first_pin.axis)*first_pin.tip)[2])}
                if worst is None or worst > 5e-5 or len(found) != len(expected):
                    self.result.add(Diagnostic.error("connector_model_pin_mismatch",
                        f"{cid}.{name}: embedded contact array differs from the owner model",
                        subjects=[cid, f"{cid}.{name}"], data={"max_error": worst, "posts": len(found), "limit": 5e-5}))

    def verify_board_poses(self) -> None:
        """Catalog connector poses of KiCad boards against the placed footprints (pcbnew)."""
        from .board import board_connectors
        from .kicad import kicad_available

        if not kicad_available():
            return
        for board in self.spec.pcb.boards:
            ctype = self.catalog.component(self.spec.components[board.component].type)
            pcb = next(iter(sorted((self.project.root / board.source).glob("*.kicad_pcb"))), None)
            refs = {n: i.source.split()[0].split(":")[1] for n, i in ctype.connectors.items() if i.source.startswith("kicad:")}
            if pcb is None or not refs:
                continue
            placed = board_connectors(pcb, ctype, self.catalog, refs)
            worst = 0.0
            for name, b in placed.items():
                cat = instance_frame(ctype.connectors[name])
                error = float(max(np.linalg.norm(cat.origin - b.frame.origin), np.linalg.norm(cat.z - b.frame.z) * 0.01,
                                  np.linalg.norm(cat.x - b.frame.x) * 0.01))
                worst = max(worst, error)
                self.data["checks"].setdefault("board_poses", {})[f"{board.component}.{name}"] = {
                    "reference": b.reference, "footprint": b.footprint, "side": b.side, "pose_error": error,
                    "pad_error": b.pad_error, "board_nets": b.nets}
                if error > 5e-5:
                    self.result.add(Diagnostic.error(
                        "connector_pose_mismatch", f"{board.component}.{name}: catalog pose is {error * 1e3:.3f} mm from "
                                                   f"KiCad {b.reference}", subjects=[board.component, f"{board.component}.{name}"],
                        data={"error": error, "board_origin": b.frame.origin.tolist(), "units": "m"}))
            self.result.metrics[f"{board.component}_max_pose_error_m"] = worst

    # ------------------------------------------------------------- run
    def run(self, render: bool = True) -> None:
        self.connector_models()
        self.component_models()
        self.component_connectors()
        self.harness_connectors()
        self.mated_pairs()
        self.wire_endpoints()
        self.verify_embedded_posts()
        self.verify_board_poses()
        from .connection_points import connection_points

        (self.out / "connection_points.json").write_text(json.dumps(connection_points(self.project), indent=1))
        self.result.artifact("connectors/connection_points.json", "json", "All component mating sites and floating pairs")
        (self.out / "connectors.json").write_text(json.dumps(self.data, indent=1, default=float))
        self.result.artifact("connectors/connectors.json", "json", "Connector frames, pins, mated pairs, wire ends")
        if render:
            from .views import render_closeups

            for path in render_closeups(self):
                self.result.artifact(f"connectors/{path.name}", "plot_png", "Connector close-up render")
        self.result.metrics.update({"connector_types": len(self.data["types"]),
                                    "component_connectors": sum(1 for c in self.data["connectors"].values()
                                                                if c["owner"] == "component"),
                                    "harness_halves": sum(1 for c in self.data["connectors"].values() if c["owner"] == "harness"),
                                    "mated_pairs": len(self.data["mated_pairs"])})


def run(project: Project, render: bool = True) -> StageResult:
    started = time.time()
    result = StageResult(stage=STAGE)
    result.inputs = project.fingerprint()
    from r3_model.pulled import PullError
    from r3_model.spec_files import validate_pulled_spec as load_pulled
    from r3_model.pulled_view import apply_pulled
    try:
        apply_pulled(project, load_pulled(project.root))
    except PullError as error:
        result.add(Diagnostic.error(error.code, str(error)))
        return result
    stage = ConnectorStage(project, result)
    stage.run(render)
    result.assume("geometry", "Connector frame: origin at the centre of the contact array on the mating face, z toward "
                              "the mating half, x from pin 1; mated halves share origin and x with opposite z.")
    result.assume("geometry", "Model sources, transforms and provenance come from the catalog and build artifacts. "
                              "Proxy geometry records occupancy but cannot verify purchased contacts.")
    result.assume("geometry", "Embedded connectors use their component model; non-embedded connectors also use "
                              "their separately placed catalog model. Sources and verification limits are recorded per pair.")
    result.duration_s = time.time() - started
    result.write(stage.out / "result.json")
    return result


__all__ = ["ConnectorStage", "STAGE", "run"]
