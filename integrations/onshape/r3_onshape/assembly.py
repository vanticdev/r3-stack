"""Assembly from verified interfaces: the kinematic source of truth.

Every part is inserted at its modeled pose. Mates are placed only on verified
physical interfaces, on the very faces the fit checks measured: fastened mates
on fastener hole/shank axes and seating faces, revolute mates on the verified
running fits (kingpin screws, stub axles, bearings, servo spline), ball mates on
the tie-rod ball/socket spheres. Implicit mate connectors are inferred from
those faces; connector B is then corrected (flip, rotation about z, offset)
so it coincides with connector A in the modeled pose, all occurrences are reset
to the modeled pose, and the definition is read back to verify that every mate
is satisfied and that every robot.yaml joint has a matching mate (type, axis,
origin, limits).
"""

from __future__ import annotations

import heapq
import math
import urllib.parse
from typing import Any

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from . import features as fx
from .client import Transport
from .fit import FitReport, Link, coaxial, rotor_crossing_links, unit
from .model import GeometryModel

POSE_TOL = 1e-6  # m / rad equivalents for occurrence transforms


def _cs(data: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return np.asarray(data["origin"], dtype=float), unit(data["xAxis"]), unit(data["zAxis"])


def correction(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, float, np.ndarray, float]:
    """Flip / rotation about z / translation (in the corrected frame) that make
    connector B coincide with connector A; also returns the off-axis offset."""
    oa, xa, za = _cs(a)
    ob, xb, zb = _cs(b)
    flip = float(za @ zb) < 0
    if flip:
        zb = -zb  # flipPrimary keeps x
    theta = math.atan2(float(np.cross(xb, xa) @ za), float(xb @ xa))
    ya = np.cross(za, xa)
    d = oa - ob
    translation = np.array([d @ xa, d @ ya, d @ za])
    return flip, theta, translation, float(math.hypot(translation[0], translation[1]))


class AssemblyBuilder:
    def __init__(self, api: Transport, did: str, wid: str, studio: str, project: Project, model: GeometryModel, fit: FitReport,
                 result: StageResult) -> None:
        self.api, self.did, self.wid, self.studio = api, did, wid, studio
        self.project, self.spec, self.model, self.fit, self.result = project, project.spec, model, fit, result
        self.eid = ""
        self.occurrence: dict[str, str] = {}
        self.mates: list[dict[str, Any]] = []

    @property
    def base(self) -> str:
        return f"/assemblies/d/{self.did}/w/{self.wid}/e/{self.eid}"

    # ------------------------------------------------------------------ insert
    def create(self, name: str = "R3 assembly") -> str:
        self.eid = self.api.json("POST", f"/assemblies/d/{self.did}/w/{self.wid}", body={"name": name})["id"]
        for part in self.model.parts.values():
            self.api.json("POST", self.base + "/instances", body={"documentId": self.did, "elementId": self.studio, "partId": part.part_id,
                                                                  "isAssembly": False, "isWholePartStudio": False})
        definition = self.definition()
        by_part = {p.part_id: name for name, p in self.model.parts.items()}
        for instance in definition["rootAssembly"]["instances"]:
            name = by_part.get(instance.get("partId"))
            if name:
                self.occurrence[name] = instance["id"]
        return self.eid

    def definition(self) -> dict[str, Any]:
        return self.api.json("GET", self.base, query={"includeMateFeatures": True, "includeMateConnectors": True})

    # -------------------------------------------------------------- mate plan
    def _faces_for(self, link: Link) -> tuple[str, str, str, str, str] | None:
        """(part_a, face_a, part_b, face_b, inference) for a verified link."""
        axes = self.fit.interface_axes
        if link.via in axes and link.kind not in ("fastener", "thread", "strap"):
            data = axes[link.via]
            interface = next(i for i in self.spec.mechanical.interfaces if i.id == link.via)
            a, b = interface.parts
            if "shaft_face" in data:
                return a, data["shaft_face"], b, data["bore_face"], "MID_AXIS_POINT"
            if "ball_face" in data:
                return a, data["ball_face"], b, data["socket_face"], "CENTER"
            if "faces" in data:
                return a, data["faces"][0], b, data["faces"][1], "CENTROID"
            return None
        fastener = next((f for f in self.spec.mechanical.fasteners if f.id == link.via), None)
        if fastener is not None:
            point, axis = np.asarray(fastener.position, dtype=float), unit(fastener.axis)
            faces = []
            for part in (link.a, link.b):
                cylinders = [f for f in self.model[part].faces_of("CYLINDER") if coaxial(f, point, axis)[0] < 3.5e-3
                             and coaxial(f, point, axis)[1] < 2e-5]
                if not cylinders:
                    return None
                faces.append(min(cylinders, key=lambda f: abs(f.radius - self.project.catalog.fastener(fastener.type).d / 2)))
            return link.a, faces[0].id, link.b, faces[1].id, "MID_AXIS_POINT"
        if link.kind == "strap":
            from .fit import plane_contacts

            contacts = plane_contacts(self.model[link.a], self.model[link.b])
            if contacts:
                best = max(contacts, key=lambda c: c.area)
                return link.a, best.face_a, link.b, best.face_b, "CENTROID"
        return None

    def plan_mates(self, root: str = "chassis") -> list[tuple[Link, str]]:
        """Spanning tree over verified links (rigid links preferred, ball joints
        last), plus every ball joint as a loop-closing mate, plus every joint."""
        joints = {j.interface: j for j in self.spec.mechanical.joints if j.interface}
        steering = {i.id for i in self.spec.mechanical.interfaces if i.kind == "spline"}
        def mate_type(link: Link) -> str:
            if link.kind == "ball_joint":
                return "BALL"
            if link.kind in ("revolute_fit", "spline"):
                return "REVOLUTE"
            return "FASTENED"
        def cost(link: Link) -> float:
            return 50.0 if link.kind == "ball_joint" else (2.0 if not link.rigid else 1.0)
        crossing = {id(self.fit.links[i]) for i in rotor_crossing_links(self.spec, self.fit.links)}
        usable = [l for l in self.fit.links if l.a in self.model.parts and l.b in self.model.parts
                  and id(l) not in crossing
                  and not (l.kind == "drive_flat" and not l.rigid)  # rotating-bore outputs turn inside the case
                  and self._faces_for(l) is not None]  # e.g. a screw in a slot has no coaxial face to mate on
        adjacency: dict[str, list[Link]] = {}
        for link in usable:
            adjacency.setdefault(link.a, []).append(link)
            adjacency.setdefault(link.b, []).append(link)
        dist = {root: 0.0}
        via: dict[str, Link] = {}
        queue = [(0.0, root)]
        while queue:
            d, node = heapq.heappop(queue)
            if d > dist.get(node, math.inf):
                continue
            for link in adjacency.get(node, []):
                other = link.b if link.a == node else link.a
                nd = d + cost(link)
                if nd < dist.get(other, math.inf):
                    dist[other], via[other] = nd, link
                    heapq.heappush(queue, (nd, other))
        chosen: list[Link] = list(dict.fromkeys(via.values()))
        for link in usable:
            if (link.kind == "ball_joint" or link.via in joints or link.via in steering) and link not in chosen:
                chosen.append(link)
        return [(link, mate_type(link)) for link in chosen]

    # --------------------------------------------------------------- mating
    def build_mates(self) -> None:
        joints = {j.interface: j for j in self.spec.mechanical.joints if j.interface}
        travel = None
        from .motion import servo_travel

        travel = servo_travel(self.project)
        plan = self.plan_mates()
        created = []
        for index, (link, kind) in enumerate(plan):
            faces = self._faces_for(link)
            if faces is None:
                self.result.add(Diagnostic.error("mate_connector_unresolved", f"no faces to mate {link.a} and {link.b} via {link.via}",
                                                 subjects=[link.a, link.b, link.via]))
                continue
            pa, fa, pb, fb, inference = faces
            if kind == "BALL":
                inference = "CENTER"
            name = f"{link.via}: {pa} / {pb}"[:100]
            a = fx.mate_connector(f"MCa{index}", self.occurrence[pa], fa, inference)
            b = fx.mate_connector(f"MCb{index}", self.occurrence[pb], fb, inference)
            limits = None
            joint = joints.get(link.via)
            if kind == "REVOLUTE" and joint is not None and joint.type == "revolute" and joint.limits:
                limits = (float(joint.limits[0]), float(joint.limits[1]))
            elif kind == "REVOLUTE" and link.kind == "spline":
                limits = travel
            body = fx.mate(name, kind, a, b, limits)
            response = self.api.json("POST", self.base + "/features", body={"btType": "BTFeatureDefinitionCall-1406", "feature": body})
            created.append({"feature_id": response["feature"]["featureId"], "name": name, "kind": kind, "link": link, "faces": faces,
                            "inference": inference, "limits": limits, "joint": joint.id if joint else None, "index": index})
        self.mates = created
        # Correct connector B (and orient A along the joint axis) from the inferred frames.
        definition = self.definition()
        frames = {f["id"]: f["featureData"] for f in definition["rootAssembly"].get("features", [])}
        for mate in created:
            data = frames.get(mate["feature_id"])
            if data is None:
                continue
            ent = data["matedEntities"]
            cs_a, cs_b = dict(ent[0]["mateConnectorCS"]), dict(ent[1]["mateConnectorCS"])
            flip_a = False
            if mate["joint"]:
                joint = next(j for j in self.spec.mechanical.joints if j.id == mate["joint"])
                if float(unit(cs_a["zAxis"]) @ unit(joint.axis)) < 0:
                    flip_a = True
                    cs_a = {**cs_a, "zAxis": (-np.asarray(cs_a["zAxis"])).tolist()}
            flip, theta, translation, off_axis = correction(cs_a, cs_b)
            planar = mate["inference"] == "CENTROID"
            mate["off_axis_mm"] = round(off_axis * 1e3, 4)
            if not planar and off_axis > 2e-5:
                self.result.add(Diagnostic.error("mate_axis_mismatch", f"{mate['name']}: connector axes are {off_axis * 1e3:.3f} mm apart",
                                                 subjects=[mate["link"].a, mate["link"].b], data={"offset": off_axis}))
            if mate["kind"] == "BALL":
                flip, theta = False, 0.0
            if not planar and off_axis > 2e-5:
                translation = np.array([0.0, 0.0, translation[2]])  # do not hide a real misalignment
            # Mates are re-created rather than updated: an update re-ids the
            # connectors without remapping the mate's connector query.
            quoted = urllib.parse.quote(mate["feature_id"], safe="")
            self.api.request("DELETE", self.base + f"/features/featureid/{quoted}")
            pa, fa, pb, fb, inference = mate["faces"]
            a = fx.mate_connector(f"MCa{mate['index']}", self.occurrence[pa], fa, inference, flip=flip_a)
            b = fx.mate_connector(f"MCb{mate['index']}", self.occurrence[pb], fb, inference, flip=flip, rotation=theta,
                                  translation=tuple(float(v) for v in translation))
            body = fx.mate(mate["name"], mate["kind"], a, b, mate["limits"])
            response = self.api.json("POST", self.base + "/features", body={"btType": "BTFeatureDefinitionCall-1406", "feature": body})
            mate["feature_id"] = response["feature"]["featureId"]
            mate["correction"] = {"flip_a": flip_a, "flip_b": flip, "rotation_deg": round(math.degrees(theta), 4),
                                  "translation_mm": [round(float(v) * 1e3, 4) for v in translation]}
        self.restore_pose()

    def restore_pose(self, root: str = "chassis") -> None:
        """Bring every occurrence back to its modeled (Part Studio) pose: undo the
        rigid motion the solver applied while connectors were uncorrected (relative
        transform by the root's inverse; absolute transforms are ignored by the
        API), then zero every revolute/ball mate value (free spins)."""
        root_occ = self.occurrence.get(root) or next(iter(self.occurrence.values()))
        for _ in range(2):
            definition = self.definition()
            transforms = {o["path"][0]: np.asarray(o["transform"], dtype=float).reshape(4, 4)
                          for o in definition["rootAssembly"].get("occurrences", [])}
            inverse = np.linalg.inv(transforms[root_occ])
            if np.abs(inverse - np.eye(4)).max() < POSE_TOL:
                break
            self.api.json("POST", self.base + "/occurrencetransforms",
                          body={"occurrences": [{"path": [occ]} for occ in self.occurrence.values()],
                                "transform": inverse.flatten().tolist(), "isRelative": True})
        values = self.api.json("GET", self.base + "/matevalues").get("mateValues", [])
        updates = []
        for value in values:
            kind = value.get("jsonType")
            owner = value.get("ownerOccurrencePath", [])
            if kind == "Revolute":
                updates.append({"featureId": value["featureId"], "jsonType": "Revolute", "rotationZ": 0.0, "ownerOccurrencePath": owner})
            elif kind == "Ball":
                updates.append({"featureId": value["featureId"], "jsonType": "Ball", "rotationX": 0.0, "rotationY": 0.0, "rotationZ": 0.0,
                                "ownerOccurrencePath": owner})
        if updates:
            self.api.json("POST", self.base + "/matevalues", body={"mateValues": updates})

    # -------------------------------------------------------------- verify
    def verify(self) -> dict[str, Any]:
        definition = self.definition()
        root = definition["rootAssembly"]
        moved = []
        for occurrence in root.get("occurrences", []):
            t = np.asarray(occurrence["transform"], dtype=float).reshape(4, 4)
            if np.abs(t - np.eye(4)).max() > POSE_TOL:
                moved.append(occurrence["path"][0])
        names = {v: k for k, v in self.occurrence.items()}
        if moved:
            self.result.add(Diagnostic.error("assembly_pose_mismatch", f"{len(moved)} occurrences are not at their modeled pose after mating",
                                             subjects=[names.get(m, m) for m in moved]))
        features = {f["id"]: f["featureData"] for f in root.get("features", [])}
        unsatisfied = []
        mates_out = []
        for mate in self.mates:
            data = features.get(mate["feature_id"])
            if data is None:
                continue
            a, b = (_cs(e["matedCS"]) for e in data["matedEntities"])
            gap = float(np.linalg.norm(a[0] - b[0]))
            z_dot = float(a[2] @ b[2])
            ok = gap < 1e-6 and (mate["kind"] == "BALL" or z_dot > 1 - 1e-9)
            if mate["kind"] == "FASTENED":
                ok = ok and float(a[1] @ b[1]) > 1 - 1e-9
            if not ok:
                unsatisfied.append(mate["name"])
            mates_out.append({"name": mate["name"], "type": data.get("mateType"), "feature_id": mate["feature_id"], "via": mate["link"].via,
                              "parts": [mate["link"].a, mate["link"].b], "origin": a[0].tolist(), "axis": a[2].tolist(),
                              "limits": data.get("mateLimits") or None, "joint": mate["joint"], "satisfied": ok})
        if unsatisfied:
            self.result.add(Diagnostic.error("assembly_mate_unsatisfied", f"{len(unsatisfied)} mates do not coincide in the modeled pose",
                                             subjects=unsatisfied[:20], data={"count": len(unsatisfied)}))
        # Every joint has a mate of the right type on its axis with its limits.
        interfaces = {i.id: i for i in self.spec.mechanical.interfaces}
        for joint in self.spec.mechanical.joints:
            if joint.type == "fixed":
                continue
            interface = interfaces.get(joint.interface or "")
            if interface is None or not all(p in self.model.parts for p in interface.parts):
                continue  # partial (scoped) builds
            candidates = [m for m in mates_out if m["joint"] == joint.id]
            if not candidates:
                self.result.add(Diagnostic.error("mate_missing_for_joint", f"joint {joint.id} has no assembly mate", subjects=[joint.id]))
                continue
            m = candidates[0]
            axis = unit(joint.axis)
            angle = math.degrees(math.acos(min(1.0, abs(float(unit(m["axis"]) @ axis)))))
            d = np.asarray(joint.origin) - np.asarray(m["origin"])
            offset = float(np.linalg.norm(d - (d @ axis) * axis))
            problems = []
            if m["type"] != "REVOLUTE":
                problems.append(f"type {m['type']}")
            if angle > 0.2 or offset > 5e-5 or float(unit(m["axis"]) @ axis) < 0:
                problems.append(f"axis off by {angle:.3f} deg / {offset * 1e3:.3f} mm")
            if joint.type == "revolute" and joint.limits:
                limits = m["limits"] or {}
                lo, hi = limits.get("limitAxialZMin"), limits.get("limitAxialZMax")
                if lo is None or abs(lo - joint.limits[0]) > 1e-6 or abs(hi - joint.limits[1]) > 1e-6:
                    problems.append(f"limits {lo}..{hi} != {joint.limits}")
            elif joint.type == "continuous" and m["limits"]:
                problems.append("continuous joint has mate limits")
            m["joint_check"] = problems or "ok"
            if problems:
                self.result.add(Diagnostic.error("mate_joint_mismatch", f"joint {joint.id}: mate {m['name']}: {'; '.join(problems)}",
                                                 subjects=[joint.id], data={"angle_deg": angle, "offset": offset}))
        return {"element_id": self.eid, "occurrences": {k: v for k, v in self.occurrence.items()}, "mates": mates_out,
                "moved_occurrences": len(moved)}

    # ----------------------------------------------------------- posing/views
    def set_mate_value(self, feature_id: str, rotation_z: float) -> None:
        self.api.json("POST", self.base + "/matevalues",
                      body={"mateValues": [{"featureId": feature_id, "jsonType": "Revolute", "rotationZ": rotation_z,
                                            "ownerOccurrencePath": []}]})

    def shaded_view(self, path: Any, view: str | list[float] = "isometric", width: int = 1400, height: int = 900,
                    pixel_size: float = 0.0) -> None:
        import base64

        query = {"viewMatrix": view if isinstance(view, str) else ",".join(f"{v:.6f}" for v in view), "outputHeight": height,
                 "outputWidth": width, "pixelSize": pixel_size, "edges": "show"}
        response = self.api.json("GET", self.base + "/shadedviews", query=query)
        path.write_bytes(base64.b64decode(response["images"][0]))


def build_assembly(api: Transport, doc: Any, project: Project, model: GeometryModel, fit: FitReport, result: StageResult) -> AssemblyBuilder:
    builder = AssemblyBuilder(api, doc.did, doc.wid, doc.part_studio, project, model, fit, result)
    builder.create(f"{project.spec.robot.name} assembly")
    builder.build_mates()
    return builder
