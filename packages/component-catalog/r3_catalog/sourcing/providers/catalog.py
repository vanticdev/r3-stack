"""The platform catalog as a sourcing provider: bought parts R3 already models in full.

Every bundled entry that is not fabricated (Raspberry Pi 5 variants, the Arducam IMX296 camera and CSI
cables, the Traxxas Slash 2WD driveline, Savox servo, Hobbywing ESC, Pololu buck, SparkFun GNSS, Gens Ace
LiPo, GY-521 IMU, ...) becomes a candidate whose parameters carry the entry's own provenance (datasheet,
vendor or estimated, with its source). Choosing one pins the existing catalog id: no entry is generated,
because the catalog already has its pins, connectors, interfaces and behaviour model.
"""

from __future__ import annotations

from typing import Any

from ..candidate import Candidate, SourcedValue
from ..requirements import Requirement, catalog_params
from .base import Provider

#: Behaviour parameters exposed as requirement-comparable values, per catalog kind: (param name, behaviour key).
BEHAVIOR_PARAMS: dict[str, tuple[tuple[str, str], ...]] = {
    "battery": (("cells", "cells_series"), ("capacity", "capacity"), ("c_rating", "c_rating")),
    "motor": (("kv", "kv_rpm_per_v"), ("max_rpm", "max_rpm")),
    "servo": (("torque", "stall_torque_at_6v"), ("speed", "speed_60deg_at_6v")),
    "regulator": (("output_voltage", "v_out"), ("output_current", "i_max")),
    "esc": (("current", "current_continuous"), ("bec_voltage", "bec_voltage"), ("bec_current", "bec_current")),
    "camera": (("sensor", "sensor"), ("shutter", "shutter"), ("hfov", "hfov"), ("vfov", "vfov"), ("resolution", "resolution")),
    "sensor": (("update_rate", "update_rate"), ("horizontal_cep", "horizontal_cep")),
    "gearbox": (("ratio", "ratio"),),
    "wheel": (("radius", "radius"),),
}


def _status(status: str) -> str:
    return status if status in ("datasheet", "vendor") else ("datasheet" if status == "measured" else "estimated")


def _url(text: str) -> str:
    for word in text.replace("(", " ").replace(")", " ").replace(";", " ").split():
        if word.startswith("http"):
            return word.rstrip(".,")
    return ""


def catalog_candidate(component: Any) -> Candidate:
    """A candidate for one catalog entry (``mpn`` = part number, ``urls.catalog`` = the catalog id)."""
    params = dict(catalog_params(component))
    behavior = component.behavior
    if behavior is not None:
        source = behavior.provenance.source or f"catalog:{component.id}"
        for name, key in BEHAVIOR_PARAMS.get(component.kind, ()):
            if key in behavior.params:
                from ..quantities import quantity

                raw = behavior.params[key]
                try:
                    value = quantity(raw) if isinstance(raw, str) and any(c.isdigit() for c in raw) else raw
                except (ValueError, TypeError):
                    value = raw
                params[name] = SourcedValue(value=value, status=_status(behavior.provenance.status), source=source)
    urls = {"catalog": f"catalog:{component.id}"}
    for provenance in (component.provenance, component.mechanical.provenance):
        if (link := _url(provenance.source)):
            urls.setdefault("product", link)
    if component.model is not None and component.model.url:
        urls["step"] = component.model.url
    notes = [f"R3 catalog entry {component.id}: pins, connectors, interfaces and behaviour model already modelled"]
    estimated = [n for n, v in params.items() if v.status == "estimated"]
    if estimated:
        notes.append(f"estimated values: {', '.join(sorted(estimated))} (see the entry's provenance)")
    return Candidate(id=f"catalog-{component.id}", provider="catalog", kind=component.kind, name=component.name,
                     manufacturer=component.manufacturer, mpn=component.part_number or component.id,
                     description=component.description.strip(), params=params, urls=urls, notes=notes)


class CatalogProvider(Provider):
    id = "catalog"
    title = "R3 platform catalog (bought parts modelled with provenance)"
    category = "manufacturer"

    def search(self, requirement: Requirement) -> list[Candidate]:
        from r3_catalog import default_catalog

        return [catalog_candidate(c) for c in default_catalog().components.values()
                if c.kind == requirement.kind and not c.mechanical.fabricated]
