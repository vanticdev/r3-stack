"""Connector and pin stage: models, exact pin positions, mated pairs and renders.

    python -m r3_connectors <project dir> [--no-render]

Reads the catalog connector geometry, the component models (official Raspberry
Pi 5 STEP, the KiCad stage's ``board.step``) and, when present, the wiring
stage's ``routes.json``/``routing_detail.json``. Writes ``build/connectors/``:

* ``models/connectors/<type>.glb``: every used connector type in its connector frame;
* ``models/components/<type>.glb``: detailed component models in their component frames;
* ``connectors.json``: robot-frame frames of every component connector and harness
  half, every pin (contact, tip, wire entry, signal, net, bindings), mated pairs
  with their alignment, and the wire endpoints checked against their pins;
* ``*_closeup.png``: reviewer renders (Pi header, XT60 pair, JST GH, servo plug);
* ``result.json``.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .display import display_paths
from .frames import Frame
from .models import ModelUnavailable, component_scene, connector_scene, export_glb, glb_digest
from .pins import component_connector_frame, instance_frame

STAGE = "connectors"
PIN_TOLERANCE = 1e-4  # wire endpoint to pin, m
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
        proxies = []
        for type_id in sorted(self.used_types()):
            ctype = self.catalog.connector(type_id)
            g = ctype.geometry
            path = self.out / "models" / "connectors" / f"{type_id}.glb"
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
            if g.model.source == "proxy":
                proxies.append(type_id)
            try:
                scene = connector_scene(self.catalog, type_id)
                export_glb(scene, path)
                self.scenes[type_id] = scene
                entry["glb"] = str(path.relative_to(self.out))
                entry["glb_sha256"] = glb_digest(path)
            except (ModelUnavailable, FileNotFoundError) as error:
                entry["glb"] = None
                self.result.add(Diagnostic.warning("connector_model_unavailable", f"{type_id}: {error}",
                                                   subjects=[type_id], data={"source": g.model.source}))
            self.data["types"][type_id] = entry
        if proxies:
            self.result.add(Diagnostic.info(
                "connector_model_proxy", f"{len(proxies)} connector types use datasheet proxies (no free KiCad or "
                                         f"vendor model): {', '.join(proxies)}",
                subjects=proxies, data={"count": len(proxies)}))
        self.result.artifact("connectors/models/connectors", "glb", "Connector type models (connector frame, m)")

    def component_models(self) -> None:
        done: dict[str, str | None] = {}
        for cid, comp in self.spec.components.items():
            ctype = self.catalog.component(comp.type)
            position, rotation = self.graph.component_pose(cid)
            entry: dict[str, Any] = {"type": comp.type, "kind": ctype.kind, "name": ctype.name,
                                     "frame": Frame(rotation, position).to_json(), "glb": None}
            if ctype.model is not None:
                entry.update({"source": ctype.model.source, "url": ctype.model.url, "license": ctype.model.license,
                              "model_path": ctype.model.path})
                if comp.type not in done:
                    path = self.out / "models" / "components" / f"{comp.type}.glb"
                    try:
                        scene = component_scene(ctype.model, self.project)
                        export_glb(scene, path)
                        self.component_scenes[comp.type] = scene
                        done[comp.type] = str(path.relative_to(self.out))
                    except (ModelUnavailable, FileNotFoundError) as error:
                        done[comp.type] = None
                        self.result.add(Diagnostic.warning(
                            "component_model_unavailable", f"{cid} ({comp.type}): {error}; catalog solids are used",
                            subjects=[cid], data={"source": ctype.model.source}))
                entry["glb"] = done[comp.type]
                if entry["glb"]:
                    entry["glb_sha256"] = glb_digest(self.out / entry["glb"])
            self.data["components"][cid] = entry

    # ------------------------------------------------------------- pins
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
        if conn.mates and conn.mates in self.graph.endpoints and self.graph.endpoints[conn.mates].owner_kind == "component":
            board = component_connector_frame(self.project, conn.mates)
            return (board, "pigtail") if conn.type == "integral" else (board.mated(), "component")
        node = (self.detail or {}).get("nodes", {}).get(f"{harness_id}.{name}")
        if node and node.get("frame"):
            f = node["frame"]
            return Frame.from_axes(f["origin"], f["x"], f["z"]), "floating"
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

    # ------------------------------------------------------------- checks
    def _frame(self, ref: str) -> Frame | None:
        c = self.data["connectors"].get(ref)
        if c is None:
            return None
        f = c["frame"]
        return Frame.from_axes(f["origin"], f["x"], f["z"])

    def _pair(self, a: str, b: str, kind: str) -> None:
        fa, fb = self._frame(a), self._frame(b)
        ta, tb = self.data["connectors"][a]["type"], self.data["connectors"][b]["type"]
        if fa is None or fb is None or "integral" in (ta, tb):
            return
        ga, gb = self.catalog.connector(ta).geometry, self.catalog.connector(tb).geometry
        if ga is None or gb is None:
            return
        d = fb.origin - fa.origin
        axial = float(d @ fa.z)
        lateral = float(np.linalg.norm(d - axial * fa.z))
        tilt = math.acos(float(np.clip(-fa.z @ fb.z, -1.0, 1.0)))
        clock = math.acos(float(np.clip(fa.x @ fb.x, -1.0, 1.0)))
        pin_errors = {}
        for number in sorted(set(ga.pins) & set(gb.pins), key=int):
            pa, pb = fa.point(ga.pins[number].position), fb.point(gb.pins[number].position)
            delta = pb - pa
            pin_errors[number] = float(np.linalg.norm(delta - (delta @ fa.z) * fa.z))
        missing = sorted(set(ga.pins) ^ set(gb.pins))
        # Insertion of the deeper-reaching contacts past the partner's mating face.
        insertion = max(ga.pins[n].tip for n in ga.pins) + max(gb.pins[n].tip for n in gb.pins) - axial
        entry = {"a": a, "b": b, "kind": kind, "types": [ta, tb], "axial_gap": _r(axial), "lateral_offset": _r(lateral),
                 "tilt_deg": _r(math.degrees(tilt), 4), "clocking_deg": _r(math.degrees(clock), 4),
                 "max_pin_lateral_error": _r(max(pin_errors.values(), default=0.0)), "contact_overlap": _r(insertion),
                 "pin_count_mismatch": missing, "center": _r(fa.origin)}
        self.data["mated_pairs"].append(entry)
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

    def verify_pi_header(self) -> None:
        """The catalog J8 pin map against the posts in the official Raspberry Pi 5 STEP."""
        for cid, comp in self.spec.components.items():
            ctype = self.catalog.component(comp.type)
            scene = self.component_scenes.get(comp.type)
            if scene is None or ctype.kind != "computer":
                continue
            for name, inst in ctype.connectors.items():
                g = self.catalog.connector(inst.type).geometry
                if g is None or g.pin_shape != "square_post" or not inst.embedded:
                    continue
                frame = instance_frame(inst)
                expected = {n: frame.point(p.position) for n, p in g.pins.items()}
                posts = []
                for node in scene.graph.nodes_geometry:
                    transform, geom = scene.graph[node]
                    lo, hi = trimesh.transform_points(scene.geometry[geom].bounds, transform)
                    lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)
                    ext = hi - lo
                    local = frame.inverse().point((lo + hi) / 2)
                    inside = g.envelope is None or (abs(local[0]) <= g.envelope[0] / 2 and abs(local[1]) <= g.envelope[1] / 2)
                    if inside and abs(ext[0] - g.pin_size) < 2e-4 and abs(ext[1] - g.pin_size) < 2e-4 and ext[2] > 0.005:
                        posts.append(((lo + hi) / 2, hi[2]))
                if not posts:
                    continue
                centres = np.array([p[0][:2] for p in posts])
                errors = {}
                for number, point in expected.items():
                    errors[number] = float(np.min(np.linalg.norm(centres - point[:2], axis=1)))
                worst = max(errors.values())
                tips = [p[1] for p in posts]
                tip_expected = float(frame.point(np.asarray(g.pins["1"].position) + np.asarray(g.pins["1"].axis) * g.pins["1"].tip)[2])
                self.data["checks"][f"{cid}.{name}_model"] = {"posts_found": len(posts), "max_pin_error": worst,
                                                               "post_tip_z": _r(float(np.median(tips))),
                                                               "pin_map_tip_z": _r(tip_expected)}
                self.result.metrics[f"{cid}.{name}_model_max_pin_error_m"] = worst
                if worst > 5e-5 or len(posts) != len(expected):
                    self.result.add(Diagnostic.error(
                        "connector_model_pin_mismatch", f"{cid}.{name}: catalog pin map is {worst * 1e3:.3f} mm from the "
                                                        f"posts of the vendor model ({len(posts)} posts found)",
                        subjects=[cid, f"{cid}.{name}"], data={"max_error": worst, "posts": len(posts), "units": "m"}))
                # PCB thickness: the vendor board against the catalog envelope.
                pcb = next((s for s in ctype.mechanical.solids if s.name == "pcb"), None)
                board = max((scene.geometry[scene.graph[n][1]] for n in scene.graph.nodes_geometry),
                            key=lambda m: float(np.prod(np.ptp(m.bounds, axis=0)[:2])))
                thickness = float(np.ptp(board.bounds[:, 2]))
                self.data["checks"][f"{cid}_pcb_thickness"] = {"model": thickness, "catalog": pcb.size[2] if pcb else None}
                if pcb is not None and pcb.size and abs(pcb.size[2] - thickness) > 1e-4:
                    self.result.add(Diagnostic.warning(
                        "component_model_dimension_mismatch",
                        f"{cid}: the vendor model's PCB is {thickness * 1e3:.2f} mm thick, the catalog solid "
                        f"{pcb.size[2] * 1e3:.2f} mm (connector poses follow the vendor model)",
                        subjects=[cid], data={"model": thickness, "catalog": pcb.size[2], "units": "m"}))

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
        self.verify_pi_header()
        self.verify_board_poses()
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
    stage = ConnectorStage(project, result)
    stage.run(render)
    result.assume("geometry", "Connector frame: origin at the centre of the contact array on the mating face, z toward "
                              "the mating half, x from pin 1; mated halves share origin and x with opposite z.")
    result.assume("geometry", "Board-side pin maps and bodies come from the KiCad library (footprints via pcbnew, STEP "
                              "models via cascadio); mating faces were measured on those models. Cable-side housings "
                              "without free vendor CAD are datasheet proxies.")
    result.assume("geometry", "The Raspberry Pi 5 is the official Raspberry Pi Ltd STEP (MIT); the HAT is the KiCad "
                              "stage's board.step. Their connector poses are taken from those models.")
    result.duration_s = time.time() - started
    result.write(stage.out / "result.json")
    return result


__all__ = ["ConnectorStage", "STAGE", "run"]
