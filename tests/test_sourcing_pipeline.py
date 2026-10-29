"""Sourcing end to end on the RC car (recorded fixtures, no network): search, rank with twin
checks, choose, lockfile pinning, catalog generation, the model stage, CLI, MCP, HTTP API and CAD import."""

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
                              sourced_rc_car)

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
    found = {slot: sourcing.search_slot(project, slot, providers=providers(tmp_path, monkeypatch)) for slot in ("pi", "receiver", "front_wheels")}
    chosen = {slot: sourcing.choose(root, slot, 1) for slot in found}
    return {"found": found, "chosen": chosen}


def test_rc_car_sources_wheel_receiver_and_pi_and_passes_the_model_stage(tmp_path, monkeypatch):
    baseline_root = sourced_rc_car(tmp_path / "plain", sources={})
    baseline = model_stage.run(Project.load(baseline_root))
    root = sourced_rc_car(tmp_path)

    before = model_stage.run(Project.load(root))
    unmet = [d for d in before.diagnostics if d.code == "source_requirement_unmet"]
    assert [d.data["param"] for d in unmet] == ["hub"]  # the authored 6 mm plain-bore wheel is not a 12 mm hex wheel
    assert set(unmet[0].subjects) == {"wheel_fl", "wheel_fr"}

    out = source_all(root, tmp_path, monkeypatch)
    pi, receiver, wheels = (out["found"][s] for s in ("pi", "receiver", "front_wheels"))
    assert pi["counts"] == {"eligible": 3, "unverified": 0, "rejected": 2}
    assert pi["ranked"][0]["candidate"]["name"] == "Raspberry Pi 5 (4 GB)" and pi["ranked"][0]["unit_price"] == 110.0
    assert receiver["ranked"][0]["candidate"]["name"] == "RadioMaster RP1 2.4GHz RX"  # the incumbent wins the tie
    assert receiver["counts"]["eligible"] > 100 and receiver["template"] == "elrs-receiver-2g4"
    tiers = {r["candidate"]["mpn"]: r["tier"] for r in wheels["ranked"]}
    assert tiers == {"FW-110-45-HEX12": "eligible", "FW-108-42-HEX12": "eligible", "FW-95-40-HEX12": "rejected", "FW-112": "unverified"}
    best = wheels["ranked"][0]
    assert best["candidate"]["mpn"] == "FW-110-45-HEX12"  # FW-108-42 has an uncited (estimated) hub
    codes = {c["code"] for c in best["twin"]["checks"]}
    assert "sourcing_interface_feature_changed" in codes  # hub_fl/hub_fr ran on the 6 mm bore; a hex hub needs an adapter
    assert not best["twin"]["errors"]
    mass = next(c for c in best["twin"]["checks"] if c["code"] == "sourcing_mass_budget")
    assert mass["data"]["delta"] == pytest.approx(2 * (0.092 - 0.085))
    assert all(r["ok"] for r in out["chosen"].values()), out["chosen"]

    lock = yaml.safe_load((root / "r3.lock.yaml").read_text())
    assert lock["cad"]["document_id"]  # existing lock sections are preserved
    assert set(lock["sourcing"]) == {"pi", "receiver", "front_wheels"}
    pin = lock["sourcing"]["front_wheels"]
    assert pin["components"] == ["wheel_fl", "wheel_fr"] and pin["price"] == {"amount": 7.5, "currency": "USD", "quantity": 2}
    assert pin["vendor"] == "Fixture Hobby Shop" and pin["url"] == "https://example.com/shop/fw-110-45" and pin["mpn"] == "FW-110-45-HEX12"
    assert lock["sourcing"]["pi"]["cad"] == "vendor_step" and lock["sourcing"]["receiver"]["cad"] == "proxy"
    for slot_pin in lock["sourcing"].values():
        assert (root / slot_pin["catalog_file"]).is_file() and slot_pin["digest"] and slot_pin["retrieved_at"] > 0

    project = Project.load(root)
    assert project.spec.components["wheel_fl"].type == project.spec.components["wheel_fr"].type == pin["catalog_id"]
    assert project.spec.components["pi"].type.startswith("sourced-raspberrypi")
    assert "sourcing_sha256" in project.fingerprint()
    after = model_stage.run(project)
    non_info = {d.code for d in after.diagnostics if d.severity.value != "info"}
    assert not non_info & ELECTRICAL_CODES, [d.message for d in after.diagnostics if d.code in ELECTRICAL_CODES]
    assert not {c for c in non_info if c.startswith(("sourc", "component_unsourced"))}, non_info
    assert "mass_exceeds_requirement" not in non_info and after.metrics["mass_margin"] > 0
    # No new interference: every overlap is one the authored layout already had.
    assert interference_pairs(after) <= interference_pairs(baseline)
    graph = json.loads((root / "build" / "model" / "graph.json").read_text())
    assert graph["components"]["wheel_fl"]


def test_catalog_entry_provenance_per_field(tmp_path, monkeypatch):
    root = sourced_rc_car(tmp_path)
    source_all(root, tmp_path, monkeypatch)
    lock = yaml.safe_load((root / "r3.lock.yaml").read_text())["sourcing"]
    wheel = yaml.safe_load((root / lock["front_wheels"]["catalog_file"]).read_text())
    (entry,) = wheel["components"]
    meta = wheel["sourced"]
    assert entry["mechanical"]["size"] == pytest.approx([0.11, 0.045]) and entry["mechanical"]["mass"] == pytest.approx(0.092)
    hub = next(f for f in entry["mechanical"]["features"] if f["id"] == "hub_hex")
    assert hub["kind"] == "socket" and hub["diameter"] == pytest.approx(0.012)
    assert hub["provenance"] == {"status": "vendor", "source": "https://example.com/wheels/fw-110-45",
                                 "notes": hub["provenance"]["notes"]}
    assert not any(f["id"] == "hub_bore" for f in entry["mechanical"]["features"])
    assert meta["fields"]["mechanical.size"]["status"] == "vendor" and meta["fields"]["mechanical.mass"]["status"] == "vendor"
    assert meta["fields"]["behavior.params.radius"]["source"] == "https://example.com/wheels/fw-110-45"
    assert meta["template"] == "wheel-110x45" and meta["cad"]["status"] == "proxy"
    assert entry["behavior"]["provenance"]["status"] == "estimated"  # inherited from a different product
    pi = yaml.safe_load((root / lock["pi"]["catalog_file"]).read_text())
    assert pi["sourced"]["fields"]["mechanical.mount_holes"]["status"] == "datasheet"
    assert pi["components"][0]["pins"]["5V"]["current_max"] == 5.0
    assert pi["sourced"]["fields"]["pins.5V.current_max"]["source"].endswith("product-brief.pdf")
    assert any(m["format"] == "step_zip" for m in pi["sourced"]["models"])


def test_pins_are_reproducible_and_guarded(tmp_path, monkeypatch):
    root = sourced_rc_car(tmp_path, sources={"pi": PI_SOURCE})
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
    codes = {d.code for d in model_stage.run(Project.load(root)).diagnostics}
    assert "sourcing_digest_mismatch" in codes

    spec = yaml.safe_load((root / "robot.yaml").read_text())
    spec["components"]["pi"]["source"]["requires"]["ram"] = ">= 8 GB"
    (root / "robot.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    assert "sourcing_pin_stale" in {d.code for d in sourcing.check_pins(Project.load(root))}
    assert sourcing.choose(root, "pi", 1)["code"] == "sourcing_search_stale"

    path.unlink()
    assert "sourcing_catalog_missing" in {d.code for d in sourcing.check_pins(Project.load(root))}
    assert sourcing.unpin(root, "pi")["removed"]["name"] == "Raspberry Pi 5 (8 GB)"
    assert Project.load(root).spec.components["pi"].type == "raspberry-pi-5-8gb"


def test_source_only_slot_uses_a_template_by_connectors(tmp_path, monkeypatch):
    root = sourced_rc_car(tmp_path, sources={"receiver": RECEIVER_SOURCE}, drop_type=("receiver",))
    project = Project.load(root)
    assert "component_unsourced" in {d.code for d in project.graph.diagnostics}
    found = sourcing.search_slot(project, "receiver", providers=providers(tmp_path, monkeypatch))
    assert found["template"] == "elrs-receiver-2g4" and found["current"] is None  # the only radio providing J1
    assert sourcing.choose(root, "receiver", 1)["ok"]
    result = model_stage.run(Project.load(root))
    assert not {d.code for d in result.diagnostics} & (ELECTRICAL_CODES | {"component_unsourced"})


def test_list_slots(tmp_path, monkeypatch):
    root = sourced_rc_car(tmp_path)
    listed = sourcing.list_slots(Project.load(root))
    slots = {s["slot"]: s for s in listed["slots"]}
    assert set(slots) == {"pi", "receiver", "front_wheels"} and slots["front_wheels"]["quantity"] == 2
    assert slots["front_wheels"]["budget"] == {"amount": 40.0, "currency": "USD"}
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

    root = sourced_rc_car(tmp_path, sources={"pi": PI_SOURCE, "receiver": RECEIVER_SOURCE})
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

    root = sourced_rc_car(tmp_path, sources={"pi": PI_SOURCE})
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
    root = sourced_rc_car(projects, sources={"pi": PI_SOURCE, "receiver": RECEIVER_SOURCE})
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


# ------------------------------------------------------------------ CAD import
class FakeOnshape:
    name = "fake-onshape"

    def __init__(self, extents):
        self.extents = extents
        self.calls: list[tuple[str, str, object]] = []

    def upload_multipart(self, path, fields, file_name, data):
        self.calls.append(("UPLOAD", path, {"fields": fields, "file": file_name, "bytes": len(data)}))
        return {"id": "tr1", "requestState": "ACTIVE"}

    def json(self, method, path, query=None, body=None):
        from r3_onshape.policy import check_request

        check_request(method, path, body)
        self.calls.append((method, path, body))
        if path == "/translations/tr1":
            return {"id": "tr1", "requestState": "DONE", "resultElementIds": ["e" * 24]}
        if path.endswith("/boundingboxes"):
            x, y, z = self.extents
            return {"lowX": 0.01, "lowY": -0.02, "lowZ": 0.003, "highX": 0.01 + x, "highY": -0.02 + y, "highZ": 0.003 + z}
        return {"feature": body["feature"] if body else None}


def test_import_vendor_step_and_add_mate_connectors(tmp_path, monkeypatch):
    from r3_onshape import sourced

    monkeypatch.setattr(sourced.time, "sleep", lambda s: None)
    root = sourced_rc_car(tmp_path, sources={"pi": PI_SOURCE, "receiver": RECEIVER_SOURCE})
    project = Project.load(root)
    for slot in ("pi", "receiver"):
        sourcing.search_slot(project, slot, providers=providers(tmp_path, monkeypatch))
        sourcing.choose(root, slot, 1)
    project = Project.load(root)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("rpi5/RPi5.step", "ISO-10303-21; fixture")
    http = CassetteHttp(tmp_path, [])
    monkeypatch.setattr(http, "_fetch", lambda m, u, b, h, raw=archive.getvalue(): sourced_response(u, raw))
    transport = FakeOnshape([0.085, 0.056, 0.0176])
    out = sourced.import_sourced(project, transport=transport, http=http)
    results = {r["slot"]: r for r in out["slots"]}
    assert results["receiver"]["status"] == "proxy"
    pi = results["pi"]
    assert pi["status"] == "imported" and pi["code"] == "sourced_cad_imported", pi
    assert pi["step"].endswith("RPi5.step") and "extents match" in pi["frame_check"]
    upload = transport.calls[0]
    assert upload[0] == "UPLOAD" and upload[1].startswith("/api/v17/translations/d/") and upload[2]["fields"]["formatName"] == "STEP"
    features = [body["feature"] for method, path, body in transport.calls if method == "POST" and path.endswith("/features")]
    assert {f["featureType"] for f in features} == {"mateConnector"} and len(features) >= 5  # 4 mounting holes + J8
    flipped = [f for f in features if any(p.get("parameterId") == "flipPrimary" for p in f["parameters"])]
    assert len(flipped) == 4  # hole axes point -z
    assert any(entry.endswith("No%20Graphics%20small%20file.zip") for entry in http.log)  # the official STEP was fetched

    transport_bad = FakeOnshape([0.056, 0.085, 0.0176])  # rotated model
    again = sourced.import_sourced(project, slots=["pi"], transport=transport_bad, http=http)
    assert again["slots"][0]["code"] == "sourced_cad_frame_unverified"


def sourced_response(url, raw):
    from r3_catalog.sourcing.http import Response

    return Response(url, 200, raw, {"Content-Type": "application/zip"})


def test_import_without_transport_is_skipped(tmp_path, monkeypatch):
    from r3_onshape import sourced

    root = sourced_rc_car(tmp_path, sources={"pi": PI_SOURCE})
    sourcing.search_slot(Project.load(root), "pi", providers=providers(tmp_path, monkeypatch))
    sourcing.choose(root, "pi", 1)

    class Browserish:
        name = "onshape-native"

    out = sourced.import_sourced(Project.load(root), transport=Browserish())
    assert out["slots"][0]["code"] == "sourced_cad_transport_missing"
