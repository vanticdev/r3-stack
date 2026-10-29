"""Read and patch ``robot.yaml`` with optimistic concurrency.

Agents (Codex, Claude Code) and humans edit the same spec. Every read returns
the SHA-256 of the file bytes; a patch must quote the SHA it was computed
against. If the file changed in between, the patch is rejected with
``spec_conflict`` and the current SHA, so the agent re-reads and re-applies
instead of silently overwriting a concurrent edit.

Patches are RFC 7386 JSON merge patches: objects merge recursively, ``null``
deletes a key, and any other value (including lists) replaces the target.
Comments and key order are preserved when ``ruamel.yaml`` is installed.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import io
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import ValidationError

from r3_model.spec import RobotSpec

try:  # round-trip YAML keeps the author's comments and formatting
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
except ImportError:  # pragma: no cover - exercised only without ruamel
    YAML = None
    CommentedMap = dict
    CommentedSeq = list


def spec_path(root: str | Path) -> Path:
    return Path(root) / "robot.yaml"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_spec(root: str | Path, section: str | None = None) -> dict[str, Any]:
    path = spec_path(root)
    text = path.read_text()
    data = yaml.safe_load(text)
    if section:
        for key in section.split("."):
            if not isinstance(data, dict) or key not in data:
                return {"ok": False, "code": "spec_section_missing", "section": section, "sha256": file_sha(path)}
            data = data[key]
    return {"ok": True, "sha256": hashlib.sha256(text.encode()).hexdigest(), "path": str(path), "section": section or "", "spec": data}


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 merge patch, applied in place to mappings (round-trip safe)."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    if not isinstance(target, dict):
        target = CommentedMap() if YAML is not None else {}
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


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load_roundtrip(text: str) -> tuple[Any, Any]:
    if YAML is None:
        return None, yaml.safe_load(text)
    loader = YAML()
    loader.preserve_quotes = True
    loader.width = 4096
    loader.indent(mapping=2, sequence=4, offset=2)
    return loader, loader.load(text)


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
    return "\n".join(lines) + ("\n" if dumped.endswith("\n") else "")


def _dump(loader: Any, data: Any) -> str:
    if loader is None:
        return yaml.safe_dump(_plain(data), sort_keys=False)
    stream = io.StringIO()
    loader.dump(data, stream)
    return stream.getvalue()


def validation_errors(data: Any) -> list[dict[str, Any]]:
    try:
        RobotSpec.model_validate(_plain(data))
    except ValidationError as error:
        return [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]} for e in error.errors()]
    except ValueError as error:
        return [{"loc": "", "msg": str(error), "type": "value_error"}]
    return []


def patch_spec(root: str | Path, patch: dict[str, Any], expected_sha256: str, dry_run: bool = False) -> dict[str, Any]:
    """Apply a merge patch to robot.yaml if its SHA still equals ``expected_sha256``.

    Returns ``{"ok": True, "sha256": <new>, "previous_sha256": ...}`` or a
    structured failure with ``code`` ``spec_conflict`` / ``spec_invalid``."""
    path = spec_path(root)
    if not isinstance(patch, dict):
        return {"ok": False, "code": "patch_invalid", "message": "a merge patch must be a JSON object"}
    with _locked(path):
        text = path.read_text()
        current = hashlib.sha256(text.encode()).hexdigest()
        if expected_sha256 != current:
            return {"ok": False, "code": "spec_conflict", "expected_sha256": expected_sha256, "current_sha256": current,
                    "message": "robot.yaml changed since it was read; re-read it with r3_get_spec and re-apply the patch"}
        loader, data = _load_roundtrip(text)
        patched = merge_patch(data, patch)
        errors = validation_errors(patched)
        if errors:
            return {"ok": False, "code": "spec_invalid", "errors": errors, "current_sha256": current,
                    "message": f"patched spec fails schema validation ({len(errors)} error(s)); file unchanged"}
        new_text = _dump(loader, patched)
        if loader is not None:
            new_text = _restore_unchanged_lines(text, new_text)
        new_sha = hashlib.sha256(new_text.encode()).hexdigest()
        if not dry_run and new_text != text:
            handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=".robot.yaml.")
            with os.fdopen(handle, "w") as out:
                out.write(new_text)
            os.chmod(tmp, path.stat().st_mode & 0o777)
            os.replace(tmp, path)
        return {"ok": True, "dry_run": dry_run, "changed": new_text != text, "previous_sha256": current,
                "sha256": new_sha if not dry_run else current, "patched_sha256": new_sha,
                "preserves_comments": loader is not None}
