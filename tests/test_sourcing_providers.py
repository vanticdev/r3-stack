"""Sourcing providers against recorded HTTP fixtures (no network)."""

from __future__ import annotations

import json

import pytest

from r3_catalog.sourcing import (HttpClient, NetworkDisabled, Requirement, RobotsDisallowed, make_providers, search)
from r3_catalog.sourcing.http import redact_url, request_key
from r3_catalog.sourcing.providers import (DigiKeyProvider, ExpressLrsProvider, MouserProvider, NexarProvider, ProviderUnavailable,
                                           RaspberryPiProvider)
from r3_catalog.sourcing.providers.raspberrypi import drawing_has, parse_brief
from sourcing_helpers import (FIXTURES, PI_SOURCE, RECEIVER_SOURCE, WHEEL_SOURCE, CassetteHttp, block_network, cassette, fake_research,
                              recorded_http)

pytestmark = pytest.mark.usefixtures("block_network")
_ = block_network


def requirement(source, slot="slot", components=("slot",)):
    return Requirement.from_source(slot, source, list(components))


# ------------------------------------------------------------------ Raspberry Pi
def test_raspberry_pi_pip_candidates(tmp_path):
    provider = RaspberryPiProvider(http=recorded_http(tmp_path))
    req = requirement(PI_SOURCE)
    assert provider.handles(req)
    found = {c.params["ram"].value: c for c in provider.search(req)}
    assert sorted(found) == [1e9, 2e9, 4e9, 8e9, 16e9]  # RAM options from the product brief
    pi4 = found[4e9]
    assert pi4.name == "Raspberry Pi 5 (4 GB)" and pi4.manufacturer == "Raspberry Pi Ltd"
    assert pi4.offers[0].price == 110.0 and pi4.offers[0].currency == "USD" and pi4.offers[0].status == "datasheet"
    assert pi4.params["supply"].value == 5.0 and pi4.params["supply_current"].value == 5.0
    assert pi4.params["supply"].source.endswith("raspberry-pi-5-product-brief.pdf")
    # The drawing confirms the B-series outline and hole pattern.
    for name in ("length", "width", "mount_holes", "mount_hole_diameter"):
        assert pi4.params[name].status == "datasheet", name
        assert pi4.params[name].source.endswith("raspberry-pi-5-mechanical-drawing.pdf")
    assert pi4.params["length"].value == pytest.approx(0.085) and pi4.params["mount_holes"].value == pytest.approx([0.058, 0.049])
    step = [m for m in pi4.models if m.format == "step_zip"]
    assert step and "STEP" in step[0].url and "No%20Graphics" in step[0].url
    matches = {m.param: m.status for m in req.evaluate_candidate(found[2e9])}
    assert matches["ram"] == "fail"


def test_brief_and_drawing_parsers():
    text = ("LPDDR4X-4267 SDRAM (options for 1GB, 2GB, 4GB, 8GB and 16GB)\n5V/5A DC power via USB-C\n"
            "Operating temperature 0°C to 70°C\nList price 1GB $45\n2GB $77.50\n4GB $110\n1 Mean Time Between Failure")
    brief = parse_brief(text)
    assert brief["ram_gb"] == [1, 2, 4, 8, 16] and brief["supply_voltage"] == 5 and brief["list_price"][2.0] == 77.5
    assert brief["operating_temperature"] == [0, 70]
    assert drawing_has("85\n58\nØ3\n564729.1\nØ2.7", 56) and drawing_has("Ø2.7", 2.7) and not drawing_has("85 58", 49)


# -------------------------------------------------------------------- ExpressLRS
def test_expresslrs_targets(tmp_path):
    provider = ExpressLrsProvider(http=recorded_http(tmp_path))
    req = requirement(RECEIVER_SOURCE)
    candidates = provider.search(req)
    by_name = {c.name: c for c in candidates}
    assert len(candidates) > 100
    rp1 = by_name["RadioMaster RP1 2.4GHz RX"]
    assert rp1.params["protocol"].value == ["crsf"] and rp1.params["uart"].value is True
    assert rp1.params["band"].value == [2.4e9, 2.4835e9] and rp1.params["band"].status == "datasheet"
    assert rp1.params["supply"].status == "estimated"  # not part of the ELRS target definitions
    assert rp1.params["telemetry_power"].value == pytest.approx(10.0)
    assert not any("FC integrated" in name or " AIO " in f" {name} " for name in by_name)  # FC/AIO receivers are not parts
    pwm_only = [c for c in candidates if c.params["uart"].value is False]
    assert pwm_only and all(c.params["protocol"].value == ["pwm"] for c in pwm_only)
    statuses = {m.param: m.status for m in req.evaluate_candidate(rp1)}
    assert set(statuses.values()) == {"pass"}
    assert {m.param: m.status for m in req.evaluate_candidate(pwm_only[0])}["protocol"] == "fail"
    assert not provider.handles(requirement({"requires": {"kind": "radio", "protocol": "sbus"}}))


def test_expresslrs_900_mhz_band_selection():
    req = requirement({"requires": {"kind": "radio", "band": "915 MHz"}})
    from r3_catalog.sourcing.providers.expresslrs import _categories

    assert _categories(req) == ["rx_900"]


# ------------------------------------------------------------------------ HTTP
def test_offline_cache_and_redaction(tmp_path):
    client = HttpClient(cache_dir=tmp_path, offline=True)
    with pytest.raises(NetworkDisabled):
        client.get("https://example.com/part")
    assert "REDACTED" in redact_url("https://api.mouser.com/api/v1/search/keyword?apiKey=secret123")
    assert request_key("POST", "https://x.test/a?apiKey=one") == request_key("POST", "https://x.test/a?apiKey=two")
    assert request_key("POST", "https://x.test/t", b"grant_type=client_credentials&client_id=a&client_secret=b") == \
        request_key("POST", "https://x.test/t", b"grant_type=client_credentials&client_id=c&client_secret=d")


def test_robots_txt_is_respected(tmp_path):
    http = CassetteHttp(tmp_path, [
        {"method": "GET", "url": "https://shop.test/robots.txt", "body": "User-agent: *\nDisallow: /api/\n", "content_type": "text/plain"},
        {"method": "GET", "url": "https://shop.test/products/1", "body": "<html>ok</html>", "content_type": "text/html"}])
    assert http.get("https://shop.test/products/1").text == "<html>ok</html>"
    with pytest.raises(RobotsDisallowed):
        http.get("https://shop.test/api/products")
    assert [s["url"] for s in http.sent] == ["https://shop.test/robots.txt", "https://shop.test/products/1"]


def test_recording_never_stores_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "super-secret-key")
    http = CassetteHttp(tmp_path, cassette("mouser"), record=tmp_path / "recorded")
    MouserProvider(http=http).search(requirement(PI_SOURCE))
    text = "".join(p.read_text(errors="replace") for p in (tmp_path / "recorded").iterdir())
    assert "super-secret-key" not in text and "REDACTED" in text
    assert "apiKey=super-secret-key" in http.sent[0]["url"]  # the real request carried the key


# ---------------------------------------------------------------- distributors
def test_distributors_skip_without_keys(tmp_path):
    result = search(requirement(PI_SOURCE), providers=make_providers(["nexar", "digikey", "mouser"], HttpClient(cache_dir=tmp_path, offline=True)))
    assert [(r.provider, r.status, r.code) for r in result.reports] == [
        ("nexar", "skipped", "sourcing_provider_key_missing"), ("digikey", "skipped", "sourcing_provider_key_missing"),
        ("mouser", "skipped", "sourcing_provider_key_missing")]
    assert "NEXAR_CLIENT_ID" in result.reports[0].message and not result.candidates


def test_nexar_search_and_mpn_pricing(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXAR_CLIENT_ID", "id")
    monkeypatch.setenv("NEXAR_CLIENT_SECRET", "secret")
    http = CassetteHttp(tmp_path, cassette("nexar"))
    provider = NexarProvider(http=http)
    (rx,) = provider.search(requirement(RECEIVER_SOURCE))
    assert rx.mpn == "FIXTURE-RX24-CRSF" and rx.params["band"].value == pytest.approx(2.4e9)
    assert rx.params["supply"].value == [4.5, 6.0] and rx.params["supply"].status == "vendor"
    assert rx.params["protocol"].value == ["crsf"] and rx.params["mass"].value == pytest.approx(0.0016)
    assert rx.offers[0].stock == 35 and rx.offers[0].price == 18.0
    assert http.sent[0]["url"] == "https://identity.nexar.com/connect/token"
    assert http.sent[1]["headers"]["Authorization"] == "Bearer fixture-nexar-token"
    assert "supSearch" in json.loads(http.sent[1]["body"])["query"]
    (buck,) = provider.price_mpn("FIXTURE-BUCK-5V3A")
    assert {o.vendor for o in buck.offers} == {"Fixture Distributor A", "Fixture Distributor B"}
    assert buck.best_offer(10).vendor == "Fixture Distributor A" and buck.best_offer(10).unit_price(10) == 1.85  # B has no stock
    assert len([s for s in http.sent if "identity" in s["url"]]) == 1  # token reused


def test_digikey_keyword_search_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "id")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "secret")
    http = CassetteHttp(tmp_path, cassette("digikey"))
    req = requirement(PI_SOURCE)
    pi, case = DigiKeyProvider(http=http).search(req)
    assert pi.params["ram"].value == 4e9 and pi.params["family"].note == "stated in the listing description"
    assert pi.offers[0].price_breaks == [(1, 60.0), (10, 58.5)] and pi.offers[0].lead_time_days == 42
    assert {m.param: m.status for m in req.evaluate_candidate(pi)}["ram"] == "pass"
    assert {m.param: m.status for m in req.evaluate_candidate(case)}["ram"] == "unknown"  # a case has no RAM: unverified
    body = json.loads(http.sent[1]["body"])
    assert "raspberry pi 5" in body["Keywords"] and http.sent[1]["headers"]["X-DIGIKEY-Client-Id"] == "id"


def test_mouser_keyword_and_part_number(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "key")
    provider = MouserProvider(http=CassetteHttp(tmp_path, cassette("mouser")))
    (pi,) = provider.search(requirement(PI_SOURCE))
    assert pi.params["ram"].value == 8e9 and pi.offers[0].stock == 57 and pi.offers[0].lead_time_days == 42
    assert pi.offers[0].unit_price(5) == 78.4
    (buck,) = provider.price_mpn("FIXTURE-BUCK-5V3A")
    assert buck.offers[0].price == 1.95


# ------------------------------------------------------------------- research
def test_agent_research_validates_and_marks_estimates(tmp_path, monkeypatch):
    provider = fake_research(tmp_path, monkeypatch)
    provider.check_available()
    req = requirement(WHEEL_SOURCE, "front_wheels", ["wheel_fl", "wheel_fr"])
    found = {c.mpn: c for c in provider.search(req)}
    call = json.loads((tmp_path / "research-call.json").read_text())
    assert "--json-schema" in call["argv"] and "WebSearch,WebFetch" in call["argv"] and "12 mm hex" in call["stdin"]
    good = found["FW-110-45-HEX12"]
    assert good.params["diameter"].value == pytest.approx(0.11) and good.params["diameter"].status == "vendor"
    assert good.params["hub"].value == {"size": pytest.approx(0.012), "shape": "hex"}
    assert good.offers[0].price == 7.5 and good.offers[0].stock == 1
    other = found["FW-108-42-HEX12"]
    assert other.params["hub"].status == "estimated" and "no citation URL" in other.params["hub"].note
    assert other.params["mass"].status == "estimated" and "not found on the cited page" in other.params["mass"].note  # page says 135 g
    assert other.params["diameter"].status == "vendor"  # verified on the page
    unverifiable = found["FW-95-40-HEX12"]
    assert "citation not verified" in unverifiable.params["diameter"].note and unverifiable.params["diameter"].status == "vendor"


def test_agent_research_rejects_invalid_output(tmp_path, monkeypatch):
    provider = fake_research(tmp_path, monkeypatch, mode="invalid")
    result = search(requirement(WHEEL_SOURCE), providers=[provider])
    assert result.reports[0].status == "failed" and result.reports[0].code == "sourcing_research_invalid"


def test_agent_research_unavailable(tmp_path, monkeypatch):
    from r3_catalog.sourcing.providers import AgentResearchProvider

    with pytest.raises(ProviderUnavailable) as error:
        AgentResearchProvider(agent="none").check_available()
    assert error.value.code == "sourcing_provider_disabled"
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("R3_RESEARCH_AGENT_BIN", raising=False)
    with pytest.raises(ProviderUnavailable) as error:
        AgentResearchProvider(agent="codex").check_available()
    assert error.value.code == "sourcing_provider_cli_missing"
    codex = AgentResearchProvider(agent="codex", executable="codex")
    command = codex.command(tmp_path / "s.json", tmp_path / "o.json")
    assert command[:3] == ["codex", "--search", "exec"] and "--output-schema" in command and "read-only" in command


def test_fixture_store_is_complete_for_keyless_providers():
    metas = [json.loads(p.read_text()) for p in (FIXTURES / "http").glob("*.json")]
    urls = {m["url"] for m in metas}
    assert any(u.endswith("/sitemap.xml") for u in urls) and any("892-raspberry-pi-5" in u for u in urls)
    assert any(u.endswith("ExpressLRS/targets/master/targets.json") for u in urls)
    assert all("size" in m for m in metas)
    assert max(p.stat().st_size for p in (FIXTURES / "http").iterdir()) < 400_000  # extracted PDF text, not 50 MB PDFs
