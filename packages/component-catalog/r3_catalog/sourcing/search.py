"""Run providers for one requirement and collect candidates plus per-provider reports."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .candidate import Candidate, ProviderReport
from .http import HttpClient, SourcingHttpError
from .providers import Provider, ProviderUnavailable, make_providers
from .requirements import Requirement

#: ``progress(event, payload)`` receives ``provider_started`` / ``provider_finished`` / ``provider_skipped``.
Progress = Callable[[str, dict[str, Any]], None]


@dataclass
class SearchResult:
    requirement: Requirement
    candidates: list[Candidate] = field(default_factory=list)
    reports: list[ProviderReport] = field(default_factory=list)


def search(requirement: Requirement, providers: list[Provider] | None = None, provider_ids: list[str] | None = None,
           http: HttpClient | None = None, progress: Progress | None = None) -> SearchResult:
    """Query every applicable provider. Unavailable providers (no key, no CLI) and
    provider failures are reported, never raised."""
    ids = provider_ids or requirement.providers or None
    providers = providers if providers is not None else make_providers(ids, http)
    if ids:
        providers = [p for p in providers if p.id in ids]
    result = SearchResult(requirement)
    emit = progress or (lambda event, payload: None)
    for provider in providers:
        if not provider.handles(requirement):
            continue
        started = time.time()
        log_start = len(provider.http.log)
        try:
            provider.check_available()
        except ProviderUnavailable as error:
            report = ProviderReport(provider=provider.id, status="skipped", code=error.code, message=error.message)
            result.reports.append(report)
            emit("provider_skipped", {**report.model_dump(mode="json"), "hint": error.hint})
            continue
        emit("provider_started", {"provider": provider.id, "title": provider.title})
        try:
            found = provider.search(requirement)
        except ProviderUnavailable as error:
            report = ProviderReport(provider=provider.id, status="failed", code=error.code, message=error.message)
        except (SourcingHttpError, ValueError, KeyError, OSError) as error:
            report = ProviderReport(provider=provider.id, status="failed", code="sourcing_provider_failed",
                                    message=f"{type(error).__name__}: {error}")
        else:
            result.candidates.extend(found)
            report = ProviderReport(provider=provider.id, status="ok", candidates=len(found))
        report.duration_s = round(time.time() - started, 3)
        report.requests = provider.http.log[log_start:][:50]
        result.reports.append(report)
        emit("provider_finished" if report.status == "ok" else "provider_failed", report.model_dump(mode="json"))
    return result
