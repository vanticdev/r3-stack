"""Expected catalog authoring failures shared by tools and pipeline stages."""
from __future__ import annotations

from typing import Any


class ModelSelectionRequired(ValueError):
    code = "catalog_model_selection_required"

    def __init__(self, component: Any, candidate_ids: list[str]) -> None:
        self.details = {"component_id": component.id, "candidate_ids": sorted(candidate_ids),
                        "field": "model.params.bank_id",
                        "component": component.model_dump(mode="json") if hasattr(component, "model_dump") else {"id": component.id}}
        self.hint = ("Inspect the candidates with r3_models_get, then select model.params.bank_id "
                     "on the returned component metadata using r3_put_catalog_component.")
        super().__init__(f"ambiguous bank models for {component.id}; set model.params.bank_id; "
                         f"candidates: {', '.join(self.details['candidate_ids'])}")

    def payload(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": str(self), "data": self.details, "hint": self.hint}
