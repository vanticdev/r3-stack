"""Sourcing providers, in default search order."""

from __future__ import annotations

from ..http import HttpClient
from .base import Provider, ProviderUnavailable
from .distributors import DigiKeyProvider, MouserProvider, NexarProvider
from .expresslrs import ExpressLrsProvider
from .raspberrypi import RaspberryPiProvider
from .research import AgentResearchProvider

#: id -> provider class. Manufacturer data first, then distributors, then agent research.
PROVIDERS: dict[str, type[Provider]] = {cls.id: cls for cls in (
    RaspberryPiProvider, ExpressLrsProvider, NexarProvider, DigiKeyProvider, MouserProvider, AgentResearchProvider)}


def make_providers(ids: list[str] | None = None, http: HttpClient | None = None) -> list[Provider]:
    """Instantiate providers (default: all), sharing one HTTP client and cache."""
    unknown = [i for i in ids or [] if i not in PROVIDERS]
    if unknown:
        raise KeyError(f"unknown sourcing provider(s) {', '.join(unknown)}; known: {', '.join(PROVIDERS)}")
    http = http or HttpClient()
    return [PROVIDERS[i](http=http) for i in (ids or list(PROVIDERS))]


__all__ = ["PROVIDERS", "AgentResearchProvider", "DigiKeyProvider", "ExpressLrsProvider", "MouserProvider", "NexarProvider",
           "Provider", "ProviderUnavailable", "RaspberryPiProvider", "make_providers"]
