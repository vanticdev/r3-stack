"""Electronic distributors through their official APIs (keys from the environment).

* **Nexar / Octopart** (GraphQL, ``https://api.nexar.com/graphql``): OAuth2 client
  credentials from ``NEXAR_CLIENT_ID`` / ``NEXAR_CLIENT_SECRET`` at
  ``https://identity.nexar.com/connect/token``; ``supSearch`` (keyword) and
  ``supSearchMpn`` (BOM pricing) with specs, sellers, offers and prices.
* **DigiKey Product Information API v4**: client credentials from
  ``DIGIKEY_CLIENT_ID`` / ``DIGIKEY_CLIENT_SECRET`` at
  ``https://api.digikey.com/v1/oauth2/token``; ``POST /products/v4/search/keyword``
  and ``GET /products/v4/search/{mpn}/productdetails``.
* **Mouser Search API v1**: ``MOUSER_API_KEY``; ``POST /api/v1/search/keyword`` and
  ``POST /api/v1/search/partnumber``.

Without keys a provider raises :class:`ProviderUnavailable` (skipped with a
``sourcing_provider_key_missing`` diagnostic). Parametric values from a
distributor listing are ``vendor`` provenance. Required string parameters the
listing has no attribute for (``family: raspberry-pi-5``) are matched against
the listing's description and MPN, also as ``vendor`` with a note.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Iterable

from ..candidate import Candidate, Offer, SourcedValue
from ..http import SourcingHttpError
from ..quantities import Equals, normalize, parse_money, parse_value
from ..requirements import Requirement
from .base import Provider, ProviderUnavailable

#: Distributor parametric attribute names -> R3 parameter names.
ATTRIBUTES = {
    "supply voltage": "supply", "voltage - supply": "supply", "operating supply voltage": "supply", "input voltage": "supply",
    "frequency": "band", "frequency range": "band", "rf family/standard": "protocol", "protocol": "protocol",
    "weight": "mass", "length": "length", "width": "width", "height": "height", "height - seated (max)": "height",
    "size / dimension": "size", "memory size": "ram", "ram size": "ram", "ram": "ram", "diameter": "diameter",
    "current - supply": "supply_current", "supply current": "supply_current", "operating temperature": "operating_temperature",
    "interface": "interface", "core processor": "processor", "series": "series",
}
#: Kind -> extra search word, so a keyword query stays in the right category.
KIND_WORDS = {"radio": "receiver", "computer": "", "sensor": "", "regulator": "regulator", "battery": "battery", "motor": "motor",
              "servo": "servo", "esc": "esc", "wheel": "wheel", "fuse": "fuse", "antenna": "antenna"}


def search_query(requirement: Requirement) -> str:
    words = [KIND_WORDS.get(requirement.kind, requirement.kind)]
    for name, constraint in requirement.constraints.items():
        if isinstance(constraint, Equals) and len(constraint.options) == 1 and constraint.options[0] not in ("true", "false"):
            words.append(constraint.options[0].replace("-", " "))
        elif name == "band":
            words.append(constraint.text.replace(" ", ""))
        elif name == "ram":
            words.append(constraint.text.lstrip("<>= ").replace(" ", ""))
    return " ".join(w for w in dict.fromkeys(words) if w)


def map_attributes(pairs: Iterable[tuple[str, Any]], source: str) -> dict[str, SourcedValue]:
    params: dict[str, SourcedValue] = {}
    for name, value in pairs:
        key = ATTRIBUTES.get(str(name).strip().lower())
        if key is None or value in (None, "", "-"):
            continue
        parsed = parse_value(value)
        if key == "protocol" and isinstance(parsed, str):
            parsed = [normalize(p) for p in re.split(r"[,/]", parsed) if p.strip()]
        params[key] = SourcedValue(value=parsed, status="vendor", source=source, note=f"{name}: {value}")
    return params


def keyword_evidence(requirement: Requirement, params: dict[str, SourcedValue], text: str, source: str) -> None:
    """Fill required string parameters that the listing text states (``Raspberry Pi 5`` -> family)."""
    haystack = normalize(text)
    for name, constraint in requirement.constraints.items():
        if name in params or not isinstance(constraint, Equals):
            continue
        for option in constraint.options:
            if option and re.search(rf"(^|-){re.escape(option)}(-|$)", haystack):
                params[name] = SourcedValue(value=option, status="vendor", source=source, note="stated in the listing description")
                break


def _int(value: Any) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


class _OAuthProvider(Provider):
    token_url = ""
    _token: tuple[str, float] | None = None

    def access_token(self, client_id: str, client_secret: str, extra: dict[str, str] | None = None) -> str:
        if self._token and self._token[1] > time.time() + 30:
            return self._token[0]
        response = self.http.post_form(self.token_url, {"grant_type": "client_credentials", "client_id": client_id,
                                                        "client_secret": client_secret, **(extra or {})})
        data = response.json()
        if "access_token" not in data:
            raise ProviderUnavailable("sourcing_provider_auth_failed", f"{self.title}: token request returned no access_token")
        self._token = (data["access_token"], time.time() + float(data.get("expires_in", 600)))
        return self._token[0]


# --------------------------------------------------------------------- Nexar
NEXAR_QUERY = """query R3Search($q: String!, $limit: Int!) {
  supSearch(q: $q, limit: $limit) { results { part { ...R3Part } } }
}
fragment R3Part on SupPart {
  mpn name shortDescription octopartUrl
  manufacturer { name }
  bestDatasheet { url }
  specs { attribute { name shortname } displayValue }
  sellers(authorizedOnly: true) { company { name } offers { clickUrl sku inventoryLevel factoryLeadDays prices { quantity price currency } } }
}"""
NEXAR_MPN_QUERY = NEXAR_QUERY.replace("supSearch(q: $q, limit: $limit)", "supSearchMpn(q: $q, limit: $limit)").replace("R3Search", "R3Mpn")


class NexarProvider(_OAuthProvider):
    id = "nexar"
    title = "Nexar / Octopart"
    env = ("NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET")
    category = "distributor"
    token_url = "https://identity.nexar.com/connect/token"
    api_url = "https://api.nexar.com/graphql"

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        token = self.access_token(os.environ["NEXAR_CLIENT_ID"], os.environ["NEXAR_CLIENT_SECRET"], {"scope": "supply.domain"})
        data = self.http.post_json(self.api_url, {"query": query, "variables": variables},
                                   headers={"Authorization": f"Bearer {token}"}).json()
        if data.get("errors"):
            raise SourcingHttpError(self.api_url, 200, "; ".join(e.get("message", "") for e in data["errors"]))
        return data.get("data") or {}

    def search(self, requirement: Requirement) -> list[Candidate]:
        data = self._query(NEXAR_QUERY, {"q": search_query(requirement), "limit": 10})
        return [self.candidate(r["part"], requirement) for r in (data.get("supSearch") or {}).get("results") or []]

    def price_mpn(self, mpn: str) -> list[Candidate]:
        data = self._query(NEXAR_MPN_QUERY, {"q": mpn, "limit": 3})
        return [self.candidate(r["part"], None) for r in (data.get("supSearchMpn") or {}).get("results") or []]

    def candidate(self, part: dict[str, Any], requirement: Requirement | None) -> Candidate:
        source = part.get("octopartUrl") or self.api_url
        params = map_attributes(((s["attribute"]["name"], s.get("displayValue")) for s in part.get("specs") or []), source)
        if requirement:
            keyword_evidence(requirement, params, f"{part.get('name', '')} {part.get('shortDescription', '')} {part.get('mpn', '')}", source)
        offers = []
        for seller in part.get("sellers") or []:
            for offer in seller.get("offers") or []:
                prices = sorted((int(p["quantity"]), float(p["price"])) for p in offer.get("prices") or [] if p.get("currency", "USD") == "USD")
                offers.append(Offer(vendor=seller["company"]["name"], url=offer.get("clickUrl") or "", sku=offer.get("sku") or "",
                                    price=prices[0][1] if prices else None, price_breaks=prices, stock=_int(offer.get("inventoryLevel")),
                                    lead_time_days=offer.get("factoryLeadDays"), source=source))
        return Candidate(provider=self.id, kind=requirement.kind if requirement else "", name=part.get("name") or part.get("mpn", ""),
                         manufacturer=(part.get("manufacturer") or {}).get("name", ""), mpn=part.get("mpn", ""),
                         description=part.get("shortDescription") or "", params=params, offers=offers,
                         urls={"product": source, **({"datasheet": part["bestDatasheet"]["url"]} if part.get("bestDatasheet") else {})})


# -------------------------------------------------------------------- DigiKey
class DigiKeyProvider(_OAuthProvider):
    id = "digikey"
    title = "DigiKey (Product Information API v4)"
    env = ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")
    category = "distributor"
    token_url = "https://api.digikey.com/v1/oauth2/token"
    api = "https://api.digikey.com/products/v4"

    def _headers(self) -> dict[str, str]:
        client_id = os.environ["DIGIKEY_CLIENT_ID"]
        token = self.access_token(client_id, os.environ["DIGIKEY_CLIENT_SECRET"])
        return {"Authorization": f"Bearer {token}", "X-DIGIKEY-Client-Id": client_id, "X-DIGIKEY-Locale-Currency": "USD",
                "X-DIGIKEY-Locale-Site": "US"}

    def search(self, requirement: Requirement) -> list[Candidate]:
        data = self.http.post_json(f"{self.api}/search/keyword", {"Keywords": search_query(requirement), "Limit": 10, "Offset": 0},
                                   headers=self._headers()).json()
        return [self.candidate(p, requirement) for p in data.get("Products") or []]

    def price_mpn(self, mpn: str) -> list[Candidate]:
        from urllib.parse import quote

        data = self.http.get(f"{self.api}/search/{quote(mpn, safe='')}/productdetails", headers=self._headers(), check_robots=False).json()
        return [self.candidate(data["Product"], None)] if data.get("Product") else []

    def candidate(self, product: dict[str, Any], requirement: Requirement | None) -> Candidate:
        source = product.get("ProductUrl") or self.api
        params = map_attributes(((p.get("ParameterText"), p.get("ValueText")) for p in product.get("Parameters") or []), source)
        description = product.get("Description") or {}
        text = f"{description.get('ProductDescription', '')} {description.get('DetailedDescription', '')} {product.get('ManufacturerProductNumber', '')}"
        if requirement:
            keyword_evidence(requirement, params, text, source)
        offers = []
        lead_weeks = _int(product.get("ManufacturerLeadWeeks"))
        for variation in product.get("ProductVariations") or [{}]:
            prices = sorted((int(p["BreakQuantity"]), float(p["UnitPrice"])) for p in variation.get("StandardPricing") or [])
            price = prices[0][1] if prices else product.get("UnitPrice")
            offers.append(Offer(vendor="DigiKey", url=source, sku=variation.get("DigiKeyProductNumber", ""), price=price,
                                price_breaks=prices, stock=_int(variation.get("QuantityAvailableforPackageType", product.get("QuantityAvailable"))),
                                lead_time_days=lead_weeks * 7.0 if lead_weeks else None, source=source))
        return Candidate(provider=self.id, kind=requirement.kind if requirement else "",
                         name=description.get("ProductDescription") or product.get("ManufacturerProductNumber", ""),
                         manufacturer=(product.get("Manufacturer") or {}).get("Name", ""), mpn=product.get("ManufacturerProductNumber", ""),
                         description=description.get("DetailedDescription", ""), params=params, offers=offers,
                         urls={"product": source, **({"datasheet": product["DatasheetUrl"]} if product.get("DatasheetUrl") else {})})


# --------------------------------------------------------------------- Mouser
class MouserProvider(Provider):
    id = "mouser"
    title = "Mouser (Search API v1)"
    env = ("MOUSER_API_KEY",)
    category = "distributor"
    api = "https://api.mouser.com/api/v1/search"

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = self.http.post_json(f"{self.api}/{path}?apiKey={os.environ['MOUSER_API_KEY']}", body).json()
        errors = data.get("Errors") or []
        if errors:
            raise SourcingHttpError(f"{self.api}/{path}", 200, "; ".join(e.get("Message", "") for e in errors))
        return data.get("SearchResults") or {}

    def search(self, requirement: Requirement) -> list[Candidate]:
        results = self._post("keyword", {"SearchByKeywordRequest": {"keyword": search_query(requirement), "records": 10,
                                                                    "startingRecord": 0, "searchOptions": "",
                                                                    "searchWithYourSignUpLanguage": "false"}})
        return [self.candidate(p, requirement) for p in results.get("Parts") or []]

    def price_mpn(self, mpn: str) -> list[Candidate]:
        results = self._post("partnumber", {"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "Exact"}})
        return [self.candidate(p, None) for p in results.get("Parts") or []]

    def candidate(self, part: dict[str, Any], requirement: Requirement | None) -> Candidate:
        source = part.get("ProductDetailUrl") or self.api
        params = map_attributes(((a.get("AttributeName"), a.get("AttributeValue")) for a in part.get("ProductAttributes") or []), source)
        if requirement:
            keyword_evidence(requirement, params, f"{part.get('Description', '')} {part.get('ManufacturerPartNumber', '')}", source)
        prices = []
        for brk in part.get("PriceBreaks") or []:
            money = parse_money(brk.get("Price"), brk.get("Currency") or "USD")
            if money:
                prices.append((int(brk.get("Quantity", 1)), money.amount))
        prices.sort()
        lead = _int(part.get("LeadTime"))
        offer = Offer(vendor="Mouser", url=source, sku=part.get("MouserPartNumber", ""), price=prices[0][1] if prices else None,
                      price_breaks=prices, stock=_int(part.get("AvailabilityInStock")) if part.get("AvailabilityInStock") is not None
                      else _int(part.get("Availability")), lead_time_days=float(lead) if lead else None, source=source)
        return Candidate(provider=self.id, kind=requirement.kind if requirement else "", name=part.get("Description") or part.get("ManufacturerPartNumber", ""),
                         manufacturer=part.get("Manufacturer", ""), mpn=part.get("ManufacturerPartNumber", ""),
                         description=part.get("Description", ""), params=params, offers=[offer],
                         urls={"product": source, **({"datasheet": part["DataSheetUrl"]} if part.get("DataSheetUrl") else {})})

