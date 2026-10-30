"""Agent-facing capabilities on unrelated synthetic fixtures; no external CAD calls."""

from pathlib import Path
import base64
import hashlib
import io
import json
import shutil
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image
import pytest

from r3_catalog.modelbank import ModelBank, ModelEntry
from r3_catalog.modelbank import service
from r3_hal.virtual import VirtualHal
from r3_model.project import Project
from r3_model.spec import ProjectDeviceSpec, Scenario
from r3_orchestrator import api, mcp_server as m, stages
from r3_sim.hal_server import TwinHalServer
from r3_sim.kernel import CoSimKernel
from r3_sim.project_devices import load_module

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project(tmp_path):
    from r3_github.scaffold import create_project
    root = tmp_path / "independent-instrument"
    create_project(root, template="blank", platform_ref="test")
    return root


def test_author_components_designs_and_scenarios_from_tools(project):
    component = {"id": "synthetic-coupon", "kind": "structure", "name": "Synthetic coupon",
        "mechanical": {"shape": "box", "size": [.04, .04, .004], "mass": .008, "material": "PETG", "density": 1270, "fabricated": True},
        "interfaces": {"face": {"kind": "face_mount", "origin": [0, 0, .004], "z": [0, 0, 1], "x": [1, 0, 0]}},
        "pins": {"A": {"role": "passive"}, "B": {"role": "passive"}},
        "connectors": {"J": {"type": "integral", "pins": {"1": "A", "2": "B"}}}}
    assert m.r3_put_catalog_component(component, project=str(project))["ok"]
    current = m.r3_get_spec(project=str(project))
    patch = {"components": {"a": {"type": component["id"]}}, "assembly": {"root": "a", "joins": []},
             "simulation": {"scenarios": [{"id": "observe", "duration": ".01 s", "expect": {"brownouts": 0}}]}}
    assert m.r3_patch_spec(patch, current["sha256"], project=str(project))["ok"]
    # An edit to the same project-local type leaves the shared catalog alone.
    component["mechanical"]["size"][0] = .05
    assert m.r3_put_catalog_component(component, project=str(project))["ok"]
    assert Project.load(project).catalog.component(component["id"]).mechanical.size[0] == .05
    assert m.r3_get_schema("robot")["schema"]["$defs"]["Simulation"]["properties"]["devices"]
    assert m.r3_get_schema("component")["schema"]["properties"]["interfaces"]
    # This invokes the actual standard-feature planner, with zero quota calls.
    planned = m.r3_cad_plan(project=str(project))
    assert planned["ok"] and planned["plan"]["summary"]["estimated_features_calls"] > 0
    features = [f["type"] for studio in planned["plan"]["part_studios"] for f in studio["features"]]
    from r3_onshape.policy import STANDARD_FEATURE_TYPES
    assert features and set(features) <= STANDARD_FEATURE_TYPES
    current = m.r3_get_spec(project=str(project))
    design = {"components": {"b": {"type": component["id"]}},
        "assembly": {"root": "a", "joins": [{"a": "a.face", "b": "b.face", "clock": 0}]},
        "electrical": {"harnesses": [{"id": "fixture-link",
            "connectors": {"A": {"type": "integral", "mates": "a.J"}, "B": {"type": "integral", "mates": "b.J"}},
            "wires": [{"id": "fixture-wire", "from": "A.1", "to": "B.1", "awg": 26}]}]}}
    assert m.r3_patch_spec(design, current["sha256"], project=str(project))["ok"]
    built = Project.load(project)
    assert len(built.spec.assembly.joins) == 1 and len(built.spec.electrical.harnesses[0].wires) == 1
    assert built.assembly is not None


def test_project_models_download_with_provenance_and_do_not_write_platform(project, tmp_path, monkeypatch):
    shared = tmp_path / "shared-bank"
    shared.mkdir()
    ModelBank(shared).put(ModelEntry(id="shared-fixture", name="Unrelated fixture", category="other"))
    before = (shared / "shared-fixture.yaml").read_bytes()
    monkeypatch.setenv("R3_PROJECT", str(project))
    monkeypatch.setenv("R3_MODEL_BANK", str(shared))
    monkeypatch.setenv("R3_MODEL_CACHE", str(tmp_path / "cache"))
    payload = b"ISO-10303-21; synthetic transport fixture; END-ISO-10303-21;"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    # Acquisition is real HTTP; conversion is covered by native STEP integration
    # tests. These synthetic bytes deliberately contain no authored part CAD.
    monkeypatch.setattr(service, "convert", lambda path, source: {"bbox_m": [.01, .02, .03]})
    try:
        added = m.r3_models_add("Synthetic transport fixture", "other", url=f"http://127.0.0.1:{server.server_port}/fixture.step",
                                 model_id="project-fixture", license="Synthetic test data")
        assert added["ok"] and added["model"]["sources"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
        assert (project / "catalog/modelbank/project-fixture.yaml").is_file()
        assert ModelBank().get("shared-fixture").id == "shared-fixture"
        assert not (shared / "project-fixture.yaml").exists()
        assert (shared / "shared-fixture.yaml").read_bytes() == before
    finally:
        server.shutdown()
        server.server_close()


def test_public_onshape_discovery_uses_connected_transport(project, monkeypatch):
    monkeypatch.setenv("R3_PROJECT", str(project))
    from r3_onshape import client

    class API:
        def json(self, method, path, **kwargs):
            assert method == "GET" and path == "/documents/" + "a" * 24
            return {"isPublic": True}

    monkeypatch.setattr(client, "default_transport", lambda: API())
    result = m.r3_models_add("Synthetic public fixture", "other", model_id="public-fixture",
        onshape=f"https://cad.onshape.com/documents/{'a'*24}/v/{'b'*24}/e/{'c'*24}")
    assert result["ok"] and result["model"]["sources"][0]["kind"] == "onshape_public"


def test_onshape_render_is_delivered_as_an_image(project):
    from r3_onshape.views import ElementRef, render_views
    image = Image.new("RGB", (16, 12), "orange")
    stream = io.BytesIO()
    image.save(stream, format="PNG")

    class API:
        def json(self, method, path, query):
            assert method == "GET" and path.endswith("/shadedviews")
            return {"images": [base64.b64encode(stream.getvalue()).decode()]}

    render_views(API(), ElementRef("D", "w", "W", "E"), project / "build/cad/views", views=["iso"])
    result = m.r3_view_artifact("cad/views/iso.png", project=str(project))
    assert result.content[1].type == "image" and result.content[1].mime_type == "image/png"
    assert base64.b64decode(result.content[1].data) == stream.getvalue()


def test_project_test_suite_passes_fails_and_invalidates_evidence(project):
    tests = project / "tests"
    tests.mkdir()
    test = tests / "test_design.py"
    test.write_text("from pathlib import Path\ndef test_artifact():\n    assert (Path.cwd() / 'robot.yaml').is_file()\n")
    passed = m.r3_run_project_tests(project=str(project))
    assert passed["ok"] and passed["stages"][0]["metrics"]["executed"] == 1
    old = Project.load(project).fingerprint()
    test.write_text("def test_design_limit():\n    assert 2 < 1\n")
    assert Project.load(project).fingerprint()["project_sources_revision"] != old["project_sources_revision"]
    failed = m.r3_run_project_tests(project=str(project))
    assert not failed["ok"]
    assert any(d["code"] == "project_tests_failed" for d in failed["diagnostics"])


def test_project_device_runs_with_native_physics_and_real_hal(tmp_path):
    root = tmp_path / "instrument-bench"
    shutil.copytree(FIXTURES / "bench", root)
    (root / "simulation").mkdir()
    shutil.copy(FIXTURES / "project-capabilities/probe.py", root / "simulation/probe.py")
    project = Project.load(root)
    project.spec.simulation.devices["probe"] = ProjectDeviceSpec(component="base", source="simulation/probe.py")
    kernel = CoSimKernel(project, Scenario(id="instrument-observe", duration=.05))
    server = TwinHalServer(kernel)
    result = {}

    def client():
        hal = VirtualHal(server.endpoint)
        try:
            result["sample"] = hal.device_request("probe", "position", timeout=.01)
        except BaseException as error:
            result["error"] = error
        finally:
            hal.close()

    thread = threading.Thread(target=client)
    thread.start()
    try:
        assert server.accept(10)
        while server.serve_one() == "ok":
            pass
        thread.join(10)
        assert not thread.is_alive()
        if "error" in result:
            raise result["error"]
        assert result["sample"]["steps"] >= 1 and result["sample"]["t"] > 0
        import mujoco
        body = mujoco.mj_name2id(kernel.vehicle.model, mujoco.mjtObj.mjOBJ_BODY, "base")
        np.testing.assert_allclose(result["sample"]["xyz"], kernel.vehicle.data.xpos[body])
    finally:
        server.close()


def test_device_sources_cannot_escape_project(project, tmp_path):
    p = Project.load(project)
    p.spec.simulation.devices["probe"] = ProjectDeviceSpec(component="a", source="simulation/../../other.py")
    with pytest.raises(ValueError, match="inside this project"):
        load_module(p, "probe")


def test_project_electrical_factory_uses_native_circuit_engine(tmp_path):
    from r3_sim.electrical.system import ElectricalSystem
    from r3_sim.electrical.models import ConstantLoad
    root = tmp_path / "circuit-bench"
    shutil.copytree(FIXTURES / "bench", root)
    (root / "simulation").mkdir()
    shutil.copy(FIXTURES / "project-capabilities/probe.py", root / "simulation/probe.py")
    project = Project.load(root)
    component = "gps"  # unrelated stationary instrument fixture
    entry = project.catalog.component(project.spec.components[component].type)
    entry.behavior.model = "project.instrument"
    entry.behavior.params["current"] = .015
    project.spec.simulation.devices["instrument"] = ProjectDeviceSpec(component=component, source="simulation/probe.py",
                                                                       electrical_factory="create_electrical")
    circuit = ElectricalSystem(project)
    assert isinstance(circuit.models[component], ConstantLoad)
    assert circuit.models[component].current == .015
    outputs = circuit.operating_point({})
    assert outputs[component]["powered"]
    assert outputs[component]["current"] == pytest.approx(.015, rel=.01)


@pytest.mark.parametrize("folder", ["backend/python/logic", "simulation", "tests"])
def test_authored_source_changes_invalidate_stage_evidence(project, folder):
    before = Project.load(project).fingerprint()
    path = project / folder / "logic.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ANSWER = 2\n")
    after = Project.load(project).fingerprint()
    from r3_orchestrator.release import is_stale
    assert is_stale(before, after)


def test_skipping_all_project_tests_is_not_a_pass(project):
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_design.py").write_text("import pytest\n@pytest.mark.skip(reason='not measured')\ndef test_design():\n    pass\n")
    result = m.r3_run_project_tests(project=str(project))
    assert not result["ok"] and result["stages"][0]["metrics"]["executed"] == 0


def test_catalog_replacement_rolls_back_existing_entry(project, monkeypatch):
    entry = {"id": "coupon", "name": "Synthetic coupon", "kind": "structure", "mechanical": {"size": [.01, .02, .003], "mass": .001}}
    assert m.r3_put_catalog_component(entry, project=str(project))["ok"]
    path = project / "catalog/components/coupon.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(api.Project, "load", classmethod(lambda *args: (_ for _ in ()).throw(ValueError("synthetic catalog failure"))))
    entry["mechanical"]["mass"] = .002
    assert not api.put_catalog_component(project, entry)["ok"]
    assert path.read_bytes() == before


def test_session_mcp_transports_schema_and_real_image_blocks(project):
    """Use the exact stdio command/environment an Agent-tab session receives."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from r3_orchestrator.api.agents import mcp_config
    image = project / "build/cad/views/synthetic.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "green").save(image)
    server = mcp_config(project)["mcpServers"]["r3"]

    async def check():
        params = StdioServerParameters(command=server["command"], args=server["args"], env=server["env"], cwd=str(project))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"r3_get_schema", "r3_cad_plan", "r3_view_artifact", "r3_run_project_tests"} <= names
            result = await session.call_tool("r3_view_artifact", {"path": "cad/views/synthetic.png"})
            assert any(block.type == "image" for block in result.content)
            assert not result.is_error
            schema = await session.call_tool("r3_get_schema", {"domain": "device"})
            assert not schema.is_error and "electrical_factory" in schema.content[0].text

    asyncio.run(asyncio.wait_for(check(), 60))
