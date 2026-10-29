"""Structured, machine-actionable results shared by every pipeline stage.

Agents act on diagnostics, not screenshots: every failure carries a stable
``code``, the identities it concerns, and the numbers that justify it.
"""

from __future__ import annotations

import enum
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Diagnostic(BaseModel):
    """One finding. ``code`` is a stable snake_case identifier, e.g.
    ``wire_clearance_violation`` or ``connector_overcurrent``."""

    code: str
    severity: Severity
    message: str
    subjects: list[str] = Field(default_factory=list, description="Component, connector, wire, net or part ids.")
    data: dict[str, Any] = Field(default_factory=dict, description="Measured values, limits, positions (SI units).")
    stage: str = ""
    hint: str = ""

    @classmethod
    def error(cls, code: str, message: str, **kwargs: Any) -> "Diagnostic":
        return cls(code=code, severity=Severity.ERROR, message=message, **kwargs)

    @classmethod
    def warning(cls, code: str, message: str, **kwargs: Any) -> "Diagnostic":
        return cls(code=code, severity=Severity.WARNING, message=message, **kwargs)

    @classmethod
    def info(cls, code: str, message: str, **kwargs: Any) -> "Diagnostic":
        return cls(code=code, severity=Severity.INFO, message=message, **kwargs)


class Artifact(BaseModel):
    """A generated file produced by a stage, relative to the project build dir."""

    path: str
    kind: str = Field(description="e.g. kicad_pcb, gerber_zip, step, glb, mjcf, plot_png, log, json")
    description: str = ""


class Assumption(BaseModel):
    """A modeling assumption that limits what a passing result proves."""

    domain: str
    statement: str


class StageResult(BaseModel):
    stage: str
    ok: bool = True
    started_at: float = Field(default_factory=time.time)
    duration_s: float = 0.0
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict, description="Input revision fingerprints.")

    def add(self, diagnostic: Diagnostic) -> Diagnostic:
        if not diagnostic.stage:
            diagnostic.stage = self.stage
        self.diagnostics.append(diagnostic)
        if diagnostic.severity is Severity.ERROR:
            self.ok = False
        return diagnostic

    def extend(self, diagnostics: list[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            self.add(diagnostic)

    def assume(self, domain: str, statement: str) -> None:
        self.assumptions.append(Assumption(domain=domain, statement=statement))

    def artifact(self, path: str | Path, kind: str, description: str = "") -> None:
        self.artifacts.append(Artifact(path=str(path), kind=kind, description=description))

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is Severity.ERROR]

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(mode="json"), indent=2))
