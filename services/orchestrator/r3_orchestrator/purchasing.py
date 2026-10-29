"""Purchase BOM shared by every user-facing surface; unknown prices stay unknown."""
from __future__ import annotations

from typing import Any

from r3_model.project import Project
from r3_model.spec import OwnedSpec


def purchase_summary(project: Project) -> dict[str, Any]:
    rows = []
    totals: dict[str, float] = {}
    unknown = 0
    slots = project.spec.source_slots()
    pins = project.lock.get("sourcing") or {}
    for cid, component in project.spec.components.items():
        owned = bool(component.owned)
        slot = next((key for key, members in slots.items() if cid in members), None)
        pin = pins.get(slot) or {}
        price = pin.get("price")
        amount = price.get("amount") if price else None
        currency = price.get("currency") if price else None
        if not owned:
            if amount is None:
                unknown += 1
            else:
                totals[currency] = totals.get(currency, 0.0) + amount
        rows.append({"component": cid, "type": component.type, "owned": owned,
                     "note": component.owned.note if isinstance(component.owned, OwnedSpec) else "",
                     "status": "owned" if owned else "sourced" if pin else "to_buy",
                     "unit_price": amount, "currency": currency, "purchase_cost": 0 if owned else amount})
    return {"lines": rows, "to_buy_totals": totals, "unpriced_to_buy": unknown,
            "to_buy_count": sum(not row["owned"] for row in rows)}
