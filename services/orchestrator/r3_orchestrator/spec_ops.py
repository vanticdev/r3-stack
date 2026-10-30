"""Read and patch the complete robot description with optimistic concurrency.

Agents (Codex, Claude Code) and humans edit the same spec. Every read returns
the SHA-256 of all included file names and bytes; a patch must quote the SHA it was computed
against. If any file changed in between, the patch is rejected with
``spec_conflict`` and the current SHA, so the agent re-reads and re-applies
instead of silently overwriting a concurrent edit.

Patches are RFC 7386 JSON merge patches: objects merge recursively, ``null``
deletes a key, and any other value (including lists) replaces the target.
Comments and key order are preserved with round-trip YAML.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from r3_model.spec import RobotSpec
from r3_model.spec_files import (SpecError, SpecFiles, atomic_write_files, dump_yaml, load_spec_files, roundtrip, spec_lock)

from ruamel.yaml.comments import CommentedMap


def spec_path(root: str | Path) -> Path:
    return Path(root) / "robot.yaml"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_spec(root: str | Path, section: str | None = None) -> dict[str, Any]:
    try:
        with spec_lock(root, shared=True):
            files = load_spec_files(root)
            files.validate()
            data = files.data
            if section:
                for key in section.split("."):
                    if not isinstance(data, dict) or key not in data:
                        return {"ok": False, "code": "spec_section_missing", "section": section, "sha256": files.sha256}
                    data = data[key]
            return {"ok": True, "sha256": files.sha256, "path": str(spec_path(root)),
                    "section": section or "", "spec": data,
                    "files": {name: {"sha256": hashlib.sha256(text.encode()).hexdigest(), "yaml": text}
                              for name, text in files.texts.items()},
                    "sources": [{"path": list(path), "file": origin.file, "source_path": list(origin.path)}
                                for path, origin in files.origins.items()],
                    "read_only": ["attachments", *[f"components.{name}.pulled" for name in files.data["components"]]]}
    except SpecError as error:
        return error.payload()


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 merge patch, applied in place to mappings (round-trip safe)."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    if not isinstance(target, dict):
        target = CommentedMap()
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict):
            target[key] = merge_patch(target.get(key), value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def _plain(value: Any) -> Any:
    """Convert round-trip containers to plain Python for validation."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _line_key(line: str) -> tuple[int, str]:
    return len(line) - len(line.lstrip(" ")), re.sub(r"\s+", " ", line.strip())


def _restore_unchanged_lines(original: str, dumped: str) -> str:
    """The YAML emitter normalizes alignment padding; reuse the author's exact
    line wherever the dumped line differs only in internal whitespace, so a
    patch produces a minimal git diff."""
    by_key: dict[tuple[int, str], str] = {}
    for line in original.splitlines():
        by_key.setdefault(_line_key(line), line)
    lines = [by_key.get(_line_key(line), line) for line in dumped.splitlines()]
    restored = "\n".join(lines) + ("\n" if dumped.endswith("\n") else "")
    # Whitespace inside YAML strings/block scalars is data. Only reuse aligned
    # lines when doing so leaves the requested values exactly unchanged.
    return restored if roundtrip(restored)[1] == roundtrip(dumped)[1] else dumped


def validation_errors(data: Any) -> list[dict[str, Any]]:
    try:
        RobotSpec.model_validate(_plain(data))
    except ValidationError as error:
        return [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]} for e in error.errors()]
    except ValueError as error:
        return [{"loc": "", "msg": str(error), "type": "value_error"}]
    return []


def _graph_diagnostics(root: Path, data: Any) -> list[dict[str, Any]]:
    """Connectivity problems in the patched spec (dangling connectors, mate mismatches). Reported, not
    rejected: an agent may need several patches to rewire a harness. ``r3 validate`` runs the full checks."""
    from r3_catalog import load_catalog
    from r3_model.graph import RobotGraph

    try:
        graph = RobotGraph(RobotSpec.model_validate(_plain(data)), load_catalog(root / "catalog"))
    except Exception as error:  # a broken project catalog must not block spec edits
        return [{"code": "graph_unavailable", "severity": "warning", "message": str(error)}]
    return [d.model_dump(mode="json") for d in graph.diagnostics]


def _patch_documents(files: SpecFiles, patch: dict[str, Any]) -> dict[str, str]:
    """Route merge patches through include pointers, retaining round-trip nodes."""
    documents = {name: roundtrip(text) for name, text in files.texts.items()}
    changed: set[str] = set()
    split = len(files.texts) > 1

    def apply(target: Any, change: Any, file: str, path: tuple[Any, ...]) -> Any:
        if isinstance(target, dict) and "$include" in target:
            included = target["$include"]
            loader, document = documents[included]
            documents[included] = loader, apply(document, change, included, path)
            return target
        changed.add(file)
        if not isinstance(change, dict):
            return copy.deepcopy(change)
        if "$include" in change:
            raise files.error("patch_invalid", path, "include pointers are managed by the description layout")
        if not isinstance(target, dict):
            target = CommentedMap()
        for key, value in change.items():
            if value is None:
                target.pop(key, None)
            elif split and path == ("components",) and key not in target:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", key):
                    raise files.error("patch_invalid", (*path, key), "part id must be a safe filename")
                name = f"parts/{key}.yaml"
                if name in documents or (files.root / name).exists():
                    raise files.error("spec_duplicate_definition", (*path, key), f"part file already exists: {name}")
                loader, _ = roundtrip("{}\n")
                documents[name] = loader, apply(CommentedMap(), value, name, (*path, key))
                target[key] = CommentedMap({"$include": name})
            else:
                target[key] = apply(target.get(key), value, file, (*path, key))
        return target

    loader, data = documents["robot.yaml"]
    documents["robot.yaml"] = loader, apply(data, patch, "robot.yaml", ())
    return {name: _restore_unchanged_lines(files.texts.get(name, ""), dump_yaml(*documents[name])) for name in changed}


def _check_readonly(files: SpecFiles, proposed: SpecFiles, patch: dict[str, Any]) -> None:
    if "attachments" in patch or files.data.get("attachments") != proposed.data.get("attachments"):
        raise files.error("spec_read_only", ("attachments",), "assembly attachments are owned by Onshape pull")
    before = files.data.get("components", {})
    after = proposed.data.get("components", {})
    if not isinstance(after, dict):
        after = {}
    changes = patch.get("components", {})
    for name in set(before) | set(after):
        old = before[name].get("pulled") if isinstance(before.get(name), dict) else None
        new = after[name].get("pulled") if isinstance(after.get(name), dict) else None
        explicit = isinstance(changes, dict) and isinstance(changes.get(name), dict) and "pulled" in changes[name]
        if old != new or explicit:
            raise files.error("spec_read_only", ("components", name, "pulled"),
                              "Onshape-derived fields are owned by pull; run r3 cad pull to refresh them")


def patch_spec(root: str | Path, patch: dict[str, Any], expected_sha256: str, dry_run: bool = False) -> dict[str, Any]:
    """Patch the merged description, writing its owning files under one lock."""
    if not isinstance(patch, dict):
        return {"ok": False, "code": "patch_invalid", "message": "a merge patch must be a JSON object"}
    root = Path(root).resolve()
    current = ""
    try:
        with spec_lock(root):
            files = load_spec_files(root)
            current = files.sha256
            if expected_sha256 != current:
                return {"ok": False, "code": "spec_conflict", "expected_sha256": expected_sha256, "current_sha256": current,
                        "message": "description files changed since read; re-read with r3_get_spec and re-apply the patch"}
            updates = _patch_documents(files, patch)
            proposed = load_spec_files(root, updates)
            _check_readonly(files, proposed, patch)
            proposed.validate()
            graph_diagnostics = _graph_diagnostics(root, proposed.data)
            changed = {name: text for name, text in updates.items() if text != files.texts.get(name)}
            if not dry_run:
                atomic_write_files(root, changed)
            return {"ok": True, "dry_run": dry_run, "changed": bool(changed), "previous_sha256": current,
                    "sha256": current if dry_run else proposed.sha256, "patched_sha256": proposed.sha256,
                    "preserves_comments": True, "files_changed": sorted(changed), "graph_diagnostics": graph_diagnostics}
    except SpecError as error:
        return {**error.payload(), "code": "spec_read_only" if error.code == "spec_read_only" else "spec_invalid",
                "current_sha256": current}
