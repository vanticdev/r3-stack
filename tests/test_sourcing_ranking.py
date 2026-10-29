"""Sourcing ranking: hard filters, budget, twin-check penalties, price/stock and estimated parameters."""

from __future__ import annotations

from r3_catalog.sourcing import Candidate, Offer, Requirement, SourcedValue, TwinCheck, rank


def value(v, status="datasheet"):
    return SourcedValue(value=v, status=status, source="https://example.com/spec")


def part(name, ram=8e9, price=None, stock=None, ram_status="datasheet", extra=None, lead=None):
    params = {"family": value("raspberry-pi-5"), **({"ram": value(ram, ram_status)} if ram is not None else {}), **(extra or {})}
    offers = [Offer(vendor="Shop", price=price, stock=stock, lead_time_days=lead)] if price is not None or stock is not None else []
    return Candidate(provider="test", kind="computer", name=name, mpn=name, params=params, offers=offers)


REQ = Requirement.from_source("pi", {"requires": {"kind": "computer", "family": "raspberry-pi-5", "ram": ">= 4 GB"},
                                     "budget": "200 USD", "quantity": 2}, ["pi"])


def test_tiers_and_order():
    ranked = rank(REQ, [part("small", ram=2e9, price=40, stock=9), part("unknown-ram", ram=None, price=50, stock=9),
                        part("cheap", price=60, stock=9), part("dear", price=95, stock=9), part("over-budget", price=120, stock=9)])
    assert [(r.candidate.name, r.tier) for r in ranked] == [
        ("cheap", "eligible"), ("dear", "eligible"), ("unknown-ram", "unverified"), ("over-budget", "rejected"), ("small", "rejected")]
    assert ranked[0].breakdown.get("price", 0) == 0 and ranked[1].breakdown["price"] < 0
    assert any("exceeds the budget" in reason for reason in ranked[3].reasons)
    assert ranked[4].reasons and "ram" in ranked[4].reasons[0]
    assert [r.rank for r in ranked] == [1, 2, 3, 4, 5]


def test_estimates_stock_and_lead_time_penalties():
    ranked = {r.candidate.name: r for r in rank(REQ, [
        part("datasheet", price=60, stock=9), part("estimated", price=60, stock=9, ram_status="estimated"),
        part("no-stock", price=60, stock=0), part("low-stock", price=60, stock=1), part("no-offer"), part("slow", price=60, stock=9, lead=70)])}
    assert ranked["datasheet"].score == 100
    assert ranked["estimated"].breakdown["estimated_required"] == -8
    assert ranked["no-stock"].breakdown["stock"] == -10 and ranked["low-stock"].breakdown["stock"] == -6
    assert ranked["no-offer"].breakdown["price"] == -6 and ranked["no-offer"].breakdown["stock"] == -3
    assert ranked["slow"].breakdown["lead_time"] == -5


def test_twin_checks_dominate_and_only_new_findings_count():
    def check(candidate):
        if candidate.name == "overvolts":
            return [TwinCheck("electrical", "power_in_overvoltage", "error", "5.5 V rail into a 5 V max input", ["pi"])]
        if candidate.name == "tight":
            return [TwinCheck("mechanical", "component_interference", "warning", "touches deck", ["pi", "deck"])]
        return []

    ranked = rank(REQ, [part("overvolts", price=10, stock=9), part("tight", price=60, stock=9), part("fine", price=90, stock=9)], check)
    assert [r.candidate.name for r in ranked] == ["fine", "tight", "overvolts"]
    assert ranked[2].twin_errors == 1 and ranked[2].breakdown["twin_errors"] == -30
    assert ranked[1].breakdown["twin_warnings"] == -8 and all(r.twin_checked for r in ranked)


def test_twin_limit_and_preferences():
    calls = []
    req = Requirement.from_source("pi", {"requires": {"kind": "computer", "family": "raspberry-pi-5"},
                                         "prefer": {"vendor": "Fixture Pi", "mass": "low"}}, ["pi"])
    parts = [part(f"p{i}", price=50 + i, stock=9, extra={"mass": value(0.04 + i * 0.001)}) for i in range(6)]
    parts[3].manufacturer = "Fixture Pi Ltd"
    ranked = rank(req, parts, lambda c: calls.append(c.name) or [], twin_limit=2, incumbent="p5")
    assert len(calls) == 2 and sum(r.twin_checked for r in ranked) == 2
    by_name = {r.candidate.name: r for r in ranked}
    assert by_name["p3"].breakdown["preferred_vendor"] == 5 and by_name["p5"].breakdown["current_part"] == 2
    assert by_name["p5"].breakdown["mass"] == -10


def test_duplicate_parts_from_two_providers_keep_the_first():
    first, second = part("same", price=60, stock=1), part("same", price=50, stock=1)
    ranked = rank(REQ, [first, second])
    assert len(ranked) == 1 and ranked[0].unit_price == 60
