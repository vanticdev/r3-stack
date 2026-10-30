"""One import environment for the web server's agents, MCP and doctor probes."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
from pathlib import Path
import tomllib


def r3_pythonpath() -> str:
    """Derive source roots from the distribution manifest, never a package allowlist.

    Editable checkouts use setuptools' own search roots. Installed wheels use
    the distribution's top-level package metadata. Existing entries are made
    absolute before a session changes its working directory.
    """
    roots: list[str] = []
    for parent in Path(__file__).resolve().parents:
        manifest = parent / "pyproject.toml"
        if not manifest.is_file():
            continue
        config = tomllib.loads(manifest.read_text())
        if config.get("project", {}).get("name") != "r3-platform":
            continue
        roots.extend(str((parent / relative).resolve()) for relative in
                     config["tool"]["setuptools"]["packages"]["find"]["where"])
        break
    else:
        distribution = importlib.metadata.distribution("r3-platform")
        for package in (distribution.read_text("top_level.txt") or "").splitlines():
            spec = importlib.util.find_spec(package)
            if spec and spec.origin:
                roots.append(str(Path(spec.origin).resolve().parents[1]))
    roots.extend(str(Path(entry).resolve()) for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry)
    return os.pathsep.join(dict.fromkeys(roots))
