"""Agent-assisted web research for long-tail hobby parts (wheels, servos, ESCs, motors).

Runs the same headless coding-agent CLIs as the R3 agent runner
(``r3_orchestrator.api.agents``): Claude Code (``claude -p --output-format json
--json-schema ...`` with only WebSearch/WebFetch) or Codex (``codex --search exec
--output-schema ...``, read-only sandbox). The agent must return candidates
matching :data:`RESEARCH_SCHEMA`, citing a URL (and a short quote) for every
parameter. R3 then:

* validates the JSON against the schema and the :class:`Candidate` model;
* marks a parameter ``estimated`` when it has no http(s) citation, the agent
  itself called it an estimate, or (with ``verify=True``) the cited page does
  not contain the value's number;
* parses value strings (``110 mm``, ``12 mm hex``, ``4.8-8.4 V``) into SI.

``R3_RESEARCH_AGENT`` selects ``claude`` (default), ``codex`` or ``none``;
``R3_RESEARCH_AGENT_BIN`` overrides the executable. The provider is skipped
cleanly when the CLI is not installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..candidate import Candidate, Offer, SourcedValue
from ..http import SourcingHttpError
from ..quantities import parse_money, parse_value
from ..requirements import Requirement, canonical
from .base import Provider, ProviderUnavailable

RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "manufacturer", "mpn", "product_url", "parameters", "offers", "models"],
                "properties": {
                    "name": {"type": "string"},
                    "manufacturer": {"type": "string"},
                    "mpn": {"type": "string"},
                    "product_url": {"type": "string"},
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "value", "status", "source_url", "quote"],
                            "properties": {
                                "name": {"type": "string", "description": "Requirement key, e.g. diameter, width, hub, mass, supply."},
                                "value": {"type": "string", "description": "Value with unit: '110 mm', '12 mm hex', '4.8-8.4 V', '85 g'."},
                                "status": {"type": "string", "enum": ["datasheet", "vendor", "estimated"]},
                                "source_url": {"type": "string", "description": "Page where this exact value is stated."},
                                "quote": {"type": "string", "description": "The text on that page stating the value."},
                            },
                        },
                    },
                    "offers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["vendor", "url", "price", "currency", "in_stock"],
                            "properties": {
                                "vendor": {"type": "string"}, "url": {"type": "string"},
                                "price": {"type": "number"}, "currency": {"type": "string"},
                                "in_stock": {"type": ["boolean", "null"]},
                            },
                        },
                    },
                    "models": {"type": "array", "items": {"type": "string", "description": "URL of a vendor STEP/STL model."}},
                },
            },
        },
    },
}
DEFAULT_KINDS = ("wheel", "servo", "esc", "motor", "battery", "gearbox", "structure", "antenna", "switch")
TIMEOUT_S = 900


def research_prompt(requirement: Requirement) -> str:
    wanted = json.dumps(requirement.raw, default=str)
    prefer = json.dumps(requirement.prefer, default=str) if requirement.prefer else "none"
    return (
        f"Find up to 6 real, currently purchasable parts for a robot component slot.\n"
        f"Slot kind: {requirement.kind}\nHard requirements (all must hold): {wanted}\nPreferences: {prefer}\n"
        f"Quantity: {requirement.quantity}\n\n"
        "Use web search and open the manufacturer's or vendor's product page for each part. For every parameter you report "
        "(at least each requirement key, plus mass if published), give the value WITH its unit, the URL of the page that states it "
        "and a short verbatim quote from that page. status: 'datasheet' for manufacturer specifications, 'vendor' for a shop listing, "
        "'estimated' when you inferred or computed it (then still give the best URL you have). Never invent URLs, part numbers or "
        "prices; omit anything you could not find. Include offers (shop, URL, unit price, currency, in_stock) and links to vendor "
        "STEP/STL models if published. Answer only with JSON matching the given schema."
    )


def _executable(agent: str) -> str | None:
    return os.environ.get("R3_RESEARCH_AGENT_BIN") or shutil.which(agent)


class AgentResearchProvider(Provider):
    id = "research"
    title = "Agent web research (Claude Code / Codex)"
    kinds = DEFAULT_KINDS
    category = "research"

    def __init__(self, http: Any = None, agent: str | None = None, executable: str | None = None, verify: bool = True,
                 timeout: float = TIMEOUT_S) -> None:
        super().__init__(http)
        self.agent = (agent or os.environ.get("R3_RESEARCH_AGENT") or "claude").lower()
        self.executable = executable
        self.verify = verify
        self.timeout = timeout
        #: Raw agent output of the last search (also written by the orchestrator to build/sourcing/).
        self.last_output: str = ""

    def check_available(self) -> None:
        if self.agent == "none":
            raise ProviderUnavailable("sourcing_provider_disabled", "agent research disabled (R3_RESEARCH_AGENT=none)")
        if self.agent not in ("claude", "codex"):
            raise ProviderUnavailable("sourcing_provider_disabled", f"R3_RESEARCH_AGENT must be claude, codex or none, not {self.agent!r}")
        if not (self.executable or _executable(self.agent)):
            raise ProviderUnavailable("sourcing_provider_cli_missing", f"`{self.agent}` CLI not found on PATH; agent research skipped",
                                      hint="Install and sign in to Claude Code or Codex (the same CLIs `r3 serve` agents use).")

    # ------------------------------------------------------------- running
    def command(self, schema_path: Path, last_message: Path) -> list[str]:
        executable = self.executable or _executable(self.agent) or self.agent
        if self.agent == "claude":
            return [executable, "-p", "--output-format", "json", "--json-schema", json.dumps(RESEARCH_SCHEMA),
                    "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch", "--permission-mode", "default",
                    "--no-session-persistence"]
        return [executable, "--search", "exec", "--json", "--skip-git-repo-check", "--color", "never", "--sandbox", "read-only",
                "--output-schema", str(schema_path), "-o", str(last_message), "-"]

    def run_agent(self, prompt: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="r3-research-") as folder:
            schema_path, last_message = Path(folder) / "schema.json", Path(folder) / "last.json"
            schema_path.write_text(json.dumps(RESEARCH_SCHEMA))
            try:
                proc = subprocess.run(self.command(schema_path, last_message), input=prompt, capture_output=True, text=True,
                                      timeout=self.timeout, cwd=folder)
            except subprocess.TimeoutExpired:
                raise ProviderUnavailable("sourcing_research_timeout", f"{self.agent} research exceeded {self.timeout:.0f} s") from None
            self.last_output = proc.stdout
            if proc.returncode != 0:
                raise ProviderUnavailable("sourcing_research_failed", f"{self.agent} exited with {proc.returncode}: {proc.stderr.strip()[-500:]}")
            if self.agent == "codex":
                return _json_object(last_message.read_text() if last_message.exists() else proc.stdout)
            return parse_claude_output(proc.stdout)

    def search(self, requirement: Requirement) -> list[Candidate]:
        payload = self.run_agent(research_prompt(requirement))
        return self.candidates(payload, requirement)

    # ---------------------------------------------------------- validation
    def candidates(self, payload: dict[str, Any], requirement: Requirement) -> list[Candidate]:
        try:
            import jsonschema  # type: ignore[import-not-found]
        except ImportError:
            jsonschema = None
        if jsonschema is not None:
            try:
                jsonschema.validate(payload, RESEARCH_SCHEMA)
            except jsonschema.ValidationError as error:
                raise ProviderUnavailable("sourcing_research_invalid", f"agent output violates the candidate schema: {error.message}") from None
        elif not isinstance(payload.get("candidates"), list):
            raise ProviderUnavailable("sourcing_research_invalid", "agent output has no `candidates` list")
        out = []
        for item in payload["candidates"]:
            try:
                out.append(self._candidate(item, requirement))
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise ProviderUnavailable("sourcing_research_invalid", f"agent candidate {item.get('name', '?')!r} is invalid: {error}") from None
        return out

    def _candidate(self, item: dict[str, Any], requirement: Requirement) -> Candidate:
        params: dict[str, SourcedValue] = {}
        page_cache: dict[str, str | None] = {}
        for entry in item["parameters"]:
            name = canonical(entry["name"])
            url, quote, status = entry["source_url"].strip(), entry["quote"].strip(), entry["status"]
            notes = []
            if not re.match(r"^https?://", url):
                status, url = "estimated", ""
                notes.append("no citation URL")
            elif not quote:
                status = "estimated"
                notes.append("no quote from the cited page")
            elif self.verify and status != "estimated":
                verdict = self._verify(url, entry["value"], quote, page_cache)
                if verdict is False:
                    status = "estimated"
                    notes.append("value not found on the cited page")
                elif verdict is None:
                    notes.append("citation not verified (page not fetchable)")
            params[name] = SourcedValue(value=parse_value(entry["value"]), status=status, source=url,
                                        note="; ".join([f"agent: {entry['value']}", *notes]) + (f" | quote: {quote[:160]}" if quote else ""))
        offers = []
        for offer in item.get("offers") or []:
            money = parse_money({"amount": offer["price"], "currency": offer.get("currency") or "USD"}) if offer.get("price") else None
            stock = None if offer.get("in_stock") is None else (1 if offer["in_stock"] else 0)
            offers.append(Offer(vendor=offer["vendor"], url=offer["url"], price=money.amount if money else None,
                                currency=money.currency if money else "USD", stock=stock, status="vendor", source=offer["url"]))
        from ..candidate import ModelFile

        models = [ModelFile(url=url, format="step" if re.search(r"\.(step|stp)(\?|$)", url, re.I) else "other", source=item["product_url"])
                  for url in item.get("models") or [] if re.match(r"^https?://", url)]
        return Candidate(provider=self.id, kind=requirement.kind, name=item["name"], manufacturer=item["manufacturer"], mpn=item["mpn"],
                         params=params, offers=offers, urls={"product": item["product_url"]}, models=models,
                         notes=[f"Found by {self.agent} web research; parameters verified against their citations where fetchable."])

    def _verify(self, url: str, value: str, quote: str, cache: dict[str, str | None]) -> bool | None:
        """True if the cited page contains the value's leading number (and some quote words), None if unfetchable."""
        if url not in cache:
            try:
                response = self.http.get(url)
                text = response.text if "pdf" not in response.headers.get("Content-Type", "") else self.http.document_text(url).text
                cache[url] = re.sub(r"<[^>]+>", " ", text)
            except (SourcingHttpError, ValueError, ImportError):
                cache[url] = None
        page = cache[url]
        if page is None:
            return None
        numbers = re.findall(r"\d+(?:\.\d+)?", value)
        flat = re.sub(r"\s+", " ", page).lower()
        if numbers:
            return all(re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", flat) for n in numbers[:2])
        return value.strip().lower() in flat


def _json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ProviderUnavailable("sourcing_research_invalid", "agent output is not JSON") from None
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ProviderUnavailable("sourcing_research_invalid", "agent output is not a JSON object")
    return value


def parse_claude_output(stdout: str) -> dict[str, Any]:
    """``claude -p --output-format json``: ``structured_output`` (with --json-schema) or the ``result`` text."""
    envelope = _json_object(stdout)
    if envelope.get("is_error"):
        raise ProviderUnavailable("sourcing_research_failed", f"claude reported an error: {str(envelope.get('result'))[:300]}")
    if isinstance(envelope.get("structured_output"), dict):
        return envelope["structured_output"]
    if "candidates" in envelope:
        return envelope
    return _json_object(str(envelope.get("result", "")))
