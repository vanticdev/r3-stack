"""Quota-safe edits of pinned standard-feature CAD, with per-studio checkpoints.

No document or unaffected instance is recreated. The acknowledged request journal
covers the gap between a remote write and the local checkpoint. Offline estimates
are conservative: the first live feature read establishes the exact parameter diff.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .client import OnshapeError, Transport
from .dry_run import FeaturePlanner, studio_plan
from .journal import RequestJournal, save_json
from .organized import (AssemblyBuilder, AssemblyPlan, DocumentInfo, MatePlan, StudioRef,
                        _load_state, _save_state, build_part_catalog, input_digest,
                        library_digest, locked_library_document, plan_assembly, robot_variables)
from .parts import LocalPart
from .quota import quota_diagnostic, update_features
from .studio import PartStudioBuilder, StudioBuildError, populate_studio


def _identity(feature: dict[str, Any]) -> tuple[str, str]:
    # Depth belongs to parameters, even when the human-readable label includes it.
    name = re.sub(r" extrude [-+\d.e]+ mm$", " extrude", feature.get("name", ""))
    return feature.get("featureType", ""), name


def _matches(expected: Any, actual: Any) -> bool:
    """Ignore server-added fields/defaults, compare every authored parameter/entity."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            (_matches(v, actual[k]) if k in actual else v is False or v == "" or v == 0)
            for k, v in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        for key in ("parameterId", "entityId"):
            if expected and all(isinstance(v, dict) and key in v for v in expected + actual):
                if key == "entityId" and len(expected) != len(actual):
                    return False
                by_id = {v[key]: v for v in actual}
                return all(v[key] in by_id and _matches(v, by_id[v[key]]) for v in expected)
        if len(expected) != len(actual):
            return False
        return all(_matches(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(expected, actual, rel_tol=1e-10, abs_tol=1e-12)
    return expected == actual


def _check_response(response: dict[str, Any], name: str) -> None:
    states = list((response.get("featureStates") or {}).values()) + [response.get("featureState") or {}]
    if any(s.get("featureStatus") == "ERROR" for s in states):
        raise StudioBuildError(name, "incremental update", "(regeneration error)")


class IncrementalStudioBuilder(PartStudioBuilder):
    """Generate standard definitions against real IDs; skip unchanged features.

    Sketch entities/constraints require updatePartStudioFeature. Only parameter
    changes go to updateFeatures; that endpoint does not update sketch geometry.
    """
    def __init__(self, api: Transport, doc: DocumentInfo, ref: StudioRef, members: list[LocalPart],
                 variables: Any = None, bindings: Any = None) -> None:
        super().__init__(api, doc.did, doc.wid, ref.element_id, ref.name, variables, bindings)
        self.ref = ref
        data = api.json("GET", self.base + "/features", query={"noSketchGeometry": False, "includeGeometryIds": True})
        self.existing = data.get("features", [])
        self.by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
        owned = {fid for ids in ref.folders.values() for fid in ids}
        for feature in self.existing:
            if feature.get("featureId") in owned:
                self.by_identity.setdefault(_identity(feature), []).append(feature)
        self.pending: list[dict[str, Any]] = []
        self.used: set[str] = set()
        self.writes = {"updates": 0, "creates": 0, "deletes": 0}
        planner = FeaturePlanner(ref.name, variables, bindings)
        populate_studio(planner, members)
        self.desired = [_identity(d["feature"]) for d in planner.definitions]
        # Only remove obsolete owned definitions. Unknown/user-authored features survive.
        remaining = Counter(self.desired)
        obsolete = []
        for feature in self.existing:
            if feature.get("featureId") not in owned:
                continue
            identity = _identity(feature)
            if remaining[identity]:
                remaining[identity] -= 1
            else:
                obsolete.append(feature)
        for feature in reversed(obsolete):
            api.request("DELETE", self.base + "/features/featureid/" + quote(feature["featureId"], safe=""))
            self.existing.remove(feature)
            self.by_identity[_identity(feature)].remove(feature)
            self.writes["deletes"] += 1
        self.position = 0

    def flush(self) -> None:
        if self.pending:
            _check_response(update_features(self.api, self.base, self.pending) or {}, self.out.name)
            self.writes["updates"] += len(self.pending)
            self.pending = []

    def _add(self, feature: dict[str, Any], part: str, folder: str) -> str:
        candidates = self.by_identity.get(_identity(feature), [])
        old = next((f for f in candidates if f["featureId"] not in self.used), None)
        if old is not None:
            fid = old["featureId"]
            # Names with an embedded dimension retain their existing meaningful label;
            # batch updates cannot rename, and a rename is not a geometry change.
            fields = {k: feature[k] for k in ("parameters", "entities", "constraints") if k in feature}
            if not _matches(fields, old):
                update = {"btType": feature["btType"], "featureId": fid, **fields}
                if feature["featureType"] == "newSketch":
                    self.flush()
                    response = self.api.json("POST", self.base + "/features/featureid/" + quote(fid, safe=""),
                                             body={"btType": "BTFeatureDefinitionCall-1406", "feature": update})
                    _check_response(response, part)
                    self.writes["updates"] += 1
                else:
                    self.pending.append(update)
        else:
            self.flush()
            # Insert before the next retained feature, never after the mate connectors.
            remaining = set(self.desired[self.position + 1:])
            index = next((i for i, f in enumerate(self.existing)
                          if f.get("featureId") not in self.used and _identity(f) in remaining), len(self.existing))
            if index < len(self.existing):
                self.api.json("POST", self.base + "/features/rollback", body=str(index))
            response = self.api.json("POST", self.base + "/features",
                                     body={"btType": "BTFeatureDefinitionCall-1406", "feature": feature})
            _check_response(response, part)
            fid = response["feature"]["featureId"]
            self.existing.insert(index, {**feature, "featureId": fid})
            if index < len(self.existing) - 1:
                self.api.json("POST", self.base + "/features/rollback", body="-1")
            self.writes["creates"] += 1
        self.position += 1
        self.used.add(fid)
        self.out.folders.setdefault(folder, []).append(fid)
        self.out.features += 1
        return fid

    def _parts(self) -> dict[str, str]:
        self.flush()
        return super()._parts()

    def _start_face(self, part_id: str, normal: np.ndarray, offset: float) -> str:
        self.flush()
        return super()._start_face(part_id, normal, offset)

    def start_part(self, part: LocalPart) -> str:
        # Re-key a single renamed catalog variant without replacing its solid.
        pid = self.ref.part_ids.get(part.key)
        if pid is None and len(self.ref.part_ids) == 1:
            pid = next(iter(self.ref.part_ids.values()))
        if pid is None:
            raise ValueError(f"{part.key}: no unambiguous existing body; use a new Part Studio")
        before = set(self._parts())
        self.out.part_ids[part.key] = pid
        first = part.plan.ops[0]
        from .geometry import Prism
        if isinstance(first, Prism):
            self._prism(part, first, "new", None, 0)
        else:
            self._revolve(part, first, "new", None, 0)
        current = self._parts()
        if pid not in current:
            added = set(current) - before
            if len(added) != 1:
                raise ValueError(f"{part.key}: changed base feature did not resolve exactly one new body")
            pid = added.pop()
            self.out.part_ids[part.key] = pid
        self.apply(part, "Base shape", skip_first=True)
        return pid

    def properties(self, part: LocalPart, metadata_ids: dict[str, str]) -> None:
        self.flush()
        super().properties(part, metadata_ids)

    def finish(self) -> StudioRef:
        self.flush()
        # Preserve user-authored/unknown feature membership in every folder.
        old_ids = {f["featureId"] for f in self.existing}
        for folder, ids in self.ref.folders.items():
            self.out.folders.setdefault(folder, []).extend(fid for fid in ids if fid in old_ids and fid not in self.used)
        return StudioRef(self.out.name, self.out.element_id, self.out.part_ids, self.out.connectors, self.out.folders)


def _state(project: Project) -> dict[str, Any]:
    path = project.build_dir("cad") / "build_state.json"
    if not path.exists():
        raise ValueError("incremental rebuild needs build/cad/build_state.json; restore the pinned build or use --full")
    state = json.loads(path.read_text())
    lock, robot = project.lock.get("cad") or {}, state["robot"]
    if (robot["did"], robot["wid"]) != (lock.get("document_id"), lock.get("workspace_id")):
        raise ValueError("build_state document/workspace does not match the CAD lock; refusing writes")
    return state


def _work(project: Project, parts: dict[str, LocalPart], state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    from .aliases import reusable_alias_parts
    pending = ((project.read_build_json("cad/cad_model.json") or {}).get("source", {}).get("recomposed", {}).get("pending_rebuild") or {})
    reused = reusable_alias_parts(project, parts)
    robot_refs, library_refs = state["robot"]["studios"], (project.lock.get("cad_library") or {}).get("studios", {})
    known = {key for ref in [*robot_refs.values(), *library_refs.values()] for key in ref["part_ids"]}
    wanted = (set(pending) & set(parts)) | (set(parts) - known - reused)
    signatures = (project.lock.get("cad_incremental") or {}).get("part_signatures", {})
    if signatures:
        from .aliases import part_signature
        wanted.update(key for key, part in parts.items() if signatures.get(key) != part_signature(part))
    if not pending and not signatures:
        # Legacy builds have recorded plan solids, not full feature signatures.
        from .recompose import PLAN_FIXTURE, same_solid
        path = project.build_dir("cad") / PLAN_FIXTURE
        if not path.exists():
            raise ValueError("incremental rebuild needs recorded plans or a pending_rebuild list; use --full")
        with gzip.open(path, "rt") as handle:
            recorded = json.load(handle)["parts"]
        wanted.update(key for key, part in parts.items() if key not in reused and
                      (key not in recorded or not same_solid(part.plan, recorded[key])[0]))
    work = []
    grouped: dict[tuple[str, str, str | None], list[str]] = {}
    for key in sorted(wanted - reused):
        part = parts[key]
        refs = library_refs if part.library else robot_refs
        old_name = next((name for name, ref in refs.items() if key in ref["part_ids"]), None)
        if old_name is None and part.studio in refs:
            old = refs[part.studio]
            if len(old["part_ids"]) == 1 and not (set(old["part_ids"]) & set(parts)):
                old_name = part.studio
        name = old_name or part.studio
        if old_name is None and name in refs:
            name += " additions " + hashlib.sha256(key.encode()).hexdigest()[:6]
        grouped.setdefault(("library" if part.library else "robot", name, old_name), []).append(key)
    for (doc, name, old_name), keys in grouped.items():
        refs = library_refs if doc == "library" else robot_refs
        ref = refs.get(old_name) if old_name else None
        members = list(keys)
        if ref:
            members = sorted(set(keys) | (set(ref["part_ids"]) & set(parts)))
            if any(parts[key].mirror_of for key in members):
                raise ValueError(f"{name}: mirrored family topology needs --full")
        if any(parts[key].bank_model for key in members):
            raise ValueError(f"{name}: vendor import replacement needs --full")
        work.append({"document": doc, "name": name, "parts": members, "pending": keys,
                     "existing": copy.deepcopy(ref), "action": "update" if ref else "create"})
    return work, sorted(reused)


def _mate_key(mate: MatePlan | dict[str, Any]) -> tuple[str, str]:
    data = asdict(mate) if isinstance(mate, MatePlan) else mate
    # One join can generate multiple fastener mates; endpoints disambiguate them.
    return data["level"], json.dumps([data["join"], data["a"], data["b"]])


def _mate_changes(plan: AssemblyPlan, state: dict[str, Any], replaced: set[str]) -> tuple[list[tuple[str, str]], list[MatePlan]]:
    old = {_mate_key(m): (level, fid, m) for level, rows in state["mate_ids"].items() for fid, m in rows}
    new = {_mate_key(m): m for m in plan.mates}
    deletes, creates = [], []
    for key, (level, fid, data) in old.items():
        mate = new.get(key)
        changed = mate is None or json.loads(json.dumps(asdict(mate))) != data
        if changed or data["a"][0] in replaced or data["b"][0] in replaced:
            deletes.append((level, fid))
            if mate is not None:
                creates.append(mate)
    creates.extend(m for key, m in new.items() if key not in old)
    return deletes, creates


def _replacement_iids(work: list[dict[str, Any]], plan: AssemblyPlan, state: dict[str, Any],
                      robot: DocumentInfo | None = None, library: DocumentInfo | None = None) -> set[str]:
    # New sources and edited version-pinned library sources need new instances.
    keys = {key for item in work if item["action"] == "create" or item["document"] == "library" for key in item["pending"]}
    if robot is not None and library is not None:
        for item in work:
            ref = item["existing"]
            if not ref:
                continue
            doc = library if item["document"] == "library" else robot
            for key in item["parts"]:
                old_pid = ref["part_ids"].get(key)
                if old_pid is None and len(ref["part_ids"]) == 1:
                    old_pid = next(iter(ref["part_ids"].values()))
                eid, pid, _ = doc.part_ref(key)
                if (eid, pid) != (ref["element_id"], old_pid):
                    keys.add(key)
    old_iids = {iid for ids in state["instance_ids"].values() for iid in ids}
    return {iid for iid, p in plan.placements.items() if p.key in keys or iid not in old_iids}


def incremental_plan(project: Project) -> dict[str, Any]:
    parts, instances = build_part_catalog(project)
    state = _state(project)
    work, reused = _work(project, parts, state)
    plan = plan_assembly(project, parts, instances)
    variables, bindings = robot_variables(project)
    replaced = _replacement_iids(work, plan, state)
    deletes, mates = _mate_changes(plan, state, replaced)
    studios = []
    # Common verification reads all pinned studios (including retired variants),
    # plus each assembly twice. Only edited studios need a pre-write diff read.
    new_studios = sum(i["action"] == "create" for i in work)
    reads = (len(state["robot"]["studios"]) + len((project.lock.get("cad_library") or {}).get("studios", {}))
             + new_studios + len(plan.levels) * 2 + sum(i["action"] == "update" for i in work))
    calls = reads + len(deletes) + len(mates)
    candidates = 0
    for item in work:
        members = [parts[key] for key in item["parts"]]
        detail = studio_plan(item["name"], members, variables if item["document"] == "robot" else None,
                             bindings.get(parts[members[0].key].studio) if item["document"] == "robot" else None)
        count = detail["feature_writes"]
        old_count = sum(len(ids) for ids in (item["existing"] or {}).get("folders", {}).values())
        # Without a /features read, use all candidate sketch updates plus one
        # parameter batch per solid as a conservative estimate (flushes may split it).
        estimate = count if item["action"] == "create" else count + 2 * max(0, count - old_count) + max(0, old_count - count)
        calls += estimate
        candidates += count
        studios.append({**{k: v for k, v in item.items() if k != "existing"}, **detail,
                        "element_id": (item["existing"] or {}).get("element_id"),
                        "estimated_feature_calls": estimate})
    checkpoint = project.lock.get("cad_incremental_pending") or {}
    return {"mode": "incremental", "dry_run": True, "part_studios": studios,
            "reused_parts": reused, "instances_to_insert_or_replace": sorted(replaced),
            "assembly_features": [asdict(m) for m in mates], "mates_to_delete": deletes,
            "summary": {"parts": sum(len(i["pending"]) for i in work), "part_studios": len(studios),
                        "part_studio_feature_writes": candidates, "feature_candidates": candidates, "assembly_feature_writes": len(mates),
                        "assembly_feature_deletes": len(deletes), "features": candidates + len(mates),
                        "rest_feature_reads": reads, "estimated_features_calls": calls},
            "checkpoint": checkpoint,
            "notes": ["Conservative estimate before feature diff; unchanged features are skipped and solid parameter edits batch.",
                      "Sketch geometry uses individual standard feature updates; it cannot use the parameter-only batch endpoint.",
                      "Existing folder membership and connector IDs survive edits; new folder membership needs the native organizer.",
                      "Estimate covers headless REST only; native folder-organization tree reads require a separate organize --dry-run.",
                      "No transport discovery or live /features calls occur in this dry-run."]}


def _document(data: dict[str, Any]) -> DocumentInfo:
    doc = DocumentInfo(**{k: v for k, v in data.items() if k not in ("studios", "references")})
    doc.studios = {n: StudioRef(**ref) for n, ref in data.get("studios", {}).items()}
    doc.references = [_document(ref) for ref in data.get("references", [])]
    return doc


def _edit_studio(api: Transport, doc: DocumentInfo, item: dict[str, Any], parts: dict[str, LocalPart],
                 variables: Any, bindings: Any) -> tuple[StudioRef, dict[str, int]]:
    members = [parts[key] for key in item["parts"]]
    if item["existing"]:
        ref = StudioRef(name=item["name"], **{k: v for k, v in item["existing"].items() if k != "name"})
        builder = IncrementalStudioBuilder(api, doc, ref, members, variables, bindings)
        populate_studio(builder, members)
        return builder.finish(), builder.writes
    from .studio import build_studio
    eid = api.json("POST", f"/partstudios/d/{doc.did}/w/{doc.wid}", body={"name": item["name"]})["id"]
    if doc.variable_studio:
        api.request("POST", f"/variables/d/{doc.did}/w/{doc.wid}/e/{eid}/variablestudioreferences",
                    body={"references": [{"referenceElementId": doc.variable_studio, "entireVariableStudio": True}]})
    built = build_studio(api, doc.did, doc.wid, eid, item["name"], members, variables, bindings)
    return StudioRef(item["name"], eid, built.part_ids, built.connectors, built.folders), {"creates": built.features}


def _edit_assembly(api: Transport, builder: AssemblyBuilder, state: dict[str, Any], replaced: set[str]) -> None:
    deletes, creates = _mate_changes(builder.plan, state, replaced)
    old_iids = {iid for ids in builder.instance_ids.values() for iid in ids}
    removed = old_iids - set(builder.plan.placements)
    for level, fid in deletes:
        api.request("DELETE", builder.base(level) + "/features/featureid/" + quote(fid, safe=""))
        builder.mate_ids[level] = [(f, m) for f, m in builder.mate_ids[level] if f != fid]
    for level, ids in builder.instance_ids.items():
        drop = [iid for iid in ids if iid in replaced or iid in removed]
        if drop:
            api.json("POST", builder.base(level) + "/modify", body={"deleteInstances": [ids[iid] for iid in drop],
                                                                    "editDescription": "R3 incremental replacement"})
        for iid in drop:
            del ids[iid]
    for level in builder.plan.levels:
        members = sorted(iid for iid in replaced if builder.plan.placements[iid].level == level)
        # _insert_parts resolves an entire source pool; preserve existing IDs and
        # assign only the set difference after insertion to avoid stealing a sibling.
        for iid in members:
            p = builder.plan.placements[iid]
            doc, eid, pid, vid = builder._source(p.key)
            body = {"documentId": doc.did, "elementId": eid, "partId": pid, "isAssembly": False, "isWholePartStudio": False}
            if vid:
                body["versionId"] = vid
            before = {i["id"] for i in api.json("GET", builder.base(level))["rootAssembly"]["instances"]}
            api.json("POST", builder.base(level) + "/instances", body=body)
            after = api.json("GET", builder.base(level))["rootAssembly"]["instances"]
            added = [i["id"] for i in after if i["id"] not in before and i.get("elementId") == eid and i.get("partId") == pid]
            if len(added) != 1:
                raise ValueError(f"{iid}: insertion did not resolve exactly one instance")
            builder.instance_ids.setdefault(level, {})[iid] = added[0]
            builder._place(level, [added[0]], p.pose)
    for mate in creates:
        builder.mate_ids.setdefault(mate.level, []).append((builder._mate(mate.level, mate), mate))


def sync_incremental(project: Project, api: Transport, result: StageResult) -> dict[str, Any]:
    """Edit a pinned document. Never trash it, including after quota/validation failures."""
    from .organized import verify_and_import
    started = time.time()
    parts, instances = build_part_catalog(project)
    plan = plan_assembly(project, parts, instances)
    variables, bindings = robot_variables(project)
    digest = input_digest(project)
    root = project.build_dir("cad")
    path = root / "incremental_state.json"
    pending = project.lock.get("cad_incremental_pending") or {}
    if pending and pending.get("digest") != digest:
        raise ValueError("unfinished incremental rebuild inputs changed; restore its inputs before resuming")
    if pending:
        _state(project)  # validate the active lock even while resuming
        cp = json.loads(path.read_text())
        if cp["digest"] != digest:
            raise ValueError("incremental checkpoint digest does not match the lock")
        if cp["robot"]["did"] != project.lock["cad"]["document_id"]:
            raise ValueError("incremental checkpoint document does not match the lock")
    else:
        state = _state(project)
        work, _ = _work(project, parts, state)
        if set(plan.levels) != set(state["robot"]["assemblies"]):
            raise ValueError("subassembly topology changed; use --full")
        if any(iid in state["instance_ids"].get(level, {}) and plan.placements[iid].level != level
               for level, ids in state["instance_ids"].items() for iid in ids if iid in plan.placements):
            raise ValueError("instance moved between subassemblies; use --full")
        cp = {"digest": digest, "attempt": uuid.uuid4().hex[:12], "base": state, "work": work, "done": [], "writes": {},
              "robot": {**state["robot"], "digest": "", "vid": ""},
              "library": asdict(locked_library_document(project)), "assembly_complete": False}
        save_json(path, cp)
        project.write_lock("cad_incremental_pending", {"digest": digest, "document_id": state["robot"]["did"]})
    prefix = f"incremental-{digest}-{cp['attempt']}"
    robot, library = _document(cp["robot"]), _document(cp["library"])
    report: dict[str, Any] = {"mode": "incremental", "parts_planned": len(parts), "instances": len(instances),
                              "mates_planned": len(plan.mates)}
    try:
        if not cp.get("variables_complete"):
            journal = RequestJournal(api, root / f"{prefix}-variables.journal.json", digest)
            if robot.variable_studio:
                base = f"/variables/d/{robot.did}/w/{robot.wid}/e/{robot.variable_studio}"
                tables = journal.json("GET", base + "/variables")
                existing = {v["name"]: v for table in tables for v in table.get("variables", [])}
                changed = [{"name": v.name, "type": "LENGTH", "expression": v.expression, "description": v.description}
                           for v in variables.values() if existing.get(v.name, {}).get("expression") != v.expression]
                if changed:
                    journal.json("POST", base + "/variables", body=changed)
            cp["variables_complete"] = True
            save_json(path, cp)
        for index, item in enumerate(cp["work"]):
            if index in cp["done"]:
                continue
            doc = library if item["document"] == "library" else robot
            journal = RequestJournal(api, root / f"{prefix}-studio-{index}.journal.json", digest)
            ref, writes = _edit_studio(journal, doc, item, parts, variables if doc is robot else None,
                                      bindings.get(parts[item["parts"][0]].studio) if doc is robot else None)
            doc.studios[item["name"]] = ref
            cp["done"].append(index)
            cp["writes"][item["name"]] = writes
            cp["robot"], cp["library"] = asdict(robot), asdict(library)
            save_json(path, cp)
        library_changed = any(i["document"] == "library" for i in cp["work"])
        if library_changed and not cp.get("library_version_complete"):
            journal = RequestJournal(api, root / f"{prefix}-version.journal.json", digest)
            library.vid = journal.json("POST", f"/documents/d/{library.did}/versions", body={
                "documentId": library.did, "workspaceId": library.wid, "name": f"R3 incremental {digest}"})["id"]
            library.digest = library_digest(parts)
            cp["library"], cp["library_version_complete"] = asdict(library), True
            save_json(path, cp)
        builder = AssemblyBuilder(api, project, robot, library, parts, plan, result)
        # _load_state supplies typed mates and IDs, but not the edited studio refs.
        state_path = root / f"{prefix}-assembly-input.json"
        if not state_path.exists():
            save_json(state_path, {**cp["base"], "robot": {**cp["base"]["robot"],
                       "studios": {n: asdict(s) for n, s in robot.studios.items()}}})
        _load_state(state_path, robot, builder)
        output = root / f"{prefix}-assembly-output.json"
        if cp["assembly_complete"]:
            _load_state(output, robot, builder)
        else:
            journal = RequestJournal(api, root / f"{prefix}-assembly.journal.json", digest)
            builder.api = journal
            replaced = _replacement_iids(cp["work"], plan, cp["base"], robot, library)
            _edit_assembly(journal, builder, cp["base"], replaced)
            _save_state(output, robot, builder)
            cp["assembly_complete"] = True
            save_json(path, cp)
        result.metrics["onshape_document"] = robot.url()
        report["feature_writes"] = cp["writes"]
        report["changed_keys"] = [key for i in cp["work"] for key in i["pending"]]
        folder_work = [i["name"] for i in cp["work"] if i["action"] == "create" or
                       any(cp["writes"].get(i["name"], {}).get(k, 0) for k in ("creates", "deletes"))]
        if not cp.get("organization_complete") and (folder_work or _mate_changes(plan, cp["base"],
                _replacement_iids(cp["work"], plan, cp["base"], robot, library)) != ([], [])):
            result.add(Diagnostic.warning("cad_incremental_organization_pending",
                "Existing folders survive edits; new features/tabs/mates require the native organizer for folder membership.",
                subjects=folder_work, data={"studios": len(folder_work)}))
        # The common path checks native solved poses, feature states, mass and exact fit.
        report = verify_and_import(project, api, result, robot, library, builder, parts, plan, bindings,
                                   report, started, incremental=True)
        return report
    except OnshapeError as error:
        if error.status != 429:
            raise
        quota_diagnostic(error, result)
        return report
    except StudioBuildError:
        raise
    except (RuntimeError, KeyError, TypeError, AttributeError) as error:
        result.add(Diagnostic.error("onshape_incremental_failed", f"{type(error).__name__}: {error}", subjects=["cad"]))
        return report


def finish_incremental(project: Project, report: dict[str, Any]) -> None:
    """Publish new pins only after the common verification and model write succeed."""
    if report.get("mode") != "incremental":
        return
    from .aliases import geometry_aliases, part_signature
    parts, _ = build_part_catalog(project)
    root = project.build_dir("cad")
    cp = json.loads((root / "incremental_state.json").read_text())
    library = report["library_doc"]
    project.write_lock("cad_library", {**project.lock.get("cad_library", {}), "document_id": library.did,
        "workspace_id": library.wid, "version_id": library.vid, "digest": library.digest,
        "studios": {n: {k: v for k, v in asdict(s).items() if k != "name"} for n, s in library.studios.items()},
        "organized": cp.get("organization_complete", False) if any(i["document"] == "library" for i in cp["work"])
                     else project.lock.get("cad_library", {}).get("organized", False),
        "aliases": geometry_aliases(project), "part_signatures": {k: part_signature(p) for k, p in parts.items() if p.library}})
    save_json(root / "build_state.json", json.loads((root / f"incremental-{cp['digest']}-{cp['attempt']}-assembly-output.json").read_text()))
    project.write_lock("cad_incremental", {"digest": cp["digest"], "organization_pending": [] if cp.get("organization_complete") else [i["name"] for i in cp["work"]], "part_signatures": {k: part_signature(p) for k, p in parts.items()}})
    project.write_lock("cad_incremental_pending", {})


def verify_plan_properties(parts: dict[str, LocalPart], keys: set[str], local: dict[str, Any],
                           masses: dict[str, Any], result: StageResult) -> dict[str, Any]:
    """Compare native readback with local standard-operation plans in SI units.

    Mesh-backed plans approximate curved boundaries. Allow 20 um at a bound and
    1% in volume/mass; these limits do not replace the exact interface fit check.
    """
    from .geometry import plan_manifold
    report = {}
    for key in sorted(keys):
        part = parts[key]
        if part.bank_model:
            continue
        geometry, props = local.get(key), masses.get(key) or {}
        if geometry is None or not props:
            result.add(Diagnostic.error("cad_incremental_readback_missing", f"{key}: missing native geometry or mass", subjects=[key]))
            continue
        solid = plan_manifold(part.plan)
        bounds = np.asarray(solid.bounding_box())
        measured = np.r_[geometry.lo, geometry.hi]
        bound_error = float(np.max(np.abs(bounds - measured)))
        volume = float(props.get("volume", [0])[0])
        mass = float(props.get("mass", [0])[0])
        expected_volume = float(solid.volume())
        expected_mass = part.plan.mass if part.plan.mass is not None else expected_volume * part.plan.density
        data = {"bbox_error": bound_error, "bbox_tolerance": 2e-5, "volume": volume,
                "plan_volume": expected_volume, "mass": mass, "plan_mass": expected_mass,
                "relative_tolerance": .01}
        report[key] = data
        if (bound_error > 2e-5 or abs(volume - expected_volume) > max(1e-12, .01 * expected_volume)
                or abs(mass - expected_mass) > max(1e-6, .01 * expected_mass)):
            result.add(Diagnostic.error("cad_incremental_plan_mismatch", f"{key}: native bounds, volume or mass differ from the local plan",
                                        subjects=[key], data=data))
    return report


def pending_organization_state(project: Project) -> tuple[Path, DocumentInfo, dict[str, Any]]:
    """Let the explicit native organizer finish unpublished incremental construction."""
    root = project.build_dir("cad")
    cp = json.loads((root / "incremental_state.json").read_text())
    if cp["digest"] != (project.lock.get("cad_incremental_pending") or {}).get("digest"):
        raise ValueError("incremental organization checkpoint does not match the lock")
    if not cp["assembly_complete"]:
        raise ValueError("finish incremental construction before organizing its checkpoint")
    return (root / f"incremental-{cp['digest']}-{cp['attempt']}-assembly-output.json",
            _document(cp["library"]), cp)
