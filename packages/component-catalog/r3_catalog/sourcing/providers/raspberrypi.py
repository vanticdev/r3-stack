"""Raspberry Pi boards from the official Raspberry Pi Product Information Portal.

The PIP (``pip.raspberrypi.com``) is Raspberry Pi Ltd's public document index:
its ``sitemap.xml`` lists one category per product (e.g.
``categories/892-raspberry-pi-5``), and each category page lists the product
brief, the mechanical drawing and the official STEP models. (The marketing site
``www.raspberrypi.com`` sits behind a bot challenge and is not used.)

From the product brief (PDF, text via pypdf) the provider reads the RAM options,
the power input (``5V/5A``), the list price of each RAM option and the operating
temperature. The mechanical drawing is used to *verify* the Raspberry Pi
B-series form factor (85 x 56 mm board, 58 x 49 mm M2.5 hole pattern 3.5 mm in
from the edge, 2.7 mm holes): every dimension found in the drawing is marked
``datasheet`` with the drawing as its source, anything not found is
``estimated``. One candidate per RAM option; the official STEP (no-graphics
variant) is attached as the vendor 3D model.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Any

from ..candidate import Candidate, ModelFile, Offer, SourcedValue
from ..http import SourcingHttpError
from ..quantities import Equals
from ..requirements import Requirement
from .base import Provider, ProviderUnavailable

PIP = "https://pip.raspberrypi.com"
SITEMAP_URL = f"{PIP}/sitemap.xml"
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_DOC_LINK = re.compile(r'href="(https://pip-assets\.raspberrypi\.com/categories/[^"]+/documents/[^"]+)"')
#: Raspberry Pi B-series board outline and mounting (mm), checked against the drawing.
B_FORM_FACTOR = {"length": 85.0, "width": 56.0, "hole_pitch_x": 58.0, "hole_pitch_y": 49.0, "hole_inset": 3.5, "hole_diameter": 2.7}


@dataclass
class Documents:
    category_url: str
    brief: str | None = None
    drawing: str | None = None
    step: str | None = None
    others: tuple[str, ...] = ()


def _family_slugs(requirement: Requirement) -> list[str]:
    family = requirement.constraints.get("family")
    if isinstance(family, Equals):
        return [option for option in family.options if option.startswith("raspberry-pi")]
    return []


def _file_name(url: str) -> str:
    from urllib.parse import unquote

    return unquote(url.rsplit("/", 1)[-1]).lower()


def classify_documents(category_url: str, html: str, slug: str) -> Documents:
    links = list(dict.fromkeys(_DOC_LINK.findall(html)))
    short = slug.replace("raspberry-pi-", "rpi-")
    docs = Documents(category_url=category_url, others=tuple(links))
    for url in links:
        name = _file_name(url)
        if name.endswith(f"{slug}-product-brief.pdf"):
            docs.brief = url
        elif name.endswith(f"{slug}-mechanical-drawing.pdf"):
            docs.drawing = url
        elif "step" in name and (slug in name or short in name):
            # Prefer the small no-graphics model.
            if docs.step is None or "no graphics" in name:
                docs.step = url
    return docs


def _number_tokens(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def drawing_has(text: str, value: float) -> bool:
    """Is the dimension label ``value`` in the drawing text? pypdf may glue adjacent
    labels into one token (``564729.1``), so a label at the start of a token counts."""
    label = f"{value:g}"
    return any(token == label or token.startswith(label) and not label.endswith(".") for token in _number_tokens(text))


def parse_brief(text: str) -> dict[str, Any]:
    """RAM options (GB), power input (V, A), list prices per option (USD) and operating temperature (C)."""
    out: dict[str, Any] = {}
    options = re.search(r"options for ([^)]*)\)", text)
    if options:
        out["ram_gb"] = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*GB", options.group(1))]
    power = re.search(r"(\d+(?:\.\d+)?)\s*V\s*/\s*(\d+(?:\.\d+)?)\s*A\s+DC power", text)
    if power:
        out["supply_voltage"], out["supply_current"] = float(power.group(1)), float(power.group(2))
    listing = re.search(r"List price(.*?)(?:\n\s*\d+\s+Mean|\nSpecification|\Z)", text, re.S)
    if listing:
        out["list_price"] = {float(gb): float(price) for gb, price in re.findall(r"(\d+(?:\.\d+)?)\s*GB\s*\$\s*(\d+(?:\.\d+)?)", listing.group(1))}
    temperature = re.search(r"Operating temperature\s*(-?\d+)\s*°?\s*C\s*to\s*(-?\d+)\s*°?\s*C", text)
    if temperature:
        out["operating_temperature"] = [float(temperature.group(1)), float(temperature.group(2))]
    processor = re.search(r"Processor\s+(.+?)(?:,|\n)", text)
    if processor:
        out["processor"] = processor.group(1).strip()
    published = re.search(r"Published\s+([A-Z][a-z]+ \d{4})", text)
    if published:
        out["published"] = published.group(1)
    if re.search(r"40-pin GPIO header", text):
        out["gpio_header"] = "40-pin"
    return out


class RaspberryPiProvider(Provider):
    id = "raspberrypi"
    title = "Raspberry Pi Product Information Portal"
    kinds = ("computer",)
    category = "manufacturer"

    def handles(self, requirement: Requirement) -> bool:
        return requirement.kind == "computer" and bool(_family_slugs(requirement))

    def category_url(self, slug: str) -> str:
        sitemap = ElementTree.fromstring(self.http.get(SITEMAP_URL).body)
        pattern = re.compile(rf"/categories/\d+-{re.escape(slug)}$")
        for loc in sitemap.iter(f"{_SITEMAP_NS}loc"):
            if loc.text and pattern.search(loc.text.strip()):
                return loc.text.strip()
        raise ProviderUnavailable("sourcing_product_not_found", f"no PIP category for {slug!r} in {SITEMAP_URL}")

    def search(self, requirement: Requirement) -> list[Candidate]:
        candidates: list[Candidate] = []
        for slug in _family_slugs(requirement):
            try:
                url = self.category_url(slug)
            except ProviderUnavailable:
                continue
            docs = classify_documents(url, self.http.get(url).text, slug)
            candidates.extend(self._candidates(slug, docs))
        return candidates

    def _candidates(self, slug: str, docs: Documents) -> list[Candidate]:
        brief = parse_brief(self.http.document_text(docs.brief).text) if docs.brief else {}
        drawing_text = ""
        if docs.drawing:
            try:
                drawing_text = self.http.document_text(docs.drawing).text
            except SourcingHttpError:
                drawing_text = ""
        family_name = slug.replace("-", " ").title()
        shared: dict[str, SourcedValue] = {
            "family": SourcedValue(value=slug, status="datasheet", source=docs.brief or docs.category_url),
        }
        if "supply_voltage" in brief:
            shared["supply"] = SourcedValue(value=brief["supply_voltage"], status="datasheet", source=docs.brief,
                                            note=f"{brief['supply_voltage']:g}V/{brief['supply_current']:g}A DC via USB-C")
            shared["supply_current"] = SourcedValue(value=brief["supply_current"], status="datasheet", source=docs.brief)
        if "operating_temperature" in brief:
            shared["operating_temperature"] = SourcedValue(value=brief["operating_temperature"], status="datasheet",
                                                           source=docs.brief, note="degrees C")
        if "processor" in brief:
            shared["processor"] = SourcedValue(value=brief["processor"], status="datasheet", source=docs.brief)
        if "gpio_header" in brief:
            shared["gpio_header"] = SourcedValue(value=brief["gpio_header"], status="datasheet", source=docs.brief)
        dims = B_FORM_FACTOR
        for name, key, scale in (("length", "length", 1e-3), ("width", "width", 1e-3)):
            found = bool(drawing_text) and drawing_has(drawing_text, dims[key])
            shared[name] = SourcedValue(value=dims[key] * scale, status="datasheet" if found else "estimated",
                                        source=docs.drawing or docs.category_url,
                                        note="label found in the mechanical drawing" if found else "B-series form factor (not found in drawing)")
        holes_found = bool(drawing_text) and all(drawing_has(drawing_text, dims[k]) for k in ("hole_pitch_x", "hole_pitch_y", "hole_inset"))
        shared["mount_holes"] = SourcedValue(value=[dims["hole_pitch_x"] * 1e-3, dims["hole_pitch_y"] * 1e-3],
                                             status="datasheet" if holes_found else "estimated", source=docs.drawing or docs.category_url,
                                             note=f"M2.5 pattern, {dims['hole_inset']:g} mm from the board edge")
        hole_found = bool(drawing_text) and drawing_has(drawing_text, dims["hole_diameter"])
        shared["mount_hole_diameter"] = SourcedValue(value=dims["hole_diameter"] * 1e-3, status="datasheet" if hole_found else "estimated",
                                                     source=docs.drawing or docs.category_url)
        models = []
        if docs.step:
            models.append(ModelFile(url=docs.step, format="step_zip", description="Official STEP model (no graphics)", source=docs.category_url))
        if docs.drawing:
            models.append(ModelFile(url=docs.drawing, format="pdf_drawing", description="Official mechanical drawing", source=docs.category_url))
        urls = {"product_documents": docs.category_url, **({"product_brief": docs.brief} if docs.brief else {}),
                **({"drawing": docs.drawing} if docs.drawing else {})}
        prices: dict[float, float] = brief.get("list_price", {})
        candidates = []
        for ram in brief.get("ram_gb", []):
            params = dict(shared)
            params["ram"] = SourcedValue(value=ram * 1e9, status="datasheet", source=docs.brief, note=f"{ram:g} GB LPDDR4X")
            offers = []
            if ram in prices:
                offers.append(Offer(vendor="Raspberry Pi Ltd (list price)", url=docs.category_url, price=prices[ram], currency="USD",
                                    status="datasheet", source=docs.brief))
            candidates.append(Candidate(
                provider=self.id, kind="computer", name=f"{family_name} ({ram:g} GB)", manufacturer="Raspberry Pi Ltd",
                mpn=f"{family_name} {ram:g}GB", description=f"{family_name}, {ram:g} GB RAM" + (f", {brief['processor']}" if "processor" in brief else ""),
                params=params, offers=offers, urls=urls, models=models,
                notes=[f"Product brief published {brief.get('published', '?')}; list price excludes tax and shipping; "
                       "stock is held by approved resellers and not reported here."]))
        return candidates
