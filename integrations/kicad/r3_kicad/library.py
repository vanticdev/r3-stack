"""KiCad installation discovery and read-only access to the official libraries.

Everything here runs without a display. On macOS the KiCad app bundle provides
``kicad-cli``, the bundled Python with the ``pcbnew`` module, and the official
symbol/footprint/3D libraries; nothing in the bundle is launched except
``kicad-cli`` and that Python interpreter. On a Linux server the same pieces
come from the KiCad PPA or the ``kicad/kicad`` image: ``/usr/bin/kicad-cli``,
``python3`` with the ``python3-pcbnew`` module, and ``/usr/share/kicad/*``.

Symbol pins are read through ``kicad-sch-api``'s library cache (a structured
KiCad-format parser), so the schematic layout knows where each pin's
connection point lands before the symbol is placed.
"""

from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MAC_APPS = (Path.home() / "Applications/KiCad/KiCad.app", Path("/Applications/KiCad/KiCad.app"))
LINUX_SHARE = Path("/usr/share/kicad")


@dataclass(frozen=True)
class KicadInstall:
    cli: Path
    python: Path               # interpreter that can ``import pcbnew``
    symbols: Path
    footprints: Path
    models_3d: Path
    app: Path | None = None    # macOS bundle (never launched; only its CLI and Python are used)

    @property
    def env(self) -> dict[str, str]:
        """KiCad's own library path variables, for kicad-cli and the pcbnew module."""
        return {"KICAD10_SYMBOL_DIR": str(self.symbols), "KICAD10_FOOTPRINT_DIR": str(self.footprints),
                "KICAD10_3DMODEL_DIR": str(self.models_3d), "KICAD_SYMBOL_DIR": str(self.symbols)}


def _mac_install(app: Path) -> KicadInstall | None:
    if not (app / "Contents/MacOS/kicad-cli").exists():
        return None
    shared = app / "Contents/SharedSupport"
    python = app / "Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
    return KicadInstall(cli=app / "Contents/MacOS/kicad-cli", python=python, symbols=shared / "symbols",
                        footprints=shared / "footprints", models_3d=shared / "3dmodels", app=app)


def _linux_install() -> KicadInstall | None:
    cli = os.environ.get("R3_KICAD_CLI") or shutil.which("kicad-cli")
    share = Path(os.environ.get("R3_KICAD_SHARE", LINUX_SHARE))
    if not cli or not (share / "symbols").exists():
        return None
    python = Path(os.environ.get("R3_KICAD_PYTHON") or shutil.which("python3") or "/usr/bin/python3")
    return KicadInstall(cli=Path(cli), python=python, symbols=share / "symbols", footprints=share / "footprints",
                        models_3d=share / "3dmodels")


@lru_cache(maxsize=None)
def find_install() -> KicadInstall | None:
    """``R3_KICAD_APP`` (macOS bundle) wins, then ~/Applications, /Applications, then a Linux install."""
    override = os.environ.get("R3_KICAD_APP")
    candidates = ([Path(override)] if override else []) + list(MAC_APPS)
    for app in candidates:
        install = _mac_install(app)
        if install is not None:
            return install
    return _linux_install()


@dataclass(frozen=True)
class LibPin:
    number: str
    name: str
    etype: str              # power_in, passive, no_connect, ...
    x: float                # connection point, library units (mm, y up)
    y: float
    angle: float            # direction from the connection point toward the body (deg)
    length: float


@dataclass(frozen=True)
class LibSymbol:
    lib_id: str
    pins: tuple[LibPin, ...]
    footprint: str
    power: bool

    def pin(self, number: str) -> LibPin:
        for p in self.pins:
            if p.number == number:
                return p
        raise KeyError(f"{self.lib_id} has no pin {number}")


def symbol_cache():
    """kicad-sch-api's library cache pointed at the official KiCad symbol libraries."""
    install = find_install()
    if install is None:
        raise FileNotFoundError("KiCad installation not found (set R3_KICAD_APP or install kicad-cli)")
    os.environ.setdefault("KICAD_SYMBOL_DIR", str(install.symbols))
    from kicad_sch_api.library.cache import get_symbol_cache

    return get_symbol_cache()


def _raw_property(raw: list, name: str) -> str:
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, list) and item and str(item[0]) == "property" and len(item) > 2 and item[1] == name:
            return str(item[2])
    return ""


def _is_power(raw: list) -> bool:
    return any(isinstance(item, list) and item and str(item[0]) == "power" for item in (raw or []))


@lru_cache(maxsize=None)
def load_symbol(lib_id: str) -> LibSymbol:
    definition = symbol_cache().get_symbol(lib_id)
    if definition is None:
        raise KeyError(f"{lib_id} not in the installed KiCad library")
    pins = tuple(LibPin(number=p.number, name=p.name, etype=p.pin_type.value, x=p.position.x, y=p.position.y,
                        angle=p.rotation, length=p.length) for p in definition.pins)
    raw = definition.raw_kicad_data
    return LibSymbol(lib_id=lib_id, pins=pins, footprint=_raw_property(raw, "Footprint"),
                     power=_is_power(raw) or lib_id.startswith("power:"))


def footprint_exists(footprint: str) -> bool:
    install = find_install()
    if install is None or ":" not in footprint:
        return False
    lib, name = footprint.split(":", 1)
    return (install.footprints / f"{lib}.pretty" / f"{name}.kicad_mod").exists()


def pin_position(pin: LibPin, at: tuple[float, float], rotation: float, mirror_x: bool = False) -> tuple[float, float]:
    """Schematic position (mm, y down) of a pin's connection point for a symbol
    instance at ``at`` rotated ``rotation`` degrees counter-clockwise on screen."""
    vx, vy = pin.x, -pin.y                    # library y up -> schematic y down
    if mirror_x:
        vy = -vy
    a = math.radians(rotation)
    # Counter-clockwise on screen with y down: (x, y) -> (x cos a + y sin a, -x sin a + y cos a).
    rx = vx * math.cos(a) + vy * math.sin(a)
    ry = -vx * math.sin(a) + vy * math.cos(a)
    return (round(at[0] + rx, 4), round(at[1] + ry, 4))


def pin_outward(pin: LibPin, rotation: float) -> float:
    """Screen direction (deg, 0 = +x, 90 = up) pointing away from the symbol body."""
    # Library angle points from the connection point into the body; outward is opposite.
    return (pin.angle + 180.0 + rotation) % 360.0
