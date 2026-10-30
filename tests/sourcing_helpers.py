"""Fixtures for the sourcing tests: recorded HTTP, distributor cassettes, a fake research agent, a sourced bench.

No test touches the network: :func:`block_network` replaces ``urllib.request.urlopen``,
recorded responses (``tests/fixtures/sourcing/http``, captured live from the
Raspberry Pi PIP and the ExpressLRS targets repository) are served offline, and
distributor responses come from cassettes shaped like each API's documented schema.
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import yaml

from r3_catalog.sourcing import HttpClient
from r3_catalog.sourcing.http import Response
from r3_catalog.sourcing.providers import AgentResearchProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "sourcing"
BENCH = ROOT / "tests" / "fixtures" / "bench"

PI_SOURCE = {"requires": {"kind": "computer", "family": "raspberry-pi-5", "ram": ">= 4 GB"}}
RECEIVER_SOURCE = {"requires": {"kind": "radio", "protocol": "crsf", "band": "2.4 GHz", "supply": ["4.5 V", "5.5 V"]}}
WHEEL_SOURCE = {"requires": {"kind": "wheel", "diameter": ["100 mm", "115 mm"], "width": ["40 mm", "50 mm"], "hub": "12 mm hex"},
                "group": "sample_pair", "budget": "40 USD"}


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("sourcing tests must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setenv("R3_RESEARCH_AGENT", "none")
    for name in ("NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET", "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET", "MOUSER_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def recorded_http(tmp_path: Path) -> HttpClient:
    """Offline client over the responses recorded live from the keyless providers."""
    return HttpClient(cache_dir=tmp_path / "http-cache", fixtures=FIXTURES / "http", offline=True)


class CassetteHttp(HttpClient):
    """An HttpClient whose network layer replays a cassette and records what was sent."""

    def __init__(self, tmp_path: Path, responses: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(cache_dir=tmp_path / "cassette-cache", offline=False, **kwargs)
        self.responses = responses
        self.sent: list[dict[str, Any]] = []

    def _fetch(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> Response:
        self.sent.append({"method": method, "url": url, "headers": headers, "body": body.decode() if body else ""})
        bare = urllib.parse.urlsplit(url)._replace(query="").geturl()
        for item in self.responses:
            if item["method"] == method and item["url"] == bare and item.get("match", "") in (body or b"").decode():
                payload = item["body"]
                data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
                return Response(url, int(item.get("status", 200)), data, {"Content-Type": item.get("content_type", "application/json")})
        return Response(url, 404, b"not in cassette", {})


def cassette(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "distributors" / f"{name}.json").read_text())["responses"]


def pages_http(tmp_path: Path) -> CassetteHttp:
    pages = json.loads((FIXTURES / "research" / "pages.json").read_text())
    pages.pop("_note")
    responses = [{"method": "GET", "url": url, "body": html, "content_type": "text/html"} for url, html in pages.items()]
    responses.append({"method": "GET", "url": "https://example.com/robots.txt", "body": "User-agent: *\nAllow: /\n", "content_type": "text/plain"})
    return CassetteHttp(tmp_path, responses)


def fake_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: str = "wheels", mode: str = "ok",
                  verify: bool = True) -> AgentResearchProvider:
    wrapper = tmp_path / "fake-claude"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FIXTURES / 'fake_research_cli.py'} \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_RESEARCH_FIXTURE", str(FIXTURES / "research" / f"{fixture}.json"))
    monkeypatch.setenv("FAKE_RESEARCH_LOG", str(tmp_path / "research-call.json"))
    monkeypatch.setenv("FAKE_RESEARCH_MODE", mode)
    return AgentResearchProvider(http=pages_http(tmp_path), agent="claude", executable=str(wrapper), verify=verify)


def sourced_bench(parent: Path, sources: dict[str, dict[str, Any]] | None = None, drop_type: tuple[str, ...] = ()) -> Path:
    """A copy of tests/fixtures/bench with exactly these `source:` blocks (in addition to `type`, unless dropped).
    The example's own slots, pins and sourced catalog files are removed first, so each test controls its slots."""
    root = parent / "bench"
    shutil.copytree(BENCH, root, ignore=shutil.ignore_patterns("build", "__pycache__", ".venv", "*.pyc", "sourced_*.yaml"))
    spec = yaml.safe_load((root / "robot.yaml").read_text())
    for component in spec["components"].values():
        component.pop("source", None)
    lock_path = root / "r3.lock.yaml"
    if lock_path.exists():
        lock = yaml.safe_load(lock_path.read_text()) or {}
        lock.pop("sourcing", None)
        lock_path.write_text(yaml.safe_dump(lock, sort_keys=True))
    for cid, y in (("sample_a", .3), ("sample_b", -.3)):
        spec["components"][cid] = {"type": "wheel-110x45", "mount": "base", "pose": {"xyz": [.3, y, .1]}}
    spec["mechanical"]["interfaces"].extend([
        {"id": "sample_fit_" + cid, "kind": "revolute_fit", "parts": ["base", cid],
         "axis_point": [.3, y, .1], "axis_dir": [0, 1, 0], "params": {"bore_feature": "hub"}}
        for cid, y in (("sample_a", .3), ("sample_b", -.3))])
    existing = yaml.safe_load(lock_path.read_text()) if lock_path.exists() else {}
    existing.setdefault("cad", {"document_id": "a" * 24, "workspace_id": "b" * 24, "element_id": "c" * 24})
    lock_path.write_text(yaml.safe_dump(existing))
    sources = sources if sources is not None else {"pi": PI_SOURCE, "receiver": RECEIVER_SOURCE, "sample_a": WHEEL_SOURCE,
                                                   "sample_b": WHEEL_SOURCE}
    for cid, source in sources.items():
        spec["components"][cid].pop("owned", None)  # fixture explicitly switches this part to buying
        spec["components"][cid]["source"] = json.loads(json.dumps(source))
    for cid in drop_type:
        spec["components"][cid].pop("type")
    (root / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return root
