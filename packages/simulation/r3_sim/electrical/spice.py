"""Cross-check against ngspice.

The solved network (resistors, gmin leakage, Thevenin sources and every
nonlinear element as a SPICE B-source) is exported with element parameters
frozen at the operating point, solved with ``ngspice -b`` (``.op``), and node
voltages are compared with the MNA/Newton solution.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

NGSPICE_CANDIDATES = ("/opt/homebrew/bin/ngspice", "/usr/local/bin/ngspice", "/usr/bin/ngspice")


def find_ngspice() -> str | None:
    for candidate in NGSPICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("ngspice")


def run_ngspice(netlist: str, executable: str | None = None, workdir: Path | None = None) -> dict[str, float]:
    """Run a ``.op`` netlist; return {lowercase node name: voltage}."""
    executable = executable or find_ngspice()
    if executable is None:
        raise FileNotFoundError("ngspice not found")
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="r3_spice_"))
    workdir.mkdir(parents=True, exist_ok=True)
    deck = workdir / "operating_point.cir"
    raw = workdir / "operating_point.raw"
    control = netlist.replace(".op\n.end", f".control\nset filetype=ascii\nop\nwrite {raw.name}\n.endc\n.end")
    deck.write_text(control)
    result = subprocess.run([executable, "-b", deck.name], cwd=workdir, capture_output=True, text=True, timeout=120)
    (workdir / "ngspice.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0 or not raw.exists():
        raise RuntimeError(f"ngspice failed ({result.returncode}): {result.stderr[-2000:]}")
    return parse_ascii_raw(raw.read_text())


def parse_ascii_raw(text: str) -> dict[str, float]:
    names: list[str] = []
    values: list[float] = []
    section = None
    for line in text.splitlines():
        if line.startswith("Variables:"):
            section = "vars"
            continue
        if line.startswith("Values:"):
            section = "values"
            continue
        if section == "vars" and line.strip():
            parts = line.split()
            names.append(parts[1].lower())
        elif section == "values" and line.strip():
            parts = line.split()
            values.append(float(parts[-1]))
    result = {}
    for name, value in zip(names, values):
        match = re.fullmatch(r"v\((.+)\)", name)
        result[match.group(1) if match else name] = value
    return result


def compare_netlist(netlist: str, mapping: dict[str, str], x: np.ndarray, workdir: Path | None = None) -> dict[str, Any]:
    """Solve an exported netlist with ngspice and compare with the MNA solution ``x``."""
    spice = run_ngspice(netlist, workdir=workdir)
    rows = []
    for node, label in mapping.items():
        if node.lower() not in spice:
            continue
        ours = float(x[int(node[1:])])
        theirs = spice[node.lower()]
        rows.append({"node": label, "spice_node": node, "mna": ours, "ngspice": theirs, "difference": ours - theirs})
    return {"netlist": netlist, "nodes": rows, "workdir": str(workdir) if workdir else None}


def compare_with_ngspice(system: Any, workdir: Path | None = None) -> dict[str, Any]:
    """Compare the system's last solved step with ngspice."""
    netlist, mapping = system.to_spice(system.x)
    return compare_netlist(netlist, mapping, system.x, workdir)
