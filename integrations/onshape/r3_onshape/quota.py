"""Quota failures stop an operation immediately, including its cleanup calls."""
from __future__ import annotations

import re
from typing import Any

from r3_model.diagnostics import Diagnostic, StageResult

from .client import OnshapeError


def raise_if_quota(error: Exception) -> None:
    if getattr(error, "status", None) == 429:
        raise error
    if re.search(r"\b429\b", str(error)):
        raise OnshapeError(429, str(error)) from error


def quota_diagnostic(error: OnshapeError, result: StageResult) -> None:
    result.add(Diagnostic.error(
        "onshape_quota_blocked", str(error), subjects=["cad"],
        data={"status": 429, "retry_after": error.retry_after, "reset_at": error.reset_at},
        hint=f"Resume after {error.reset_at}." if error.reset_at else
             "Onshape did not supply a reset time; consult the quota response before resuming."))


def update_features(api: Any, base: str, features: list[dict[str, Any]]) -> Any:
    """Batch parameter/suppression updates to *existing* features in one API call.

    Onshape's updateFeatures endpoint cannot create features or rename them. New
    sketches/solids must use addPartStudioFeature; do not pretend those can batch.
    https://onshape-public.github.io/docs/api-adv/featureaccess/
    """
    if not features:
        return None
    if any(not feature.get("featureId") for feature in features):
        raise ValueError("batch updates require existing feature IDs")
    suppression = ["suppressed" in feature for feature in features]
    if any(suppression) and not all(suppression):
        raise ValueError("suppression batches must specify every feature state; split parameter-only updates into a separate batch")
    return api.json("POST", base + "/features/updates", body={
        "btType": "BTUpdateFeaturesCall-1748", "features": [{"btType": "BTMFeature-134", **feature} for feature in features],
        "updateSuppressionAttributes": all(suppression)})
