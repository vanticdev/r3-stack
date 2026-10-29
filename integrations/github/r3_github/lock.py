"""``r3.lock.yaml``: pinned revisions of everything a build depends on that
is not in the repository (platform version, Onshape microversion, PCB hashes).

Each entry is ``<section>: {revision: ..., pinned_at: ..., ...}``; the robot
model copies every ``revision`` into each stage result's ``inputs``.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path
from typing import Any

import yaml

LOCK_FILE = "r3.lock.yaml"


def read_lock(root: str | Path) -> dict[str, Any]:
    path = Path(root) / LOCK_FILE
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def pin(root: str | Path, section: str, revision: str, **fields: Any) -> dict[str, Any]:
    """Create or replace one lock entry and return it."""
    lock = read_lock(root)
    entry = {"revision": str(revision), "pinned_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **fields}
    lock[section] = entry
    (Path(root) / LOCK_FILE).write_text(
        "# Pinned external revisions. Written by r3 tools; do not edit by hand.\n" + yaml.safe_dump(lock, sort_keys=True))
    return entry


def pin_onshape(root: str | Path, document_id: str, workspace_id: str, element_id: str, microversion: str) -> dict[str, Any]:
    return pin(root, "onshape", microversion, document_id=document_id, workspace_id=workspace_id, element_id=element_id)


def pin_board(root: str | Path, board: str, sha256: str, **fields: Any) -> dict[str, Any]:
    return pin(root, f"pcb_{board}", sha256, **fields)


def git_revision(path: str | Path) -> str | None:
    """HEAD commit of the git checkout containing ``path``, if any."""
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip() or None
