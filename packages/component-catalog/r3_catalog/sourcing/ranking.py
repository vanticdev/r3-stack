"""Ranking: hard requirement filters, then a score from twin checks and purchase data.

Tiers (in order): ``eligible`` (every hard requirement verified), ``unverified``
(no requirement fails, but some have no value from any source), ``rejected``
(a requirement or the budget fails; twin checks are not run). Within a tier,
candidates without new twin-check errors come first, then by score.

Score (100 = no penalties)::

    - 30 per new twin-check error, - 8 per new twin-check warning
      (new = not already present with the slot's current part)
    - 12 per required parameter without a value
    -  5 per required parameter that is only estimated, - 1 per other estimated parameter (max - 5)
    - 15 * (price - cheapest) / (dearest - cheapest) among ranked candidates   (x2 with prefer.price: low)
    -  6 without a price; stock: - 10 if none, - 6 if below the quantity, - 3 unknown
    - lead time / 7 days (max - 5)
    - 10 * normalized mass with prefer.mass: low; + 5 for a preferred vendor/manufacturer;
    +  2 when the candidate is the slot's current part (ties keep a working design);
    -  5 * normalized distance from a preferred parameter value
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from .candidate import Candidate
from .quantities import normalize, quantity
from .requirements import ParamMatch, Requirement, canonical

TIERS = ("eligible", "unverified", "rejected")


@dataclass
class TwinCheck:
    """One finding of a digital-twin check run with the candidate in the robot."""

    check: str  # electrical | mechanical | assembly | mass | interface
    code: str
    severity: str  # error | warning | info
    message: str
    subjects: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"check": self.check, "code": self.code, "severity": self.severity, "message": self.message,
                "subjects": self.subjects, "data": self.data}


@dataclass
class Ranked:
    candidate: Candidate
    matches: list[ParamMatch]
    tier: str
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    checks: list[TwinCheck] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    unit_price: float | None = None
    currency: str = ""
    stock: int | None = None
    rank: int = 0

    @property
    def twin_errors(self) -> int:
        return sum(1 for c in self.checks if c.severity == "error")

    @property
    def twin_warnings(self) -> int:
        return sum(1 for c in self.checks if c.severity == "warning")

    def to_json(self) -> dict[str, Any]:
        return {"rank": self.rank, "tier": self.tier, "score": round(self.score, 2), "breakdown": {k: round(v, 2) for k, v in self.breakdown.items()},
                "reasons": self.reasons, "unit_price": self.unit_price, "currency": self.currency, "stock": self.stock,
                "twin": {"errors": self.twin_errors, "warnings": self.twin_warnings, "checks": [c.to_json() for c in self.checks]},
                "matches": [m.to_json() for m in self.matches], "candidate": self.candidate.model_dump(mode="json")}


CheckFunction = Callable[[Candidate], list[TwinCheck]]


def _budget_reason(requirement: Requirement, price: float | None, currency: str) -> str | None:
    budget = requirement.budget
    if budget is None or price is None:
        return None
    if currency != budget.currency:
        return None  # no exchange rates: reported, not filtered
    total = price * requirement.quantity
    if total > budget.amount + 1e-9:
        return f"{requirement.quantity} x {price:g} {currency} = {total:g} {currency} exceeds the budget {budget.amount:g} {budget.currency}"
    return None


def _normalized(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    return {k: (v - low) / (high - low) if high > low else 0.0 for k, v in values.items()}


def rank(requirement: Requirement, candidates: list[Candidate], check: CheckFunction | None = None,
         incumbent: str = "") -> list[Ranked]:
    """``incumbent``: name or part number of the slot's current part; a candidate
    that is the same product gets + 2 (keeps a working design on ties)."""
    ranked: list[Ranked] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.id in seen:  # the same part from two providers keeps the first (manufacturer data first)
            continue
        seen.add(candidate.id)
        matches = requirement.evaluate_candidate(candidate)
        failed = [m for m in matches if m.status == "fail"]
        offer = candidate.best_offer(requirement.quantity)
        entry = Ranked(candidate, matches, "eligible", unit_price=offer.unit_price(requirement.quantity) if offer else None,
                       currency=offer.currency if offer else "", stock=offer.stock if offer else None)
        entry.reasons = [f"{m.param}: {m.detail}" for m in failed]
        budget = _budget_reason(requirement, entry.unit_price, entry.currency)
        if budget:
            entry.reasons.append(budget)
        if failed or budget:
            entry.tier = "rejected"
        elif any(m.status == "unknown" for m in matches):
            entry.tier = "unverified"
            entry.reasons.extend(f"{m.param}: no value from any source" for m in matches if m.status == "unknown")
        if entry.tier != "rejected" and check is not None:
            entry.checks = check(candidate)
        ranked.append(entry)
    _score(requirement, [r for r in ranked if r.tier != "rejected"], incumbent)
    order = {tier: i for i, tier in enumerate(TIERS)}
    ranked.sort(key=lambda r: (order[r.tier], r.twin_errors > 0, -r.score, r.candidate.name))
    for index, entry in enumerate(ranked, start=1):
        entry.rank = index
    return ranked


def _score(requirement: Requirement, entries: list[Ranked], incumbent: str = "") -> None:
    prefer = {canonical(k): v for k, v in requirement.prefer.items()}
    currencies = Counter(e.currency for e in entries if e.unit_price is not None)
    currency = currencies.most_common(1)[0][0] if currencies else ""
    prices = _normalized({i: e.unit_price for i, e in enumerate(entries) if e.unit_price is not None and e.currency == currency})
    masses = _normalized({i: float(e.candidate.value("mass")) for i, e in enumerate(entries)
                          if isinstance(e.candidate.value("mass"), (int, float))})
    price_weight = 30.0 if normalize(prefer.get("price", "")) == "low" else 15.0
    for index, entry in enumerate(entries):
        b: dict[str, float] = {}
        b["twin_errors"] = -30.0 * entry.twin_errors
        b["twin_warnings"] = -8.0 * entry.twin_warnings
        required = {m.param for m in entry.matches if m.param != "kind"}
        b["unknown_required"] = -12.0 * sum(1 for m in entry.matches if m.status == "unknown")
        b["estimated_required"] = -5.0 * sum(1 for m in entry.matches if m.status == "pass" and m.provenance == "estimated")
        other_estimates = sum(1 for name, p in entry.candidate.params.items() if canonical(name) not in required and p.status == "estimated")
        b["estimated_other"] = -min(5.0, float(other_estimates))
        if index in prices:
            b["price"] = -price_weight * prices[index]
        elif entry.unit_price is None:
            b["price"] = -6.0
        b["stock"] = -3.0 if entry.stock is None else -10.0 if entry.stock <= 0 else -6.0 if entry.stock < requirement.quantity else 0.0
        offer = entry.candidate.best_offer(requirement.quantity)
        if offer and offer.lead_time_days:
            b["lead_time"] = -min(5.0, offer.lead_time_days / 7.0)
        if normalize(prefer.get("mass", "")) == "low" and index in masses:
            b["mass"] = -10.0 * masses[index]
        vendor = prefer.get("vendor") or prefer.get("manufacturer")
        if vendor:
            names = [entry.candidate.manufacturer, *(o.vendor for o in entry.candidate.offers)]
            if any(normalize(vendor) in normalize(n) for n in names if n):
                b["preferred_vendor"] = 5.0
        for name, target in prefer.items():
            if name in ("price", "mass", "vendor", "manufacturer", "stock"):
                continue
            value = entry.candidate.value(name)
            try:
                goal = quantity(target)
            except (ValueError, TypeError):
                continue
            if isinstance(value, (int, float)) and goal:
                b[f"prefer_{name}"] = -5.0 * min(1.0, abs(float(value) - goal) / abs(goal))
        if incumbent and normalize(incumbent) in normalize(f"{entry.candidate.manufacturer} {entry.candidate.name} {entry.candidate.mpn}"):
            b["current_part"] = 2.0
        entry.breakdown = {k: v for k, v in b.items() if v}
        entry.score = 100.0 + sum(b.values())
