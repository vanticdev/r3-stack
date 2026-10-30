"""Sourcing end to end on the bench (recorded fixtures, no network): search, rank with twin
checks, choose, lockfile pinning, catalog metadata, CLI, MCP and HTTP API."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from r3_catalog.sourcing.providers import ExpressLrsProvider, RaspberryPiProvider
from r3_model.project import Project
from r3_orchestrator import model_stage, sourcing
from sourcing_helpers import (FIXTURES, PI_SOURCE, RECEIVER_SOURCE, CassetteHttp, block_network, fake_research, recorded_http,
                              sourced_bench)

pytestmark = pytest.mark.usefixtures("block_network")
_ = block_network

ELECTRICAL_CODES = {
    "binding_not_connected", "binding_pin_function_missing", "binding_pwm_channel_mismatch", "binding_device_mismatch",
    "binding_gpio_line_mismatch", "uart_tx_tx_conflict", "input_without_driver", "power_conflict", "power_ground_short",
    "power_in_voltage_out_of_range", "power_in_overvoltage", "logic_level_overvoltage", "i2c_address_conflict",
    "connector_mate_mismatch", "wire_gauge_connector_mismatch", "connector_multiply_mated", "unknown_component_type",
}


def providers(tmp_path, monkeypatch):
    http = recorded_http(tmp_path)
    return [RaspberryPiProvider(http=http), ExpressLrsProvider(http=http), fake_research(tmp_path, monkeypatch)]


def interference_pairs(result) -> set[frozenset[str]]:
    return {frozenset(d.subjects) for d in result.diagnostics if d.code == "component_interference"}


def source_all(root: Path, tmp_path: Path, monkeypatch) -> dict[str, dict]:
    project = Project.load(root)
    found = {slot: sourcing.search_slot(project, slot, providers=providers(tmp_path, monkeypatch)) for slot in ("pi", "receiver", "sample_pair")}
    chosen = {slot: sourcing.choose(root, slot, 1) for slot in found}
    return {"found": found, "chosen": chosen}


def test_bench_sources_wheel_receiver_and_pi_and_passes_the_model_stage(tmp_path, monkeypatch):
    root = sourced_bench(tmp_path)

    unmet = [d for d in sourcing.check_pins(Project.load(root)) if d.code == "source_requirement_unmet"]
    assert [d.data["param"] for d in unmet] == ["hub"]  # the authored 6 mm plain-bore wheel is not a 12 mm hex wheel
    assert set(unmet[0].subjects) == {"sample_a", "sample_b"}

    out = source_all(root, tmp_path, monkeypatch)
    pi, receiver, wheels = (out["found"][s] for s in ("pi", "receiver", "sample_pair"))
    assert pi["counts"] == {"eligible": 3, "unverified": 0, "rejected": 2}
    assert pi["ranked"][0]["candidate"]["name"] == "Raspberry Pi 5 (4 GB)" and pi["ranked"][0]["unit_price"] == 110.0
    assert receiver["ranked"][0]["candidate"]["name"] == "RadioMaster RP1 2.4GHz RX"  # the incumbent wins the tie
    assert receiver["counts"]["eligible"] > 100 and receiver["template"] == "elrs-receiver-2g4"
    tiers = {r["candidate"]["mpn"]: r["tier"] for r in wheels["ranked"]}
    assert tiers == {"FW-110-45-HEX12": "eligible", "FW-108-42-HEX12": "eligible", "FW-95-40-HEX12": "rejected", "FW-112": "unverified"}
    best = wheels["ranked"][0]
    assert best["candidate"]["mpn"] == "FW-110-45-HEX12"  # FW-108-42 has an uncited (estimated) hub
    codes = {c["code"] for c in best["twin"]["checks"]}
    assert "sourcing_interface_feature_changed" in codes  # The specimen mounts used a plain bore; a hex hub needs an adapter
    assert not best["twin"]["errors"]
    mass = next(c for c in best["twin"]["checks"] if c["code"] == "sourcing_mass_budget")
    assert mass["data"]["delta"] == pytest.approx(2 * (0.092 - 0.085))
    assert all(r["ok"] for r in out["chosen"].values()), out["chosen"]

    lock = yaml.safe_load((root / "r3.lock.yaml").read_text())
    assert lock["cad"]["document_id"]  # existing lock sections are preserved
    assert set(lock["sourcing"]) == {"pi", "receiver", "sample_pair"}
    pin = lock["sourcing"]["sample_pair"]
    assert pin["components"] == ["sample_a", "sample_b"] and pin["price"] == {"amount": 7.5, "currency": "USD", "quantity": 2}
    assert pin["vendor"] == "Fixture Hobby Shop" and pin["url"] == "https://example.com/shop/fw-110-45" and pin["mpn"] == "FW-110-45-HEX12"
    assert lock["sourcing"]["pi"]["cad"] == "vendor_step" and lock["sourcing"]["receiver"]["cad"] == "missing"
    for slot_pin in lock["sourcing"].values():
        assert (root / slot_pin["catalog_file"]).is_file() and slot_pin["digest"] and slot_pin["retrieved_at"] > 0

    project = Project.load(root)
    assert project.spec.components["sample_a"].type == project.spec.components["sample_b"].type == pin["catalog_id"]
    assert project.spec.components["pi"].type.startswith("sourced-raspberrypi")
    assert "sourcing_sha256" in project.fingerprint()
    assert not {d.code for d in sourcing.check_pins(project) if d.severity.value == "error"}
    after = model_stage.run(project)
    assert "cad_pull_missing" in {d.code for d in after.diagnostics}
    assert not (root / "build/model/graph.json").exists()


def test_catalog_entry_provenance_per_field(tmp_path, monkeypatch):
    root = sourced_bench(tmp_path)
    source_all(root, tmp_path, monkeypatch)
    lock = yaml.safe_load((root / "r3.lock.yaml").read_text())["sourcing"]
    wheel = yaml.safe_load((root / lock["sample_pair"]["catalog_file"]).read_text())
    (entry,) = wheel["components"]
    meta = wheel["sourced"]
    assert entry["mechanical"]["size"] == pytest.approx([0.11, 0.045]) and entry["mechanical"]["mass"] == pytest.approx(0.092)
    assert "features" not in entry["mechanical"] and "solids" not in entry["mechanical"]
    assert any("native model import" in note for note in meta["notes"])
    assert meta["fields"]["mechanical.size"]["status"] == "vendor" and meta["fields"]["mechanical.mass"]["status"] == "vendor"
    assert meta["fields"]["behavior.params.radius"]["source"] == "https://example.com/wheels/fw-110-45"
    assert meta["template"] == "wheel-110x45" and meta["cad"]["status"] == "missing"
    assert entry["behavior"]["provenance"]["status"] == "estimated"  # inherited from a different product
    pi = yaml.safe_load((root / lock["pi"]["catalog_file"]).read_text())
    assert pi["sourced"]["fields"]["mechanical.mount_holes"]["status"] == "datasheet"
    assert pi["components"][0]["pins"]["5V"]["current_max"] == 5.0
    assert pi["sourced"]["fields"]["pins.5V.current_max"]["source"].endswith("product-brief.pdf")
    assert any(m["format"] == "step_zip" for m in pi["sourced"]["models"])


def test_pins_are_reproducible_and_guarded(tmp_path, monkeypatch):
    root = sourced_bench(tmp_path, sources={"pi": PI_SOURCE})
    project = Project.load(root)
    sourcing.search_slot(project, "pi", providers=providers(tmp_path, monkeypatch))
    pinned = sourcing.choose(root, "pi", "raspberrypi-raspberry-pi-ltd-raspberry-pi-5-8gb")  # by candidate id
    assert pinned["ok"] and pinned["pinned"]["name"] == "Raspberry Pi 5 (8 GB)"
    first = Project.load(root).spec.components["pi"].type
    assert Project.load(root).spec.components["pi"].type == first  # no re-sourcing on load

    rejected = sourcing.choose(root, "pi", "raspberrypi-raspberry-pi-ltd-raspberry-pi-5-2gb")
    assert rejected["code"] == "sourcing_candidate_rejected" and not rejected["ok"]
    with pytest.raises(sourcing.SourcingError):
        sourcing.choose(root, "pi", 99)

    path = root / pinned["catalog_file"]
    path.write_text(path.read_text().replace("Raspberry Pi 5 (8 GB)", "Edited by hand", 1))
    codes = {d.code for d in sourcing.check_pins(Project.load(root))}
    assert "sourcing_digest_mismatch" in codes

    spec = yaml.safe_load((root / "robot.yaml").read_text())
    spec["components"]["pi"]["source"]["requires"]["ram"] = ">= 8 GB"
    (root / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    assert "sourcing_pin_stale" in {d.code for d in sourcing.check_pins(Project.load(root))}
    assert sourcing.choose(root, "pi", 1)["code"] == "sourcing_search_stale"

    path.unlink()
    assert "sourcing_catalog_missing" in {d.code for d in sourcing.check_pins(Project.load(root))}
    assert sourcing.unpin(root, "pi")["removed"]["name"] == "Raspberry Pi 5 (8 GB)"
    assert Project.load(root).spec.components["pi"].type == "raspberry-pi-5-4gb"  # back to the authored type


def test_source_only_slot_uses_a_template_by_connectors(tmp_path, monkeypatch):
    root = sourced_bench(tmp_path, sources={"receiver": RECEIVER_SOURCE}, drop_type=("receiver",))
    project = Project.load(root)
    assert "component_unsourced" in {d.code for d in project.graph.diagnostics}
    found = sourcing.search_slot(project, "receiver", providers=providers(tmp_path, monkeypatch))
    assert found["template"] == "elrs-receiver-2g4" and found["current"] is None  # the only radio providing J1
    assert sourcing.choose(root, "receiver", 1)["ok"]
    result = model_stage.run(Project.load(root))
    assert not {d.code for d in result.diagnostics} & (ELECTRICAL_CODES | {"component_unsourced"})


def test_list_slots(tmp_path, monkeypatch):
    root = sourced_bench(tmp_path)
    listed = sourcing.list_slots(Project.load(root))
    slots = {s["slot"]: s for s in listed["slots"]}
    assert set(slots) == {"pi", "receiver", "sample_pair"} and slots["sample_pair"]["quantity"] == 2
    assert slots["sample_pair"]["budget"] == {"amount": 40.0, "currency": "USD"}
    providers_by_id = {p["id"]: p for p in listed["providers"]}
    assert not providers_by_id["nexar"]["available"] and providers_by_id["raspberrypi"]["available"]


# ------------------------------------------------------------------ interfaces
@pytest.fixture
def offline_cache(tmp_path, monkeypatch) -> Path:
    """The recorded responses as the default offline cache (same on-disk format)."""
    cache = tmp_path / "cache"
    shutil.copytree(FIXTURES / "http", cache)
    monkeypatch.setenv("R3_SOURCING_CACHE", str(cache))
    return cache


def test_cli_source(tmp_path, offline_cache, capsys):
    from r3_orchestrator import cli

    root = sourced_bench(tmp_path, sources={"pi": PI_SOURCE, "receiver": RECEIVER_SOURCE})
    assert cli.main(["source", "-C", str(root)]) == 0
    assert "pi" in capsys.readouterr().out
    assert cli.main(["source", "pi", "--offline", "-C", str(root)]) == 0
    text = capsys.readouterr().out
    assert "Raspberry Pi 5 (4 GB)" in text and "nexar" in text and "skipped" in text
    assert cli.main(["source", "--all", "--offline", "--choose", "1", "--json", "-C", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["choice"]["pinned"]["name"] for r in payload["results"]] == ["Raspberry Pi 5 (4 GB)", "RadioMaster RP1 2.4GHz RX"]
    assert cli.main(["source", "--all", "--offline", "--choose", "1", "-C", str(root)]) == 0
    assert "already pinned" in capsys.readouterr().out
    assert cli.main(["source", "nope", "--offline", "-C", str(root)]) == 2


def test_mcp_tools(tmp_path, offline_cache):
    from r3_orchestrator import mcp_server

    root = sourced_bench(tmp_path, sources={"pi": PI_SOURCE})
    assert {"r3_source_slots", "r3_source_search", "r3_source_choose"} <= set(mcp_server.ungated_tools())
    assert mcp_server.TOOL_TIERS["r3_source_search"] == "design" and mcp_server.TOOL_TIERS["r3_source_choose"] == "edit"
    assert mcp_server.r3_source_slots(project=str(root))["slots"][0]["slot"] == "pi"
    found = mcp_server.r3_source_search("pi", project=str(root), offline=True, top=2, providers=["raspberrypi"])
    assert len(found["ranked"]) == 2 and found["total"] == 5 and found["rejected"][0]["reasons"]
    chosen = mcp_server.r3_source_choose("pi", "1", project=str(root))
    assert chosen["ok"] and chosen["pinned"]["price"]["amount"] == 110.0
    assert mcp_server.r3_source_search("nope", project=str(root), offline=True)["code"] == "usage_error"


def test_http_api_search_job_and_choose(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from api_helpers import sse
    from r3_orchestrator.api.server import create_app
    from r3_orchestrator.api.sourcing_jobs import SourcingJobs

    projects = tmp_path / "projects"
    root = sourced_bench(projects, sources={"pi": PI_SOURCE, "receiver": RECEIVER_SOURCE})
    jobs = SourcingJobs(http_factory=lambda: recorded_http(tmp_path))
    with TestClient(create_app([projects], web_dist=tmp_path / "dist", sourcing_jobs=jobs)) as client:
        pid = client.get("/api/projects").json()["projects"][0]["id"]
        slots = client.get(f"/api/projects/{pid}/sourcing").json()["slots"]
        assert [s["slot"] for s in slots] == ["pi", "receiver"]
        assert client.get(f"/api/projects/{pid}/sourcing/pi").json()["code"] == "sourcing_not_searched"
        created = client.post(f"/api/projects/{pid}/sourcing/pi/search", json={"providers": ["raspberrypi"]})
        assert created.status_code == 202
        events = sse(client, created.json()["events_url"])
        kinds = [e["type"] for e in events]
        assert kinds[0] == "job_started" and kinds[-1] == "job_finished" and events[-1]["status"] == "succeeded"
        assert {"provider_started", "provider_finished", "ranking_started", "candidate", "search_finished"} <= set(kinds)
        stored = client.get(f"/api/projects/{pid}/sourcing/pi").json()
        assert stored["counts"]["eligible"] == 3 and stored["stale"] is False and stored["pinned"] is None
        assert stored["ranked"][0]["matches"] and stored["ranked"][0]["candidate"]["params"]["ram"]["source"]
        bad = client.post(f"/api/projects/{pid}/sourcing/pi/choose", json={"candidate": 5})
        assert bad.status_code == 422 and bad.json()["code"] == "sourcing_candidate_rejected"
        chosen = client.post(f"/api/projects/{pid}/sourcing/pi/choose", json={"candidate": 1}).json()
        assert chosen["pinned"]["name"] == "Raspberry Pi 5 (4 GB)"
        assert yaml.safe_load((root / "r3.lock.yaml").read_text())["sourcing"]["pi"]["catalog_id"] == chosen["pinned"]["catalog_id"]
        assert client.post(f"/api/projects/{pid}/sourcing/nope/search").json()["code"] == "sourcing_slot_not_found"
        assert client.get("/api/sourcing/jobs/missing").status_code == 404
        assert client.post(f"/api/projects/{pid}/sourcing/pi/unpin").json()["removed"]["name"] == "Raspberry Pi 5 (4 GB)"
