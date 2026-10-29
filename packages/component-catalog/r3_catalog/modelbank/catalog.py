"""Additive bank resolution using the existing typed catalog ModelRef contract."""
from __future__ import annotations
from typing import Any

from r3_catalog.schema import ModelRef, Placement
from .bank import ModelBank


def resolve_model(component: Any, bank: ModelBank | None = None) -> ModelRef | None:
    bank = bank or ModelBank()
    entry = bank.resolve(component)
    if entry is None or not entry.available:
        return component.model
    source = next((s for s in entry.sources if s.direct and not s.requires_login and s.kind != 'onshape_public'
                   and s.format in ('step', 'stp', 'stp.zip', 'stp.rar') and s.sha256), None)
    if source is None:
        return component.model
    placement = Placement.model_validate(entry.placement) if entry.placement else Placement()
    return ModelRef(source='vendor', path=source.member or 'model.step', url=source.url, sha256=source.sha256,
                    license=source.license, placement=placement, params={'bank_id': entry.id})
