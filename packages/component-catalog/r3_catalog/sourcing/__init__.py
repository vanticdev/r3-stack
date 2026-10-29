"""Spec-based part sourcing: find real purchasable parts for ``source:`` slots.

A component in ``robot.yaml`` may declare ``source: {requires, prefer, quantity,
budget}``. Providers turn the requirement into :class:`Candidate` parts with a
provenance URL and a ``datasheet|vendor|estimated`` status per parameter,
offers (price, stock, lead time) and vendor 3D models. :func:`ranking.rank`
applies the hard filters and scores the rest (the orchestrator supplies the
digital-twin checks); :func:`build.build_entry` turns the chosen candidate into
a project catalog entry, and the choice is pinned in ``r3.lock.yaml``.

Providers (``providers/``): Raspberry Pi PIP (keyless), ExpressLRS targets
(keyless), Nexar/Octopart, DigiKey v4 and Mouser (API keys from the
environment), and agent web research (Claude Code / Codex CLI).
"""

from __future__ import annotations

from .build import build_entry, entry_id, file_name, render_file, select_template, substituted_catalog
from .candidate import Candidate, ModelFile, Offer, ProviderReport, SourcedValue
from .http import HttpClient, NetworkDisabled, RobotsDisallowed, SourcingHttpError
from .providers import PROVIDERS, Provider, ProviderUnavailable, make_providers
from .quantities import Money, parse_constraint, parse_money, parse_value
from .ranking import Ranked, TwinCheck, rank
from .requirements import ParamMatch, Requirement, catalog_params
from .search import SearchResult, search

__all__ = [
    "PROVIDERS", "Candidate", "HttpClient", "ModelFile", "Money", "NetworkDisabled", "Offer", "ParamMatch", "Provider",
    "ProviderReport", "ProviderUnavailable", "Ranked", "Requirement", "RobotsDisallowed", "SearchResult", "SourcedValue",
    "SourcingHttpError", "TwinCheck", "build_entry", "catalog_params", "entry_id", "file_name", "make_providers",
    "parse_constraint", "parse_money", "parse_value", "rank", "render_file", "search", "select_template", "substituted_catalog",
]
