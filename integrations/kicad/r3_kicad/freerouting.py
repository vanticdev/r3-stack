"""Freerouting in CLI mode, as people use it with KiCad: pcbnew exports a
Specctra DSN, Freerouting autoroutes it, and pcbnew imports the Specctra session
(SES) and refills the zones. The export/import run in KiCad's own Python.

Freerouting runs from its release jar on a Java runtime new enough for it
(2.5.0 needs Java 25), always with ``-Djava.awt.headless=true`` and
``--gui.enabled=false``: no window can open. Its native ``freerouting-cli``
build is not used (2.5.0's GraalVM image fails with a serialization error).
The jar and runtime live in ``~/.cache/r3`` (``R3_FREEROUTING_JAR`` and
``R3_JAVA`` override them); the R3 worker image installs the same pair.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .engine import StepResult, kicad_python

CACHE = Path.home() / ".cache/r3"
MAIN_CLASS = "app/freerouting/Freerouting.class"


@dataclass(frozen=True)
class Router:
    java: Path
    jar: Path
    version: str
    java_version: int = 0


def _class_major(jar: Path) -> int:
    """Class-file major version of Freerouting's main class (65 = Java 21, 69 = Java 25)."""
    with zipfile.ZipFile(jar) as zf:
        head = zf.read(MAIN_CLASS)[:8]
    return int.from_bytes(head[6:8], "big")


def _java_feature(java: Path) -> int:
    out = subprocess.run([str(java), "-version"], capture_output=True, text=True).stderr
    match = re.search(r'version "(\d+)', out)
    return int(match.group(1)) if match else 0


def _javas() -> list[Path]:
    found = [Path(os.environ["R3_JAVA"])] if os.environ.get("R3_JAVA") else []
    found += sorted(CACHE.glob("java/*/Contents/Home/bin/java"), reverse=True)    # macOS JRE bundles
    found += sorted(CACHE.glob("java/*/bin/java"), reverse=True)                   # Linux JREs
    system = shutil.which("java")
    if system:
        found.append(Path(system))
    return [j for j in found if j.exists()]


def _version(jar: Path) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+)*)", jar.stem)
    return tuple(int(x) for x in match.group(1).split(".")) if match else (0,)


def find_router() -> Router | None:
    """Newest Freerouting jar that one of the available Java runtimes can run."""
    jars = [Path(os.environ["R3_FREEROUTING_JAR"])] if os.environ.get("R3_FREEROUTING_JAR") else []
    jars += sorted((CACHE / "freerouting").glob("freerouting-*.jar"), key=_version, reverse=True)
    javas = [(j, _java_feature(j)) for j in _javas()]
    for jar in jars:
        needed = _class_major(jar) - 44
        for java, feature in sorted(javas, key=lambda x: -x[1]):
            if feature >= needed:
                return Router(java=java, jar=jar, version=".".join(map(str, _version(jar))), java_version=feature)
    return None


def export_dsn(pcb: Path, dsn: Path) -> StepResult:
    return kicad_python("specctra.py", "export", str(pcb), str(dsn))


def import_ses(pcb: Path, ses: Path, fill: bool = True) -> StepResult:
    return kicad_python("specctra.py", "import", str(pcb), str(ses), *(["--fill"] if fill else []))


def unroute(pcb: Path, nets: set[str]) -> StepResult:
    return kicad_python("specctra.py", "unroute", str(pcb), ",".join(sorted(nets)))


def route(dsn: Path, ses: Path, passes: int = 100, timeout: float = 3600) -> StepResult:
    router = find_router()
    if router is None:
        raise FileNotFoundError("Freerouting not found: put freerouting-<version>.jar in ~/.cache/r3/freerouting "
                                "and a matching Java runtime in ~/.cache/r3/java (or set R3_FREEROUTING_JAR / R3_JAVA)")
    cmd = [str(router.java), "-Djava.awt.headless=true", "-jar", str(router.jar), "-de", str(dsn), "-do", str(ses),
           f"--router.autorouter.max_passes={passes}", "--gui.enabled=false",
           # Pours are planes the router may cross on other nets; no automatic neck-down below the board's minimum.
           "--router.plane_as_obstacle=false", "--router.automatic_neckdown=false", "--router.neck_width_um=150",
           "--usage_and_diagnostic_data.disable_analytics=true"]
    if ses.exists():
        ses.unlink()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(dsn.parent))
    return StepResult(cmd, proc.returncode if ses.exists() else (proc.returncode or 1), proc.stdout + proc.stderr)


def unrouted(log: str) -> int | None:
    """Unrouted connection count of Freerouting's final routing summary."""
    hits = re.findall(r"final score: [\d.]+ \((\d+) unrouted", log)
    if hits:
        return int(hits[-1])
    return 0 if "fully routed" in log.lower() else None
