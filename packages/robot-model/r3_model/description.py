"""CAD pull's public description refresh and integrity hooks.

Publish and pin the immutable manifest first, then refresh_pulled. Consumers
call check_pulled after load_pulled. Neither hook contacts Onshape or authors
shapes. General spec reads/edits use spec_files and orchestrator.spec_ops.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pulled import PullError, PulledModel, load_pulled
from .spec_files import _pulled_values, refresh_pulled_spec, validate_pulled_spec


def refresh_pulled(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Refresh part metadata and attachments from a published, pinned manifest."""
    root = Path(root).resolve()
    load_pulled(root)
    if json.loads((root / "cad/pulled/pull.json").read_text()) != manifest:
        raise PullError("cad_pull_modified", "Refresh manifest differs from the pinned CAD pull")
    return refresh_pulled_spec(root)


def check_pulled(root: Path, model: PulledModel) -> None:
    """Reject editable metadata/frames/attachments or a stale supplied model."""
    root = Path(root).resolve()
    current = validate_pulled_spec(root)
    if _pulled_values(model, root) != _pulled_values(current, root):
        raise PullError("cad_pull_modified", "Supplied CAD model differs from the pinned pull")
