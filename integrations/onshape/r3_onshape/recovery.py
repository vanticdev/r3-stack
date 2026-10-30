"""Restart a failed new CAD build without losing references or touching Onshape."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from typing import Any, Iterator

from r3_model.project import Project

PENDING_SECTIONS = ("cad_library_build", "cad_build_pending")


class CadBusy(RuntimeError):
    pass


@contextmanager
def build_lock(project: Project) -> Iterator[None]:
    with (project.build_dir("cad") / ".build.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CadBusy("A CAD build or recovery is already running for this project") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def recover(project: Project, expected_sha256: str = "", dry_run: bool = False) -> dict[str, Any]:
    """Inspect first; a matching revision abandons only unfinished new builds.

    Completed pins, journals and all native documents remain intact. The next
    CAD stage creates a new attempt using current inputs. No transport is used.
    """
    try:
        with build_lock(project):
            project = Project.load(project.root)
            path = project.root / "r3.lock.yaml"
            current = hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()
            pending = {key: project.lock[key] for key in PENDING_SECTIONS if project.lock.get(key)}
            payload = {"ok": True, "sha256": current, "pending": pending, "changed": False, "dry_run": dry_run}
            if expected_sha256 and expected_sha256 != current:
                return {**payload, "ok": False, "code": "cad_recovery_conflict", "message": "Lock changed; inspect recovery again"}
            if not expected_sha256 or dry_run or not pending:
                return payload
            history = list(project.lock.get("cad_abandoned_builds") or [])
            for section, record in pending.items():
                if not any(item.get("section") == section and item.get("build") == record for item in history):
                    history.append({"section": section, "build": record, "abandoned_at": datetime.now(timezone.utc).isoformat()})
            # References are persisted before clearing. An interruption can leave
            # original pending state, but cannot orphan its native document.
            project.write_lock("cad_abandoned_builds", history)
            for section in pending:
                project.write_lock(section, {})
            return {**payload, "changed": True, "message": "Unfinished attempts archived locally; documents and journals preserved. Run CAD with current inputs."}
    except CadBusy as error:
        return {"ok": False, "code": "cad_busy", "message": str(error)}
