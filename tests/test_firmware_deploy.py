"""BLITZ project declaration and the twin launch paths (host process)."""

from __future__ import annotations

import base64
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from firmware_hal_stub import LockstepStub, serve

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tests/fixtures/bench/backend"


@pytest.fixture(scope="module")
def deploy() -> dict:
    return runpy.run_path(str(BACKEND / "deploy.py"), run_name="bench_deploy_test")


def test_blitz_module_declaration(deploy: dict) -> None:
    from backend.deployment.compilation.util.systems import Architecture, LinuxDistro
    from backend.deployment.module.base import VerificationResult
    from backend.deployment.module.layout import validate_backend_layout
    from backend.deployment.module.supported import PythonModule

    modules = deploy["get_modules"]()
    assert len(modules) == 1 and isinstance(modules[0], PythonModule)
    module = modules[0]
    assert module.name == "bench_controller" and module.entrypoint.file == "main.py"
    assert Path(module.module_folder_path) == BACKEND / "python/bench_controller"
    assert module.verify() == (VerificationResult.SUCCESS, "")
    assert module.targets.architectures == (Architecture.AARCH64,) and module.targets.distros == (LinuxDistro.DEBIAN_12,)
    assert not module.launch.accepts_blitz_flags
    validate_backend_layout(modules, deploy["modules"].backend_path)
    argv = module.get_run_argv("/opt/blitz/backend", "/opt/blitz")
    assert argv == ["/opt/blitz/.venv/bin/python", "-u", "/opt/blitz/backend/python/bench_controller/main.py"]


def test_one_controller_per_pi(deploy: dict) -> None:
    process = deploy["ProcessType"].BENCH_CONTROLLER
    assert process.get_name() == "bench-controller" and process.get_weight() == 1.0
    plan = deploy["pi_name_to_process_types"](["rig-1", "rig-2", "rig-3"])
    assert plan == {"rig-1": [process], "rig-2": [process], "rig-3": [process]}


def test_blitz_root_lookup_prefers_cicd(deploy: dict, tmp_path: Path) -> None:
    for layout in ("cicd/backend/deployment", "backend/deployment"):
        (tmp_path / layout).mkdir(parents=True)
        (tmp_path / layout / "deployer.py").write_text("")
    project = tmp_path / "examples/robots/x/backend"
    project.mkdir(parents=True)
    environment = os.environ.pop("BLITZ_ROOT", None)
    try:
        assert deploy["find_blitz_root"](project) == tmp_path / "cicd"
    finally:
        if environment is not None:
            os.environ["BLITZ_ROOT"] = environment


def test_config_supplier_is_base64_firmware_document(deploy: dict) -> None:
    document = json.loads(base64.b64decode(deploy["config_base64"]()))
    assert document["module"] == "bench_controller"
    assert document["config"]["control_rate_hz"] == 100 and document["config"]["radio_failsafe_timeout_s"] == .2
    assert document["bindings"]["crsf"] == {"interface": "uart", "device": "/dev/ttyAMA0", "baud": 420000}


def test_stage_hal_vendors_r3_hal(deploy: dict, tmp_path: Path) -> None:
    module = tmp_path / "bench_controller"
    module.mkdir()
    staged = deploy["stage_hal"](module)
    assert (staged / "__init__.py").is_file() and (staged / "virtual.py").is_file() and (staged / "linux.py").is_file()
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import vendor, r3_hal; print(r3_hal.__file__)")
    firmware = BACKEND / "python/bench_controller"
    (module / "vendor.py").write_text((firmware / "vendor.py").read_text())
    # -S: no site-packages, like a BLITZ bundle venv; an installed r3-platform (e.g. the R3
    # worker image) would otherwise provide r3_hal and the vendored copy is rightly unused.
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(module)], capture_output=True, text=True, check=True)
    assert result.stdout.strip().startswith(str(staged))


def test_host_process_mode_against_tcp_stub(tmp_path: Path) -> None:
    twin = runpy.run_path(str(BACKEND / "twin.py"), run_name="bench_twin_test")
    config = twin["write_config"](tmp_path / "firmware.json", "autonomous_course")
    assert json.loads(config.read_text())["module"] == "bench_controller"
    stub = LockstepStub()
    server = serve(stub, host="127.0.0.1")
    try:
        argv, environment, cwd = twin["host_command"](server.server_address[1], config)
        result = subprocess.run([*argv, "--duration", "0.5"], cwd=cwd, env={**os.environ, **environment},
                                capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stderr
    assert stub.done.is_set()
    assert len([r for r in stub.requests if r["op"] == "clock.sleep_until"]) == 50
    boot = next(entry for entry in stub.logs if entry["message"] == "boot")
    assert boot["fields"]["mission"] is False and "R3_FIRMWARE_CONFIG" in boot["fields"]["config_sources"]
