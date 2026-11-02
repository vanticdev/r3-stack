"""Read loaded Onshape meshes with revision and byte provenance, never author CAD."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any

import numpy as np

from r3_model.pulled import PullError, _frame
from .native import NativeCadTransport

SCHEMA = "r3/viewer-mesh@1"


def _folder(project: Any) -> Path:
    root = Path(project.root).resolve()
    folder = root / "build/cad/viewer-mesh"
    if root not in folder.resolve().parents:
        raise PullError("cad_viewer_invalid", "Viewer mesh cache must stay inside this project")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cached_file(root: Path, relative: str, checksum: str) -> Path:
    path = root / relative
    if (path.is_symlink() or root.resolve() not in path.resolve().parents or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != checksum):
        raise PullError("cad_viewer_modified", "Viewer artifact is missing, modified or outside its cache")
    return path


def cached(project: Any) -> dict[str, Any] | None:
    try:
        return _cached(project)
    except PullError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise PullError('cad_viewer_invalid', f'Invalid native viewer cache: {error}') from error


def _cached(project: Any) -> dict[str, Any] | None:
    folder = _folder(project)
    pointer = folder / "latest.json"
    if not pointer.exists():
        return None
    if pointer.is_symlink():
        raise PullError("cad_viewer_invalid", "Viewer cache index must not be a symlink")
    data = json.loads(pointer.read_text())
    if data.get("schema") != SCHEMA or data.get("qualification") != "display_mesh":
        raise PullError("cad_viewer_invalid", "Unsupported viewer mesh cache")
    for part in data["parts"]:
        for kind in ("mesh", "provenance"):
            _cached_file(folder, part[kind], part[kind + "_sha256"])
        _frame(part["pose"], "native viewer pose")
    return data


def capture(project: Any, url: str = "", tab_id: int | None = None,
            transport: Any = None) -> dict[str, Any]:
    """Cache current loaded bodies as unchanged OBJ plus native placement.

    This can show an unfinished assembly without hiding missing components.
    It never installs cad/pulled or asserts closure, fit or fabrication readiness.
    A revision change aborts publication and retains the prior snapshot.
    """
    try:
        return _capture(project, url, tab_id, transport)
    except PullError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise PullError('cad_viewer_invalid', f'Invalid native viewer response: {error}') from error


def _capture(project: Any, url: str, tab_id: int | None, transport: Any) -> dict[str, Any]:
    url = url or project.spec.cad.source or ""
    native = transport if transport is not None else NativeCadTransport()
    versioned = re.fullmatch(r"https://([^/]+)/documents/([a-f0-9]{24})/v/[a-f0-9]{24}/e/([a-f0-9]{24})/?", url)
    if versioned:
        host, did, eid = versioned.groups()
        document = native.api_request('GET', f'/api/v9/documents/{did}')
        url = f'https://{host}/documents/{did}/w/{document["defaultWorkspace"]["id"]}/e/{eid}'
    if not re.fullmatch(r"https://[^/]+/documents/[a-f0-9]{24}/w/[a-f0-9]{24}/e/[a-f0-9]{24}/?", url):
        raise PullError("cad_viewer_target_invalid", "Use the exact, unconfigured workspace assembly URL")
    if tab_id is None:
        try:
            previous = cached(project)
        except PullError:
            previous = None  # A fresh inspection can replace a corrupt preview.
        if previous and previous['url'] == url:
            tab_id = previous.get('tab_id')
    listing = native.local("viewer_mesh", {"url": url, "action": "list", "tab_id": tab_id})
    snapshot = native.artifact(listing["snapshot"])
    revision = listing.get("microversion")
    if (not revision or snapshot.get("kind") != "viewer-mesh-list" or snapshot.get("url") != url
            or snapshot.get("microversion") != revision or snapshot.get("tab_id") != listing.get("tab_id")):
        raise PullError("cad_viewer_invalid", "Viewer snapshot does not match the selected revision and tab")
    folder = _folder(project)
    attempt = Path(tempfile.mkdtemp(prefix="snapshot-", dir=folder))
    parts, unavailable = [], []
    for item in snapshot["parts"]:
        if not item.get("loaded"):
            unavailable.append({"name": item.get("name"), "part_index": item["part_index"]})
            continue
        result = native.local("viewer_mesh", {"url": url, "action": "download",
            "tab_id": listing["tab_id"], "snapshot": listing["snapshot"], "part_index": item["part_index"]})
        if (result.get("units") != "meter" or result.get("coordinate_frame") != "part-local"
                or result.get("exact_cad") is not False):
            raise PullError("cad_viewer_invalid", "Unsupported viewer mesh coordinates or qualification")
        handles = {"mesh": result["artifact"], "provenance": result["provenance"]}
        row = {"name": item.get("name") or str(item["part_index"]), "part_index": item["part_index"],
               "occurrence_path": item["occurrence_path"], "body_id": item["body_id"],
               "full_element_id": item["full_element_id"], "body_count": result["body_count"]}
        for kind, handle in handles.items():
            expected_suffix = ".obj" if kind == "mesh" else ".json"
            if not re.fullmatch(r"[a-f0-9]{64}" + re.escape(expected_suffix), handle):
                raise PullError("cad_viewer_invalid", "Unsupported viewer artifact handle")
            raw = native.artifact_bytes(handle)
            checksum = hashlib.sha256(raw).hexdigest()
            if checksum != handle.split(".")[0]:
                raise PullError("cad_viewer_modified", "Viewer artifact checksum mismatch")
            (attempt / handle).write_bytes(raw)
            row[kind] = str((attempt / handle).relative_to(folder))
            row[kind + "_sha256"] = checksum
            if kind == "provenance":
                provenance = json.loads(raw)
        if (provenance.get("microversion") != revision or any(
                provenance.get("part", {}).get(key) != item[key]
                for key in ("body_id", "full_element_id", "occurrence_path"))
                or provenance.get("mesh_artifact") != handles["mesh"]):
            raise PullError("cad_viewer_invalid", "Viewer provenance identifies a different part or revision")
        pose = _frame(np.asarray(result["occurrence_transform"], dtype=float).reshape(4, 4, order="F"),
                      "native viewer pose")
        if not np.array_equal(np.asarray(provenance["occurrence_transform"]), result["occurrence_transform"]):
            raise PullError("cad_viewer_invalid", "Viewer placement differs from its provenance")
        row["pose"] = pose.tolist()
        parts.append(row)
    final = native.local("viewer_mesh", {"url": url, "action": "list", "tab_id": listing["tab_id"]})
    if final.get("microversion") != revision:
        raise PullError("cad_viewer_stale", "CAD changed during download; list and capture the new revision")
    if not parts:
        raise PullError("cad_viewer_unloaded", "No loaded mesh geometry in the selected Onshape tab")
    data = {"schema": SCHEMA, "url": url, "microversion": revision, "snapshot": listing["snapshot"],
            "tab_id": listing["tab_id"], "captured_at": datetime.now(timezone.utc).isoformat(),
            "qualification": "display_mesh", "exact_cad": False, "closed_solid_verified": False,
            "parts": parts, "unavailable": unavailable, "total": len(snapshot["parts"])}
    temporary = attempt / "index.json"
    if temporary.is_symlink() or (folder / "latest.json").is_symlink():
        raise PullError("cad_viewer_invalid", "Viewer cache index must not be a symlink")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(folder / "latest.json")
    return data
