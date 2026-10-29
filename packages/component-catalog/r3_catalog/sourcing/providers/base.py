"""Provider interface: ``search(requirement) -> list[Candidate]``."""

from __future__ import annotations

import os
from typing import ClassVar

from ..candidate import Candidate
from ..http import HttpClient
from ..requirements import Requirement


class ProviderUnavailable(RuntimeError):
    """The provider cannot run here (missing API key, CLI or optional package).

    ``code`` is a stable diagnostic code, e.g. ``sourcing_provider_key_missing``."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


class Provider:
    #: Stable id used in ``source.providers``, ``--provider`` and lock entries.
    id: ClassVar[str] = ""
    title: ClassVar[str] = ""
    #: Catalog kinds this provider can return; empty means any kind.
    kinds: ClassVar[tuple[str, ...]] = ()
    #: Environment variables that must be set (API keys).
    env: ClassVar[tuple[str, ...]] = ()
    #: ``distributor``, ``manufacturer`` or ``research``.
    category: ClassVar[str] = ""

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    def handles(self, requirement: Requirement) -> bool:
        return not self.kinds or requirement.kind in self.kinds

    def check_available(self) -> None:
        """Raise :class:`ProviderUnavailable` when the provider cannot run."""
        missing = [name for name in self.env if not os.environ.get(name)]
        if missing:
            raise ProviderUnavailable(
                "sourcing_provider_key_missing", f"{self.title}: {', '.join(missing)} not set; provider skipped",
                hint=f"Set {', '.join(missing)} in the environment (server or CI secret) to enable {self.title}.")

    def search(self, requirement: Requirement) -> list[Candidate]:
        raise NotImplementedError

    def describe(self) -> dict[str, object]:
        try:
            self.check_available()
            available, reason = True, ""
        except ProviderUnavailable as error:
            available, reason = False, error.message
        return {"id": self.id, "title": self.title, "category": self.category, "kinds": list(self.kinds),
                "env": list(self.env), "available": available, "reason": reason}
