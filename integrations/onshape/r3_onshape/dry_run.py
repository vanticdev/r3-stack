"""Offline write plans built by the same standard-feature builder as a real rebuild.

No transport discovery, authentication, browser, or live /features reads are needed.
Onshape only batches parameter/suppression updates to existing features; creation
and renaming are single-feature calls. Hole points and visibility changes are
already grouped into one sketch/hole feature and one visibility batch per studio.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from r3_model.project import Project

from .parts import LocalPart, build_part_catalog
from .studio import PartStudioBuilder, populate_studio


class _OfflineMetadata:
    def json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"properties": []}


class FeaturePlanner(PartStudioBuilder):
    def __init__(self, name: str, variables: Any = None, bindings: Any = None) -> None:
        super().__init__(_OfflineMetadata(), "planned", "planned", "planned", name, variables, bindings)
        self.writes: list[dict[str, Any]] = []
        self.definitions: list[dict[str, Any]] = []

    def _add(self, feature: dict[str, Any], part: str, folder: str) -> str:
        fid = f"planned-{len(self.writes)}"
        self.definitions.append({"id": fid, "folder": folder, "part": part, "feature": feature})
        self.writes.append({"part": part, "feature": feature["name"], "type": feature["featureType"], "folder": folder})
        self.out.features += 1
        self.out.folders.setdefault(folder, []).append(fid)
        return fid

    def _parts(self) -> dict[str, str]:
        return dict(self.out.part_ids)

    def _new_part(self, before: set[str], label: str) -> str:
        return f"planned-part-{len(self.out.part_ids)}"

    def _start_face(self, part_id: str, normal: np.ndarray, offset: float) -> str:
        return "planned-face"

    def properties(self, part: LocalPart, metadata_ids: dict[str, str]) -> None:
        pass


def studio_plan(name: str, members: list[LocalPart], variables: Any = None, bindings: Any = None) -> dict[str, Any]:
    from .purchased import public_source

    native = [p for p in members if not p.bank_model and not public_source(p)]
    builder = FeaturePlanner(name, variables, bindings)
    if native:
        populate_studio(builder, native)
    writes = list(builder.writes)
    imports = []
    for part in members:
        if public_source(part):
            continue
        if part.bank_model:
            imports.append(part.key)
            placement = part.bank_model.get("placement") or {}
            # import_studio accepts a reviewed translation; rotations currently fail closed.
            from r3_connectors.frames import Frame
            transform = Frame.from_placement(type("Placement", (), placement)()).matrix() if placement else np.eye(4)
            if not np.allclose(transform, np.eye(4)):
                writes.append({"part": part.name, "feature": "Align vendor model to component frame",
                               "type": "transform", "folder": "Reference geometry"})
            writes.extend({"part": part.name, "feature": c.name, "type": "mateConnector", "folder": "Mate connectors"}
                          for c in part.connectors)
    return {"name": name, "parts": [p.key for p in members], "features": writes, "imports": imports,
            "feature_writes": len(writes), "estimated_features_calls": len(writes)}


def rebuild_plan(project: Project, full: bool = False) -> dict[str, Any]:
    import os
    if not full and os.environ.get("R3_CAD_FULL") != "1" and (project.lock.get("cad") or {}).get("document_id"):
        from .incremental import incremental_plan
        return incremental_plan(project)
    return full_rebuild_plan(project)


def full_rebuild_plan(project: Project) -> dict[str, Any]:
    from .aliases import geometry_aliases, reusable_alias_parts
    from .organized import library_digest, plan_assembly, robot_variables
    from .purchased import public_source

    parts, instances = build_part_catalog(project)
    assembly = plan_assembly(project, parts, instances)
    variables, bindings = robot_variables(project)
    library_reused = (project.lock.get("cad_library") or {}).get("digest") == library_digest(parts)
    alias_reused = reusable_alias_parts(project, parts)
    groups: dict[tuple[str, str], list[LocalPart]] = {}
    reused = []
    for part in parts.values():
        if public_source(part) or part.key in alias_reused or (part.library and library_reused):
            reused.append(part.key)
            continue
        # Imported models have their own Part Studios, even when their original family was shared.
        group = ("library" if part.library else "robot", part.studio)
        groups.setdefault(group, []).append(part)
    studios = [{"document": doc, **studio_plan(name, members, variables if doc == "robot" else None,
                                             bindings.get(name) if doc == "robot" else None)}
               for (doc, name), members in sorted(groups.items())]
    mates = [{"assembly": mate.level, "feature": mate.name[:100], "type": "mate"} for mate in assembly.mates]
    part_writes = sum(s["feature_writes"] for s in studios)
    # organization_check reads one feature list per robot/library Part Studio and assembly;
    # AssemblyBuilder.verify reads one feature list per assembly before that check.
    existing_library = (project.lock.get("cad_library") or {}).get("studios") or {}
    checks = len(studios) + (len(existing_library) if library_reused else 0) + 2 * len(assembly.levels)
    out = {"mode": "rebuild", "dry_run": True, "library_reused": library_reused,
           "aliases": geometry_aliases(project), "reused_parts": sorted(reused), "part_studios": studios,
           "assembly_features": mates,
           "summary": {"parts": sum(len(s["parts"]) for s in studios), "part_studios": len(studios),
                       "part_studio_feature_writes": part_writes, "assembly_feature_writes": len(mates),
                       "features": part_writes + len(mates), "rest_feature_reads": checks,
                       "estimated_features_calls": part_writes + len(mates) + checks},
           "notes": ["Feature creation cannot batch in the official REST API; existing parameter/suppression updates can.",
                     "Hole points share a sketch/Hole feature when their drill parameters and plane match.",
                     "Estimates include REST construction and verification, exclude optional browser organization reads.",
                     "A fresh robot document is rebuilt and re-mated; pending_rebuild is not an incremental geometry-only command."]}
    model = project.read_build_json("cad/cad_model.json") or {}
    out["pending_rebuild"] = model.get("source", {}).get("recomposed", {}).get("pending_rebuild", {})
    _rebuild_progress(project, out)
    return out


def organize_plan(project: Project) -> dict[str, Any]:
    """Exact pinned studio IDs/features from build_state and the immutable library lock."""
    from .organized import locked_library_document

    state_path = project.build_dir("cad") / "build_state.json"
    if not state_path.exists():
        raise ValueError(f"{state_path}: pinned construction state is required to plan organization")
    checkpoint = None
    if project.lock.get("cad_incremental_pending"):
        from .incremental import pending_organization_state
        state_path, library, checkpoint = pending_organization_state(project)
    else:
        library = locked_library_document(project)
    robot = json.loads(state_path.read_text())["robot"]
    documents = [(robot["did"], robot["name"], robot["studios"])]
    if not (project.lock.get("cad_library") or {}).get("organized") or checkpoint and any(i["document"] == "library" for i in checkpoint["work"]):
        from dataclasses import asdict
        documents.append((library.did, library.name, {n: asdict(s) for n, s in library.studios.items()}))
    studios = []
    tree_reads = 0
    for did, docname, refs in documents:
        for name, ref in refs.items():
            folders = [{"name": folder, "feature_ids": ids} for folder, ids in ref["folders"].items()
                       if ids and folder in PartStudioBuilder.PASSES + ("Mirror", "Mate connectors")]
            hidden = list(ref["folders"].get("Mate connectors") or []) + ["Top", "Front", "Right", "Origin"]
            studios.append({"document_id": did, "document": docname, "name": name, "element_id": ref["element_id"],
                            "feature_folders": folders, "hide_feature_ids": hidden})
            tree_reads += 2 * len(folders)  # pre-write and verification trees in the editor adapter
    checks = len(robot["studios"]) + len(library.studios) + len(robot["assemblies"])
    state = json.loads(state_path.read_text())
    feature_count = sum(sum(len(f["feature_ids"]) for f in s["feature_folders"]) for s in studios)
    return {"mode": "organize", "dry_run": True, "part_studios": studios,
            "tab_folders": ["01 Variables", "02 Fabricated parts", "04 Subassemblies", "05 Top-level assembly"],
            "assembly_folders": {level: {"element_id": eid, "instances": state["instance_ids"].get(level, {}),
                                          "sub_instances": state["sub_instances"] if level == "Top-level assembly" else {},
                                          "mates": [fid for fid, _ in state["mate_ids"].get(level, [])]}
                                 for level, eid in robot["assemblies"].items()},
            "summary": {"part_studios": len(studios), "features_to_organize": feature_count, "feature_writes": 0,
                        "rest_feature_reads": checks, "estimated_editor_feature_tree_reads": tree_reads + len(studios),
                        "estimated_features_calls": checks + tree_reads + len(studios)},
            "notes": ["Organization changes folders/visibility, not solid features.",
                      "Editor tree reads are an estimate (two per folder plus one per studio for verification).",
                      "Completed checkpoints and already organized editor folders are skipped during resume."]}


def negative_plan(project: Project) -> dict[str, Any]:
    parts, _ = build_part_catalog(project)
    post = next(p for p in parts.values() if p.name == "Servo post")
    studio = studio_plan(post.studio, [post])
    mutations = [{"feature": "Extrude 1", "type": "rename", "part": post.key},
                 {"feature": "loose line sketch", "type": "newSketch", "part": post.key}]
    writes = studio["feature_writes"] + len(mutations)
    # explicit feature GET + organization_check on post and the deliberately loose studio
    return {"mode": "negative_organized", "dry_run": True,
            "part_studios": [studio, {"name": "Loose Part Studio", "parts": [], "features": [], "feature_writes": 0}],
            "mutations": mutations,
            "summary": {"parts": 1, "part_studios": 2, "features": writes,
                        "rest_feature_reads": 3, "estimated_features_calls": writes + 3},
            "notes": ["Scratch document only; no current robot/library feature writes.",
                      "Rename and creation cannot batch; parameter/suppression updates use features/updates."]}


def _completed_features(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    state = json.loads(path.read_text())
    completed = []
    for call in state["calls"]:
        request = call.get("request") or []
        if len(request) >= 4 and request[0] == "POST" and request[1].endswith("/features"):
            feature = (request[3] or {}).get("feature") or {}
            completed.append({"path": request[1], "feature": feature.get("name"), "type": feature.get("featureType")})
    return completed


def _rebuild_progress(project: Project, out: dict[str, Any]) -> None:
    completed = []
    for key, prefix in (("cad_library_build", "library"), ("cad_build_pending", "robot")):
        pending = project.lock.get(key) or {}
        if pending.get("document_id"):
            completed.extend(_completed_features(project.build_dir("cad") / f"{prefix}-{pending['document_id']}.journal.json"))
    out["completed_feature_requests"] = completed
    count = len(completed)
    remaining = max(0, out["summary"]["features"] - count)
    out["summary"]["completed_feature_writes"] = count
    out["summary"]["remaining_feature_writes"] = remaining
    out["summary"]["estimated_features_calls"] = remaining + out["summary"]["rest_feature_reads"]
