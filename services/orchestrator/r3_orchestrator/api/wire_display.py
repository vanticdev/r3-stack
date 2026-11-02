"""Read-only conductor inspection paths derived from the current routing response."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import threading
from typing import Any

import numpy as np

_lock = threading.RLock()


def _build_flat_paths(payload: str, diameters: tuple[tuple[str, float], ...]) -> dict[str, Any]:
    from r3_connectors.display import display_paths
    paths = display_paths(json.loads(payload), dict(diameters), layout="flat")
    identity = payload.encode() + b"\0flat\0" + json.dumps(diameters, separators=(",", ":")).encode()
    return {"layout": "bonded_flat", "qualification": "display_only",
            "source_sha256": hashlib.sha256(identity).hexdigest(),
            "paths": {name: path.tolist() for name, path in paths.items()}}


_flat_paths = lru_cache(maxsize=8)(_build_flat_paths)


def conductor_display(routes: dict[str, Any] | None, detail: dict[str, Any] | None) -> dict[str, Any] | None:
    """Never reuse stale connector paths or invent an absent conductor diameter."""
    if not routes:
        return None
    wires, diameters = {}, []
    for name, wire in routes.get("wires", {}).items():
        metadata = (detail or {}).get("wires", {}).get(name, {})
        diameter = metadata.get("od", wire.get("od"))
        if wire.get("failed") or metadata.get("failed"):
            continue
        if not isinstance(diameter, (int, float)) or not math.isfinite(diameter) or diameter <= 0:
            continue
        try:
            path = np.asarray(wire.get("path", []), dtype=float)
        except (TypeError, ValueError):
            continue
        if path.ndim != 2 or path.shape[1:] != (3,) or len(path) < 2 or not np.isfinite(path).all():
            continue
        wires[name] = {key: wire[key] for key in ("harness", "path", "ends", "fanout") if key in wire}
        diameters.append((name, float(diameter)))
    payload = json.dumps({"wires": wires}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # Serialize cold generation too: concurrent panels must not duplicate its
    # temporary projection arrays. Retain at most eight immutable input sets.
    with _lock:
        generate = _flat_paths if len(payload.encode()) <= 1024 * 1024 else _build_flat_paths
        return generate(payload, tuple(sorted(diameters)))
