"""CAD pull's public description refresh and integrity hooks.

Publish and pin the immutable manifest first, then refresh_pulled. Consumers
call check_pulled after load_pulled. Neither hook contacts Onshape or authors
shapes. General spec reads/edits use spec_files and orchestrator.spec_ops.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .pulled import PullError, PulledModel
from .spec_files import _pulled_values, refresh_pulled_spec, validate_pulled_spec


def refresh_pulled(root: Path, manifest: dict[str, Any], *, expected_sha256: str | None = None,
                   remove_components: list[str] | None = None) -> dict[str, Any]:
    """Refresh part metadata and attachments from a published, pinned manifest."""
    root = Path(root).resolve()
    return refresh_pulled_spec(root, expected_sha256=expected_sha256, manifest=manifest,
                               remove_components=remove_components)


def check_pulled(root: Path, model: PulledModel) -> None:
    """Reject editable metadata/frames/attachments or a stale supplied model."""
    root = Path(root).resolve()
    current = validate_pulled_spec(root)
    if set(model.parts) != set(current.parts) or set(model.poses) != set(current.poses) or {m.id for m in model.mates} != {m.id for m in current.mates}:
        raise PullError("cad_pull_modified", "Supplied CAD instances or mates differ from the pinned pull")
    if _pulled_values(model, root) != _pulled_values(current, root):
        raise PullError("cad_pull_modified", "Supplied CAD model differs from the pinned pull")
