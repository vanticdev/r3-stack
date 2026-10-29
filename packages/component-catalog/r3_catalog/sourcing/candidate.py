"""Candidates: purchasable parts found by a provider, with provenance per parameter."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: ``datasheet``: the manufacturer's datasheet, drawing or official hardware definition;
#: ``vendor``: the seller's listing; ``estimated``: inferred, a convention, or not supported by a cited source.
ValueStatus = Literal["datasheet", "vendor", "estimated"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourcedValue(_Model):
    """One parameter value in SI units (floats, ``[min, max]`` ranges, strings,
    string lists, booleans or ``{"size", "shape"}``) and where it came from."""

    value: Any
    status: ValueStatus
    source: str = Field(default="", description="Provenance URL (or document reference).")
    note: str = ""


class Offer(_Model):
    vendor: str
    url: str = ""
    sku: str = ""
    price: float | None = Field(default=None, description="Unit price at quantity 1.")
    currency: str = "USD"
    price_breaks: list[tuple[int, float]] = Field(default_factory=list, description="(min quantity, unit price).")
    stock: int | None = Field(default=None, description="Units in stock; None when the source does not say.")
    lead_time_days: float | None = None
    status: ValueStatus = "vendor"
    source: str = ""

    def unit_price(self, quantity: int = 1) -> float | None:
        price = self.price
        for minimum, value in sorted(self.price_breaks):
            if quantity >= minimum:
                price = value
        return price


class ModelFile(_Model):
    """A vendor 3D model or drawing."""

    url: str
    format: Literal["step", "step_zip", "stl", "pdf_drawing", "dxf", "other"] = "step"
    description: str = ""
    source: str = ""


class Candidate(_Model):
    id: str = Field(default="", description="Stable slug; derived from provider + manufacturer + MPN when empty.")
    provider: str
    kind: str
    name: str
    manufacturer: str = ""
    mpn: str = ""
    description: str = ""
    params: dict[str, SourcedValue] = Field(default_factory=dict)
    offers: list[Offer] = Field(default_factory=list)
    urls: dict[str, str] = Field(default_factory=dict, description="product, datasheet, drawing, ...")
    models: list[ModelFile] = Field(default_factory=list)
    retrieved_at: float = Field(default_factory=time.time)
    notes: list[str] = Field(default_factory=list)

    def model_post_init(self, _: Any) -> None:
        if not self.id:
            self.id = slug(self.provider, self.manufacturer, self.mpn or self.name)

    def value(self, name: str) -> Any:
        param = self.params.get(name)
        return None if param is None else param.value

    def best_offer(self, quantity: int = 1) -> Offer | None:
        priced = [o for o in self.offers if o.unit_price(quantity) is not None]
        if not priced:
            return self.offers[0] if self.offers else None
        return min(priced, key=lambda o: (o.stock == 0, o.unit_price(quantity)))

    def digest(self) -> str:
        data = self.model_dump(mode="json", exclude={"retrieved_at"})
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]

    @property
    def primary_url(self) -> str:
        offer = self.best_offer()
        return (offer.url if offer and offer.url else "") or self.urls.get("product") or next(iter(self.urls.values()), "")


def slug(*parts: str) -> str:
    text = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "part"


class ProviderReport(_Model):
    """What one provider did for one search."""

    provider: str
    status: Literal["ok", "skipped", "failed"]
    candidates: int = 0
    message: str = ""
    code: str = ""
    duration_s: float = 0.0
    requests: list[str] = Field(default_factory=list, description="URLs fetched (or served from cache).")
