"""Geometry identities for catalog variants, independent of selected SKU metadata."""
from __future__ import annotations

from typing import Any

import yaml


def geometry_type(project: Any, type_id: str) -> str:
    """Follow ``extends`` (or lock ``cad_library.aliases``) only across identical CAD inputs.

    The catalog intentionally resolves away ``extends``. Read that declaration from its
    source files here, without changing the model/catalog contracts. An electrical or SKU
    variant may share CAD; a mechanical/interface/model override must have its own part.
    """
    from r3_catalog import DATA_DIR

    parents = getattr(project, "_onshape_alias_parents", None)
    if parents is None:
        parents: dict[str, str] = {}
        for root in (DATA_DIR, project.root / "catalog"):
            for path in sorted((root / "components").glob("*.yaml")):
                for entry in (yaml.safe_load(path.read_text()) or {}).get("components", []):
                    if entry.get("extends"):
                        parents[entry["id"]] = entry["extends"]
                    else:
                        parents.pop(entry["id"], None)
        parents.update((project.lock.get("cad_library") or {}).get("aliases") or {})
        project._onshape_alias_parents = parents
    seen: set[str] = set()
    while type_id in parents:
        if type_id in seen:
            raise ValueError(f"CAD alias cycle at {type_id}")
        seen.add(type_id)
        target = parents[type_id]
        source, base = project.catalog.component(type_id), project.catalog.component(target)
        fields = ("mechanical", "interfaces", "model", "cad_name", "cad_family", "kind")
        if any(getattr(source, key) != getattr(base, key) for key in fields):
            break
        type_id = target
    return type_id


def geometry_aliases(project: Any) -> dict[str, str]:
    return {comp.type: canonical for comp in project.spec.components.values()
            if (canonical := geometry_type(project, comp.type)) != comp.type}


def part_signature(part: Any) -> str:
    """CAD solid and mate-frame identity; moving a connector invalidates reuse too."""
    import hashlib
    import json
    from .recompose import plan_sha

    payload = {"plan": plan_sha(part.plan), "name": part.name, "studio": part.studio, "model": part.bank_model,
               "connectors": [[connector.name, connector.frame.round(9).tolist()] for connector in part.connectors]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def reusable_alias_parts(project: Any, parts: dict[str, Any]) -> set[str]:
    """Reuse canonical parts across library refreshes only with matching solid/frame evidence.

    The immutable library key alone cannot prove a parent interface is unchanged.
    Legacy locks must first pin a reviewed part signature; every new build records
    these signatures automatically. Unknown or changed signatures fail closed.
    """
    locked = project.lock.get("cad_library") or {}
    keys = {key for source in (locked, *locked.get("references", []))
            for studio in source.get("studios", {}).values() for key in studio["part_ids"]}
    signatures = locked.get("part_signatures") or {}
    candidates = set(geometry_aliases(project).values()) & keys & set(parts)
    return {key for key in candidates if signatures.get(key) == part_signature(parts[key])}
