"""Thin wrapper around ``kicad-cli`` (ERC, DRC, exports, renders)."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .library import KicadInstall, find_install


@dataclass
class Run:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _clean(stderr: str) -> str:
    # Homebrew fontconfig emits harmless warnings on every run.
    return "\n".join(line for line in stderr.splitlines() if not line.startswith("Fontconfig warning"))


@dataclass
class KicadCli:
    install: KicadInstall
    log: list[Run] = field(default_factory=list)

    @classmethod
    def find(cls) -> "KicadCli | None":
        install = find_install()
        return cls(install) if install else None

    def run(self, *args: str, timeout: float = 600) -> Run:
        cmd = [str(self.install.cli), *args]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, **self.install.env, "LC_ALL": "C"})
        run = Run(cmd, proc.returncode, proc.stdout, _clean(proc.stderr))
        self.log.append(run)
        return run

    def version(self) -> str:
        return self.run("version").stdout.strip()

    # ------------------------------------------------------------------ checks
    def erc(self, sch: Path, out: Path) -> Run:
        return self.run("sch", "erc", "--format", "json", "--severity-all", "--units", "mm", "-o", str(out), str(sch))

    def drc(self, pcb: Path, out: Path, parity: bool = True, refill: bool = True) -> Run:
        args = ["pcb", "drc", "--format", "json", "--severity-all", "--units", "mm", "-o", str(out)]
        if parity:
            args.append("--schematic-parity")
        if refill:
            args.append("--refill-zones")
        return self.run(*args, str(pcb))

    # ------------------------------------------------------------------ exports
    def netlist(self, sch: Path, out: Path, fmt: str = "kicadxml") -> Run:
        return self.run("sch", "export", "netlist", "--format", fmt, "-o", str(out), str(sch))

    def bom(self, sch: Path, out: Path) -> Run:
        return self.run("sch", "export", "bom", "--fields", "Reference,Value,Footprint,${QUANTITY},${DNP}",
                        "--labels", "Refs,Value,Footprint,Qty,DNP", "--group-by", "Value,Footprint", "-o", str(out), str(sch))

    def sch_pdf(self, sch: Path, out: Path) -> Run:
        return self.run("sch", "export", "pdf", "-o", str(out), str(sch))

    def sch_svg(self, sch: Path, out_dir: Path) -> Run:
        return self.run("sch", "export", "svg", "-o", str(out_dir), str(sch))

    def gerbers(self, pcb: Path, out_dir: Path) -> Run:
        out_dir.mkdir(parents=True, exist_ok=True)
        layers = "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"
        return self.run("pcb", "export", "gerbers", "--layers", layers, "--no-protel-ext", "--subtract-soldermask",
                        "-o", str(out_dir) + "/", str(pcb))

    def drill(self, pcb: Path, out_dir: Path) -> Run:
        out_dir.mkdir(parents=True, exist_ok=True)
        return self.run("pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th", "--generate-map",
                        "--map-format", "gerberx2", "-o", str(out_dir) + "/", str(pcb))

    def pos(self, pcb: Path, out: Path) -> Run:
        return self.run("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
                        "--exclude-dnp", "-o", str(out), str(pcb))

    def step(self, pcb: Path, out: Path) -> Run:
        return self.run("pcb", "export", "step", "--force", "--subst-models", "--include-tracks", "--include-zones",
                        "-o", str(out), str(pcb), timeout=1200)

    def render(self, pcb: Path, out: Path, side: str, width: int = 1600, height: int = 1200) -> Run:
        return self.run("pcb", "render", "--side", side, "--width", str(width), "--height", str(height),
                        "--quality", "high", "--background", "opaque", "-o", str(out), str(pcb), timeout=900)

    def pcb_svg(self, pcb: Path, out: Path, layers: str, mirror: bool = False) -> Run:
        args = ["pcb", "export", "svg", "--layers", layers, "--page-size-mode", "2", "--exclude-drawing-sheet",
                "--mode-single", "-o", str(out)]
        if mirror:
            args.append("--mirror")
        return self.run(*args, str(pcb))


def report_counts(report: Path) -> dict[str, int]:
    """Error/warning counts of a kicad-cli ERC or DRC JSON report."""
    data = json.loads(Path(report).read_text())
    counts = {"errors": 0, "warnings": 0, "exclusions": 0}

    def tally(items: list[dict]) -> None:
        for item in items:
            if item.get("excluded"):
                counts["exclusions"] += 1
            elif item.get("severity") == "error":
                counts["errors"] += 1
            elif item.get("severity") == "warning":
                counts["warnings"] += 1

    if "sheets" in data:  # ERC
        for sheet in data["sheets"]:
            tally(sheet.get("violations", []))
    else:  # DRC
        tally(data.get("violations", []))
        counts["unconnected"] = len(data.get("unconnected_items", []))
        counts["parity"] = len(data.get("schematic_parity", []))
        tally(data.get("unconnected_items", []))
        tally(data.get("schematic_parity", []))
    return counts


def report_items(report: Path) -> list[dict]:
    data = json.loads(Path(report).read_text())
    if "sheets" in data:
        return [v for sheet in data["sheets"] for v in sheet.get("violations", [])]
    return data.get("violations", []) + data.get("unconnected_items", []) + data.get("schematic_parity", [])
