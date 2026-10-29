"""Slot requirements and their evaluation against candidates or catalog parts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..schema import ComponentType
from .candidate import Candidate, SourcedValue
from .quantities import Constraint, Equals, MatchStatus, Money, normalize, parse_constraint, parse_money

#: Parameter names with the same meaning across providers (``requires`` keys may use either).
ALIASES = {"frequency": "band", "voltage": "supply", "supply_voltage": "supply", "memory": "ram", "dia": "diameter"}


def canonical(name: str) -> str:
    key = normalize(name).replace("-", "_")
    return ALIASES.get(key, key)


@dataclass
class ParamMatch:
    param: str
    required: str
    status: MatchStatus
    detail: str
    value: Any = None
    provenance: str = ""
    source: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"param": self.param, "required": self.required, "status": self.status, "detail": self.detail,
                "value": self.value, "provenance": self.provenance, "source": self.source}


@dataclass
class Requirement:
    """The hard filters and soft preferences of one sourcing slot."""

    slot: str
    kind: str
    constraints: dict[str, Constraint]
    raw: dict[str, Any]
    prefer: dict[str, Any] = field(default_factory=dict)
    quantity: int = 1
    budget: Money | None = None
    providers: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)

    @classmethod
    def from_source(cls, slot: str, source: Any, components: list[str]) -> "Requirement":
        """``source`` is an ``r3_model.spec.SourceSpec`` (or the equivalent dict)."""
        data = source.model_dump() if hasattr(source, "model_dump") else dict(source)
        requires = dict(data["requires"])
        kind = str(requires.pop("kind"))
        constraints = {canonical(k): parse_constraint(v) for k, v in requires.items()}
        return cls(slot=slot, kind=kind, constraints=constraints, raw={"kind": kind, **requires},
                   prefer=dict(data.get("prefer") or {}), quantity=int(data.get("quantity") or len(components) or 1),
                   budget=parse_money(data.get("budget")), providers=list(data.get("providers") or []),
                   components=list(components))

    def digest(self) -> str:
        payload = {"requires": self.raw, "prefer": self.prefer, "quantity": self.quantity,
                   "budget": self.budget.to_json() if self.budget else None}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def keywords(self) -> str:
        """Search keywords for text-search providers: the kind plus the string requirements."""
        words = [self.kind]
        for name, constraint in self.constraints.items():
            if isinstance(constraint, Equals) and len(constraint.options) == 1 and constraint.options[0] not in ("true", "false"):
                words.append(constraint.options[0].replace("-", " "))
            elif name in ("band",):
                words.append(constraint.text)
        return " ".join(dict.fromkeys(words))

    def evaluate(self, params: dict[str, SourcedValue], kind: str) -> list[ParamMatch]:
        matches = [ParamMatch("kind", self.kind, "pass" if normalize(kind) == normalize(self.kind) else "fail",
                              f"{kind} vs {self.kind}", kind, "datasheet")]
        lookup = {canonical(name): value for name, value in params.items()}
        for name, constraint in self.constraints.items():
            sourced = lookup.get(name)
            if sourced is None:
                matches.append(ParamMatch(name, constraint.text, "unknown", "no value from any source"))
                continue
            status, detail = constraint.check(sourced.value)
            matches.append(ParamMatch(name, constraint.text, status, detail, sourced.value, sourced.status, sourced.source))
        return matches

    def evaluate_candidate(self, candidate: Candidate) -> list[ParamMatch]:
        return self.evaluate(candidate.params, candidate.kind)


# ------------------------------------------------------------- catalog parts
def catalog_params(component: ComponentType) -> dict[str, SourcedValue]:
    """Requirement-comparable parameters of an existing catalog part, so a fixed
    ``type`` can be checked against its slot's ``requires`` (and templates compared)."""
    mech = component.mechanical
    status = "datasheet" if mech.provenance.status in ("datasheet", "measured", "vendor") else "estimated"
    source = mech.provenance.source or f"catalog:{component.id}"
    out: dict[str, SourcedValue] = {"mass": SourcedValue(value=mech.mass, status=status, source=source)}
    if mech.shape == "cylinder":
        out["diameter"] = SourcedValue(value=mech.size[0], status=status, source=source)
        out["width"] = SourcedValue(value=mech.size[1], status=status, source=source)
    else:
        for axis, name in zip(mech.size, ("length", "width", "height")):
            out[name] = SourcedValue(value=axis, status=status, source=source)
    for feature in mech.features:
        if feature.id.startswith("hub") and feature.kind in ("bore", "d_bore", "socket"):
            shape = {"bore": "bore", "d_bore": "d-bore", "socket": "hex"}[feature.kind]
            out["hub"] = SourcedValue(value={"size": feature.diameter, "shape": shape}, status=feature.provenance.status
                                      if feature.provenance.status in ("datasheet", "vendor") else "estimated",
                                      source=feature.provenance.source or source)
    for pin in component.pins.values():
        if pin.role == "power_in" and pin.voltage_min is not None and pin.voltage_max is not None:
            out["supply"] = SourcedValue(value=[pin.voltage_min, pin.voltage_max], status="estimated", source=f"catalog:{component.id}")
            break
    for software in component.software:
        if software.protocol and software.bus != "none":
            out["protocol"] = SourcedValue(value=software.protocol, status="datasheet", source=f"catalog:{component.id}")
    if component.behavior:
        frequency = component.behavior.params.get("frequency")
        if frequency is not None:
            from .quantities import quantity

            out["band"] = SourcedValue(value=quantity(frequency), status="datasheet", source=component.behavior.provenance.source)
    ram = re.search(r"(\d+)\s*GB", component.name)
    if component.kind == "computer" and ram:
        out["ram"] = SourcedValue(value=float(ram.group(1)) * 1e9, status="datasheet", source=f"catalog:{component.id}")
    if component.kind == "computer" and "raspberry pi 5" in component.name.lower():
        out["family"] = SourcedValue(value="raspberry-pi-5", status="datasheet", source=f"catalog:{component.id}")
    return out
