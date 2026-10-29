"""Runs scripts inside KiCad's own Python (the bundled interpreter with the
``pcbnew`` SWIG module). They are separate processes because the module only
loads in KiCad's interpreter; no GUI is created (the one wx assert about a
missing wxApp is expected and harmless)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .library import KicadInstall, find_install

SCRIPTS = Path(__file__).parent / "kicad_scripts"


@dataclass
class StepResult:
    command: list[str]
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _clean(text: str) -> str:
    noise = ("Fontconfig warning", "create wxApp", "stdpbase.cpp")
    return "\n".join(line for line in text.splitlines() if not any(n in line for n in noise))


def kicad_python(script: str, *args: str, install: KicadInstall | None = None, timeout: float = 900) -> StepResult:
    install = install or find_install()
    if install is None:
        raise FileNotFoundError("KiCad installation not found")
    cmd = [str(install.python), "-I", str(SCRIPTS / script), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **install.env, "LC_ALL": "C"})
    return StepResult(cmd, proc.returncode, _clean(proc.stdout + proc.stderr))
