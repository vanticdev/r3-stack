"""Resolve native part records without collapsing immutable source revisions."""
from __future__ import annotations

from typing import Any, Iterable

from r3_model.pulled import PullError


def part_key(reference: dict[str, Any]) -> tuple[str, str, str, str]:
    return (reference["documentId"], reference["elementId"], reference["partId"],
            reference.get("fullConfiguration") or reference.get("configuration") or "")


class NativePartRecords:
    def __init__(self, records: Iterable[dict[str, Any]]) -> None:
        self._records: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for record in records:
            self._records.setdefault(part_key(record), []).append(record)

    def resolve(self, reference: dict[str, Any]) -> dict[str, Any]:
        key = part_key(reference)
        candidates = self._records.get(key, [])
        # A microversion identifies the source content even when two named
        # versions refer to it. Prefer it over a potentially aliased version.
        for field in ("documentMicroversion", "documentVersion"):
            revision = reference.get(field)
            if revision and any(record.get(field) for record in candidates):
                candidates = [record for record in candidates if record.get(field) == revision]
                break
        if len(candidates) != 1:
            raise PullError("cad_pull_incomplete", "Native part definition is missing or ambiguous for its source revision",
                            {"document_id": key[0], "element_id": key[1], "part_id": key[2],
                             "configuration": key[3], "document_microversion": reference.get("documentMicroversion"),
                             "document_version": reference.get("documentVersion"), "matching_records": len(candidates)})
        return candidates[0]
