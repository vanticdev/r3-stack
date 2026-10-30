"""Session import paths and doctor must survive changing to a fresh project cwd."""

import os
from pathlib import Path
import subprocess
import sys
import tomllib

from r3_orchestrator.environment import r3_pythonpath
from r3_orchestrator import doctor


def test_session_roots_follow_packaging_manifest(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "pyproject.toml").read_text())
    expected = {(root / entry).resolve() for entry in manifest["tool"]["setuptools"]["packages"]["find"]["where"]}
    assert expected <= {Path(entry) for entry in r3_pythonpath().split(os.pathsep)}
    process = subprocess.run([sys.executable, "-c", "import r3_connectors.stage, r3_fab.stage; print('ok')"],
                             cwd=tmp_path, env={**os.environ, "PYTHONPATH": r3_pythonpath()},
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr


def test_doctor_imports_every_stage_and_reports_missing_transitive_dependency(monkeypatch):
    from r3_orchestrator.stages import StageSpec

    monkeypatch.setattr(doctor, "ALL_STAGES", {"missing": StageSpec("missing", "r3_does_not_exist.stage", "test")})
    result = doctor.probe_stage_imports()
    assert not result["missing"]["available"]
    assert "ModuleNotFoundError" in result["missing"]["detail"]


def test_doctor_refuses_unimportable_requested_stage(monkeypatch):
    from test_headless_env import _fake_tools

    _fake_tools(monkeypatch)
    monkeypatch.setattr(doctor, "probe_stage_imports", lambda: {
        "fabrication": {"available": False, "module": "r3_fab.stage", "detail": "ModuleNotFoundError: mesh dependency"}})
    result = doctor.run_doctor(["fabrication"])
    assert not result["ok"] and result["code"] == "stage_not_importable"
    stage = next(s for s in result["stages"] if s["stage"] == "fabrication")
    assert stage["level"] == "unavailable" and not stage["headless"]
