"""Owned parts remain in the model but never enter the purchase/sourcing flow."""
import pytest
from pydantic import ValidationError

from r3_model.project import Project
from r3_model.spec import ComponentInstance
from r3_orchestrator import sourcing
from r3_orchestrator.purchasing import purchase_summary


@pytest.mark.parametrize("owned", [True, {"note": "workbench"}])
def test_owned_spec(owned):
    part = ComponentInstance(type="raspberry-pi-5-4gb", owned=owned)
    assert part.owned
    with pytest.raises(ValidationError) as error:
        ComponentInstance(type="raspberry-pi-5-4gb", owned=owned, source={"requires": {"kind": "computer"}})
    assert error.value.errors()[0]["type"] == "owned_source_conflict"


def project(tmp_path):
    (tmp_path / "robot.yaml").write_text('robot: {name: owned-test}\ncomponents:\n  pi: {type: raspberry-pi-5-4gb, owned: {note: workbench}}\n  buy: {type: raspberry-pi-5-4gb, source: {requires: {kind: computer}}}\n')
    return Project.load(tmp_path)


def test_purchase_bom_and_sourcing_skip(tmp_path, monkeypatch):
    p = project(tmp_path)
    p.lock = {"sourcing": {"buy": {"price": {"amount": 110, "currency": "USD"}}}}
    summary = purchase_summary(p)
    assert summary["to_buy_totals"] == {"USD": 110}
    assert summary["to_buy_count"] == 1
    assert summary["lines"][0]["status"] == "owned"
    assert summary["lines"][0]["purchase_cost"] == 0
    assert summary["lines"][0]["note"] == "workbench"
    monkeypatch.setattr(sourcing, "make_providers", lambda *a, **kw: pytest.fail("provider called"))
    with pytest.raises(sourcing.SourcingError):
        sourcing.search_slot(p, "pi")
    assert [s["slot"] for s in sourcing.list_slots(p)["slots"]] == ["buy"]
    assert sourcing.list_slots(p)["purchases"] == summary


def test_owned_http_field(tmp_path):
    from fastapi.testclient import TestClient
    from r3_orchestrator.api.server import create_app
    project(tmp_path)
    with TestClient(create_app([tmp_path], web_dist=tmp_path / "dist")) as client:
        pid = client.get("/api/projects").json()["projects"][0]["id"]
        response = client.get(f"/api/projects/{pid}/sourcing")
        assert response.status_code == 200
        assert response.json()["purchases"]["lines"][0]["owned"] is True


def test_owned_board_not_priced(tmp_path, monkeypatch):
    p = project(tmp_path)
    from r3_model.spec import Board
    p.spec.pcb.boards = [Board(component="pi", source="pcb/owned-board")]
    build = tmp_path / "build" / "pcb"
    build.mkdir(parents=True)
    (build / "owned-board-bom.csv").write_text("MPN,Qty\nSHOULD-NOT-QUERY,1\n")
    monkeypatch.setattr(sourcing, "make_providers", lambda *a, **kw: [])
    assert sourcing.price_bom(p)["lines"] == []


def test_report_and_release_include_owned(tmp_path):
    from r3_orchestrator import report_stage, release
    p = project(tmp_path)
    summary = report_stage.build_summary(p, {})
    assert summary["purchases"]["lines"][0]["owned"]
    assert "owned" in report_stage.render_html(summary, tmp_path / "build")
    assert release.evaluate(p)["purchases"] == summary["purchases"]
    result = report_stage.run(p)
    assert (tmp_path / "build/report/bom.csv").is_file()
