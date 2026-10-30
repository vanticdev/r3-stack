"""R3 component catalog: component, connector and wire metadata with provenance."""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .schema import Catalog, ComponentType, ConnectorGeometry, ConnectorType, FastenerType, Material, Printer, PrintFit, WireGauge

DATA_DIR = Path(__file__).parent / "data"


def _merge(base: Any, override: Any) -> Any:
    """Deep merge for ``extends``: mappings merge key by key, everything else is replaced."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _merge(base.get(key), value) if key in base else value
        return merged
    return override


def _component_entries(paths: list[Path], raw_by_id: dict[str, dict[str, Any]]) -> list[tuple[Path, dict[str, Any]]]:
    """Component entries of one catalog folder with ``extends: <id>`` resolved (a variant such as
    another RAM size repeats only what differs from its base). Bases may be in any file of this
    folder or of a folder loaded earlier."""
    documents = [(path, yaml.safe_load(path.read_text())) for path in paths]
    pending = [(path, entry) for path, document in documents for entry in document["components"]]
    # Physical refinements live with the vendor family, independently of legacy
    # example catalogs. Apply before extends so every RAM variant inherits them.
    # The final merged entry still passes the strict ComponentType schema below.
    for path, document in documents:
        for identifier, refinement in document.get("refinements", {}).items():
            matches = [i for i, (_, entry) in enumerate(pending) if entry["id"] == identifier]
            if len(matches) != 1 or "id" in refinement or "extends" in refinement:
                raise ValueError(f"{path}: invalid component refinement {identifier!r}")
            i = matches[0]
            original_path, entry = pending[i]
            pending[i] = (original_path, _merge(entry, refinement))
    local = {entry["id"]: entry for _, entry in pending if "extends" not in entry}
    resolved: dict[str, dict[str, Any]] = {}

    def resolve(path: Path, entry: dict[str, Any], chain: tuple[str, ...] = ()) -> dict[str, Any]:
        if "extends" not in entry:
            return entry
        if entry["id"] in resolved:
            return resolved[entry["id"]]
        base_id = entry["extends"]
        if base_id in chain:
            raise ValueError(f"{path}: component extends cycle {' -> '.join((*chain, base_id))}")
        base = next((e for _, e in pending if e["id"] == base_id), None) or local.get(base_id) or raw_by_id.get(base_id)
        if base is None:
            raise ValueError(f"{path}: component {entry['id']!r} extends unknown component {base_id!r}")
        base = resolve(path, base, (*chain, entry["id"]))
        merged = _merge(copy.deepcopy(base), {k: v for k, v in entry.items() if k != "extends"})
        resolved[entry["id"]] = merged
        return merged

    out = [(path, resolve(path, entry)) for path, entry in pending]
    for _, entry in out:
        raw_by_id[entry["id"]] = entry
    return out


def load_catalog(*extra_dirs: Path) -> Catalog:
    """Load the bundled catalog plus optional project-local catalog folders
    (each may contain ``components/*.yaml``, ``connectors.yaml``, ``wires.yaml``)."""
    catalog = Catalog()
    raw_components: dict[str, dict[str, Any]] = {}
    for root in (DATA_DIR, *extra_dirs):
        root = Path(root)
        if not root.exists():
            continue
        wires = root / "wires.yaml"
        if wires.exists():
            for entry in yaml.safe_load(wires.read_text())["wires"]:
                gauge = WireGauge.model_validate(entry)
                catalog.wires[gauge.awg] = gauge
        fasteners = root / "fasteners.yaml"
        if fasteners.exists():
            for entry in yaml.safe_load(fasteners.read_text())["fasteners"]:
                fastener = FastenerType.model_validate(entry)
                catalog.fasteners[fastener.id] = fastener
        print_fits = root / "print_fits.yaml"
        if print_fits.exists():
            for entry in yaml.safe_load(print_fits.read_text())["print_fits"]:
                fit = PrintFit.model_validate(entry)
                catalog.print_fits[fit.process] = fit
        materials = root / "materials.yaml"
        if materials.exists():
            for entry in yaml.safe_load(materials.read_text())["materials"]:
                material = Material.model_validate(entry)
                catalog.materials[material.id] = material
        printers = root / "printers.yaml"
        if printers.exists():
            for entry in yaml.safe_load(printers.read_text())["printers"]:
                printer = Printer.model_validate(entry)
                catalog.printers[printer.id] = printer
        connectors = root / "connectors.yaml"
        connector_files = [connectors, *sorted(root.glob("connectors_*.yaml"))]
        for connectors in (c for c in connector_files if c.exists()):
            for entry in yaml.safe_load(connectors.read_text())["connectors"]:
                connector = ConnectorType.model_validate(entry)
                catalog.connectors[connector.id] = connector
        geometry = root / "connector_geometry.yaml"
        if geometry.exists():
            # Exact pin maps and models per connector type (generated by r3_connectors.derive).
            for type_id, entry in (yaml.safe_load(geometry.read_text()).get("geometry") or {}).items():
                if type_id not in catalog.connectors:
                    raise ValueError(f"connector_geometry.yaml: unknown connector type {type_id!r}")
                catalog.connectors[type_id].geometry = ConnectorGeometry.model_validate(entry)
        for path, entry in _component_entries(sorted((root / "components").glob("*.yaml")), raw_components):
            component = ComponentType.model_validate(entry)
            if component.id in catalog.components and root == DATA_DIR:
                raise ValueError(f"duplicate component id {component.id!r} in {path}")
            catalog.components[component.id] = component
    # A dataset link is resolved through the existing typed ModelRef. No second
    # component schema or project configuration format is introduced.
    from .modelbank import ModelBank
    from .modelbank.catalog import resolve_model
    bank = ModelBank()
    for component in catalog.components.values():
        if not component.mechanical.fabricated and component.kind != "pcb":
            component.model = resolve_model(component, bank)
    return catalog


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    return load_catalog()


__all__ = ["Catalog", "ComponentType", "ConnectorGeometry", "ConnectorType", "FastenerType", "Material", "Printer", "PrintFit", "WireGauge", "default_catalog", "load_catalog"]
