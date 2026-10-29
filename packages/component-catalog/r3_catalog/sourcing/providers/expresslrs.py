"""ExpressLRS receivers from the official hardware target definitions.

Source: the ``ExpressLRS/targets`` repository on GitHub (the data the ExpressLRS
Configurator uses): ``targets.json`` lists every approved device per
manufacturer and category (``rx_2400``, ``rx_900``, ``rx_dual``, ...), and
``RX/<layout_file>`` gives its hardware layout: UART pins (``serial_rx`` /
``serial_tx``), radio power table (``power_min``/``power_max`` index into the
ELRS power levels 10, 25, 50, 100, 250, 500, 1000, 2000 mW), PWM outputs and
diversity. A device ``overlay`` overrides layout fields.

Values read from these files are ``datasheet`` (official hardware definition).
ExpressLRS receivers speak CRSF on their serial port; that, too, is official
(ExpressLRS documentation). The definitions carry no supply voltage, size, mass
or price: supply is reported as an explicit ``estimated`` 5 V +-10 % convention,
and size/mass are left to the slot template (also ``estimated``). Receivers
integrated into flight controllers or VTX combos (``FC``, ``AIO``, ``VTX``) are skipped.
"""

from __future__ import annotations

import math
import re
import urllib.parse
from typing import Any

from ..candidate import Candidate, SourcedValue
from ..http import SourcingHttpError
from ..requirements import Requirement
from .base import Provider

REPO_RAW = "https://raw.githubusercontent.com/ExpressLRS/targets/master"
TARGETS_URL = f"{REPO_RAW}/targets.json"
REPO_URL = "https://github.com/ExpressLRS/targets"
SERIAL_DOCS = "https://www.expresslrs.org/software/serial-protocols/"
#: ELRS PowerLevels_e: index -> mW.
POWER_LEVELS_MW = (10, 25, 50, 100, 250, 500, 1000, 2000)
#: Category -> ISM band edges (Hz) the receivers operate in.
#: Receivers built into flight controllers or VTX combos are not standalone parts.
INTEGRATED = re.compile(r"\bFC\b|integrated|\bAIO\b|\bVTX\b|\bHalo\b|-fc$|^aio", re.I)
BANDS = {"rx_2400": [2.400e9, 2.4835e9], "rx_900": [0.863e9, 0.928e9], "rx_dual": [2.400e9, 2.4835e9]}


def _categories(requirement: Requirement) -> list[str]:
    band = requirement.constraints.get("band")
    if band is None:
        return list(BANDS)
    chosen = [cat for cat, edges in BANDS.items() if band.check(edges)[0] == "pass"]
    return chosen


def layout_url(layout_file: str) -> str:
    return f"{REPO_RAW}/RX/{urllib.parse.quote(layout_file)}"


class ExpressLrsProvider(Provider):
    id = "expresslrs"
    title = "ExpressLRS hardware targets"
    kinds = ("radio",)
    category = "manufacturer"

    def handles(self, requirement: Requirement) -> bool:
        if requirement.kind != "radio":
            return False
        protocol = requirement.constraints.get("protocol")
        return protocol is None or protocol.check(["crsf", "expresslrs", "elrs"])[0] == "pass"

    def search(self, requirement: Requirement) -> list[Candidate]:
        targets = self.http.get(TARGETS_URL).json()
        categories = _categories(requirement)
        layouts: dict[str, dict[str, Any] | None] = {}
        candidates: list[Candidate] = []
        for vendor_key, vendor in sorted(targets.items()):
            manufacturer = vendor.get("name", vendor_key)
            for category in categories:
                for device_key, device in sorted((vendor.get(category) or {}).items()):
                    if INTEGRATED.search(device.get("product_name", "")) or INTEGRATED.search(device_key):
                        continue
                    layout_file = device.get("layout_file")
                    if layout_file and layout_file not in layouts:
                        try:
                            layouts[layout_file] = self.http.get(layout_url(layout_file)).json()
                        except (SourcingHttpError, ValueError):
                            layouts[layout_file] = None
                    layout = {**(layouts.get(layout_file) or {}), **(device.get("overlay") or {})}
                    candidates.append(self._candidate(vendor_key, manufacturer, category, device_key, device, layout_file, layout))
        return candidates

    def _candidate(self, vendor_key: str, manufacturer: str, category: str, device_key: str, device: dict[str, Any],
                   layout_file: str | None, layout: dict[str, Any]) -> Candidate:
        layout_src = layout_url(layout_file) if layout_file else TARGETS_URL
        target_src = f"{TARGETS_URL}#{vendor_key}.{category}.{device_key}"
        params: dict[str, SourcedValue] = {
            "band": SourcedValue(value=BANDS[category], status="datasheet", source=target_src,
                                 note=f"category {category}" + (" (dual-band: also 868/915 MHz)" if category == "rx_dual" else "")),
            "platform": SourcedValue(value=device.get("platform", ""), status="datasheet", source=target_src),
            "firmware": SourcedValue(value=device.get("firmware", ""), status="datasheet", source=target_src),
        }
        has_uart = "serial_rx" in layout and "serial_tx" in layout
        params["uart"] = SourcedValue(value=has_uart, status="datasheet", source=layout_src,
                                      note="serial_rx/serial_tx pins defined" if has_uart else "no serial pins in layout")
        if has_uart:
            params["protocol"] = SourcedValue(value=["crsf"], status="datasheet", source=SERIAL_DOCS,
                                              note=f"ELRS receivers output CRSF on serial_tx (layout {layout_file})")
        else:
            params["protocol"] = SourcedValue(value=["pwm"], status="datasheet", source=layout_src,
                                              note="no serial_rx/serial_tx in the layout: PWM outputs only (ELRS 3.x can remap "
                                                   "a PWM pin to serial; not modeled)")
        if "power_max" in layout:
            index = max(0, min(int(layout["power_max"]), len(POWER_LEVELS_MW) - 1))
            milliwatts = POWER_LEVELS_MW[index]
            params["telemetry_power"] = SourcedValue(value=round(10 * math.log10(milliwatts), 2), status="datasheet",
                                                     source=layout_src, note=f"power_max index {index} = {milliwatts} mW (dBm)")
        pwm = layout.get("pwm_outputs")
        params["pwm_outputs"] = SourcedValue(value=len(pwm) if isinstance(pwm, list) else 0, status="datasheet", source=layout_src)
        diversity = "radio_nss_2" in layout or "radio_busy_2" in layout
        antenna_switch = "ant_ctrl" in layout or "ant_ctrl_compl" in layout
        params["diversity"] = SourcedValue(value="true" if diversity else "antenna" if antenna_switch else "none",
                                           status="datasheet", source=layout_src)
        params["supply"] = SourcedValue(value=[4.5, 5.5], status="estimated", source=REPO_URL,
                                        note="not in the ELRS target definition; assumed 5 V +-10 % (BEC-powered receiver)")
        name = device.get("product_name") or f"{manufacturer} {device_key}"
        return Candidate(provider=self.id, kind="radio", name=name, manufacturer=manufacturer,
                         mpn=device.get("lua_name") or device_key, description=f"ExpressLRS {category} receiver ({device.get('platform', '')})",
                         params=params, urls={"target_definition": target_src, "layout": layout_src, "repository": REPO_URL},
                         notes=["Price, stock, size and mass are not part of the ELRS target definitions."])
