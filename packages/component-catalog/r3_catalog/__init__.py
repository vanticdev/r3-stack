"""R3 component catalog: component, connector and wire metadata with provenance."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .schema import Catalog, ComponentType, ConnectorType, FastenerType, WireGauge

DATA_DIR = Path(__file__).parent / "data"


def load_catalog(*extra_dirs: Path) -> Catalog:
    """Load the bundled catalog plus optional project-local catalog folders
    (each may contain ``components/*.yaml``, ``connectors.yaml``, ``wires.yaml``)."""
    catalog = Catalog()
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
        connectors = root / "connectors.yaml"
        if connectors.exists():
            for entry in yaml.safe_load(connectors.read_text())["connectors"]:
                connector = ConnectorType.model_validate(entry)
                catalog.connectors[connector.id] = connector
        for path in sorted((root / "components").glob("*.yaml")):
            for entry in yaml.safe_load(path.read_text())["components"]:
                component = ComponentType.model_validate(entry)
                if component.id in catalog.components and root == DATA_DIR:
                    raise ValueError(f"duplicate component id {component.id!r} in {path}")
                catalog.components[component.id] = component
    return catalog


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    return load_catalog()


__all__ = ["Catalog", "ComponentType", "ConnectorType", "FastenerType", "WireGauge", "default_catalog", "load_catalog"]
