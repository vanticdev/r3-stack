"""Typed project-local catalog authoring; the platform catalog is read-only."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from pydantic import ValidationError
import yaml

from r3_catalog import default_catalog
from r3_catalog.schema import ComponentType, ConnectorType, FastenerType, Material, Printer, PrintFit, WireGauge
from r3_model.project import Project

# Domain -> schema, catalog collection/YAML key, identity field.
DOMAINS = {
    "component": (ComponentType, "components", "id"),
    "connector": (ConnectorType, "connectors", "id"),
    "wire": (WireGauge, "wires", "awg"),
    "fastener": (FastenerType, "fasteners", "id"),
    "material": (Material, "materials", "id"),
    "printer": (Printer, "printers", "id"),
    "print_fit": (PrintFit, "print_fits", "process"),
}


def _paths(root: Path, collection: str) -> list[Path]:
    catalog = root / "catalog"
    return sorted((catalog / "components").glob("*.yaml")) if collection == "components" else [catalog / f"{collection}.yaml"]


def get_entry(root: Path, kind: str, entry_id: str) -> dict[str, Any]:
    if kind not in DOMAINS:
        return {"ok": False, "code": "usage_error", "message": f"catalog kind must be one of {', '.join(DOMAINS)}"}
    _, collection, identity = DOMAINS[kind]
    key = int(entry_id) if kind == "wire" and entry_id.isdigit() else entry_id
    entries = getattr(Project.load(root).catalog, collection)
    if key not in entries:
        return {"ok": False, "code": "catalog_entry_missing", "message": f"no catalog {kind} {entry_id!r}",
                "similar": [str(k) for k in entries if entry_id.lower() in str(k).lower()][:20]}
    origin = "platform"
    for path in _paths(root, collection):
        if path.exists() and any(str(e.get(identity)) == entry_id for e in (yaml.safe_load(path.read_text()) or {}).get(collection, [])):
            origin = str(path.relative_to(root))
    return {"ok": True, "kind": kind, "entry": entries[key].model_dump(mode="json", exclude_defaults=True), "defined_in": origin}


def put_entry(root: Path, kind: str, entry: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    if kind not in DOMAINS:
        return {"ok": False, "code": "usage_error", "message": f"catalog kind must be one of {', '.join(DOMAINS)}"}
    schema, collection, identity = DOMAINS[kind]
    try:
        parsed = schema.model_validate(entry)
    except ValidationError as error:
        return {"ok": False, "code": "catalog_entry_invalid",
                "errors": [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()]}
    key = getattr(parsed, identity)
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", str(key)):
        return {"ok": False, "code": "catalog_entry_invalid", "errors": [f"invalid {identity}: {key!r}"]}
    path = root / "catalog" / (f"components/{key}.yaml" if kind == "component" else f"{collection}.yaml")
    # A project may keep several components in a named family file. Update
    # the existing owner instead of adding a shadow definition in a new file.
    owners = []
    for candidate in _paths(root, collection):
        if root.resolve() not in candidate.resolve().parents:
            return {"ok": False, "code": "catalog_entry_invalid", "errors": ["catalog path escapes this project"]}
        if candidate.exists() and any(str(item.get(identity)) == str(key)
                for item in (yaml.safe_load(candidate.read_text()) or {}).get(collection, [])):
            owners.append(candidate)
    if len(owners) > 1:
        return {"ok": False, "code": "catalog_entry_ambiguous",
                "errors": [f"{identity} {key!r} is defined in multiple project catalog files"],
                "paths": [str(owner.relative_to(root)) for owner in owners]}
    if owners:
        path = owners[0]
    if root.resolve() not in path.resolve().parents:
        return {"ok": False, "code": "catalog_entry_invalid", "errors": ["catalog path escapes this project"]}
    payload = {"ok": True, "path": str(path.relative_to(root)), "id": str(key), "kind": kind,
               "overrides_platform_entry": key in getattr(default_catalog(resolve_models=False), collection), "dry_run": dry_run}
    if dry_run:
        return payload
    previous = path.read_bytes() if path.exists() else None
    document = yaml.safe_load(previous) if previous else {}
    # Retain other entries and metadata (e.g. joint_defaults) in shared tables.
    body = parsed.model_dump(mode="json", exclude_defaults=True, by_alias=True)
    entries = [e for e in document.get(collection, []) if e.get(identity) != key]
    document[collection] = [*entries, body]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Project-local catalog; record provenance for every number.\n" + yaml.safe_dump(document, sort_keys=False))
    try:
        Project.load(root).catalog
    except Exception as error:  # noqa: BLE001 - preserve the prior valid catalog
        if previous is None:
            path.unlink()
        else:
            path.write_bytes(previous)
        return {"ok": False, "code": "catalog_entry_invalid", "errors": [f"{type(error).__name__}: {error}"]}
    return payload
