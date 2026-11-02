"""Explicit local-source read boundaries for tools running outside a CLI sandbox."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ReadRule:
    path: Path
    access: Literal["read", "deny"]

    def __post_init__(self) -> None:
        path = Path(self.path).expanduser()
        if not path.is_absolute() or self.access not in ("read", "deny"):
            raise ValueError("Invalid local read scope rule")
        object.__setattr__(self, "path", path.resolve())


@dataclass(frozen=True)
class LocalReadScope:
    rules: tuple[ReadRule, ...]

    def __post_init__(self) -> None:
        if not self.rules or any(not isinstance(rule, ReadRule) for rule in self.rules):
            raise ValueError("Local read scope requires explicit rules")

    @classmethod
    def from_json(cls, raw: str) -> LocalReadScope:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"rules"} or not isinstance(value["rules"], list):
                raise ValueError
            rules = []
            for rule in value["rules"]:
                if (not isinstance(rule, dict) or set(rule) != {"path", "access"}
                        or not isinstance(rule["path"], str) or not rule["path"]):
                    raise ValueError
                rules.append(ReadRule(Path(rule["path"]), rule["access"]))
            return cls(tuple(rules))
        except (ValueError, TypeError, KeyError, OSError, RuntimeError):
            raise ValueError("Invalid configured local read scope; no source opened") from None

    def to_json(self) -> str:
        return json.dumps({"rules": [{"path": str(rule.path), "access": rule.access} for rule in self.rules]})

    def check(self, source: Path) -> Path:
        source = source.expanduser().resolve()
        matches = [(len(rule.path.parts), rule.access == "deny") for rule in self.rules
                   if source.is_relative_to(rule.path)]
        # A more-specific selected project can reopen a denied parent directory.
        # Conflicting equally-specific rules always deny, independent of order.
        if not matches or max(matches)[1]:
            raise ValueError(f"Source is outside the configured local read scope: {source}")
        return source


def local_source(path: Path) -> Path:
    """Check an optional tool-host policy before reading a user-selected file.

    Standalone CLI users retain explicit local-file access when no scope is set.
    Agent MCP servers receive a scope because their file I/O is not performed by
    the provider's sandboxed shell.
    """
    raw = os.environ.get("R3_LOCAL_READ_SCOPE")
    return LocalReadScope.from_json(raw).check(path) if raw is not None else path.expanduser().resolve()
