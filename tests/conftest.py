"""Shared, session-scoped fixtures: expensive stages run once into temporary projects.

* ``bench_wiring``: the full wiring stage on a fresh copy of the reference bench's
  ``robot.yaml`` (explicit synthetic pulled CAD; nothing read from ``tests/fixtures/bench/build``).
  Every test that uses it is marked ``slow`` automatically.
* ``bench_fresh``: a copy of the bench's sources without ``build/`` (module scope), so
  simulation tests never pick up stale CAD exports or ``routes.json`` from earlier runs.
* ``small_wiring`` / ``branch_wiring``: :func:`r3_wiring.stage.route_and_check` on the
  synthetic projects of :mod:`test_wiring_support` (one connector pair; every
  termination kind), for fast router/planner tests.

Fixture projects are copied from sources before each module uses them. Every
artifact read by a test is generated in its temporary project; local build
folders never affect the suite.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

BENCH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bench"
EXAMPLES = BENCH.parent.resolve()
_SOURCE_ONLY = shutil.ignore_patterns("build", "__pycache__", "_vendor")


class _ExampleCopies:
    """Source-only copies of example projects for the current test module."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.module = "session"
        self.copies: dict[tuple[str, Path], Path] = {}

    def redirect(self, root: Path) -> Path:
        path = Path(root).resolve()
        if path.parent != EXAMPLES:
            return path
        key = (self.module, path)
        if key not in self.copies:
            target = self.base / self.module.replace("/", "_").replace(".", "_") / path.name
            shutil.copytree(path, target, ignore=_SOURCE_ONLY)
            self.copies[key] = target
        return self.copies[key]


@pytest.fixture(scope="session", autouse=True)
def _examples_without_build(tmp_path_factory: pytest.TempPathFactory):
    from r3_model.project import Project

    copies = _ExampleCopies(tmp_path_factory.mktemp("example-sources"))
    original = Project.__dict__["load"]
    old_templates = os.environ.get("R3_EXAMPLES_DIR")
    os.environ["R3_EXAMPLES_DIR"] = str(EXAMPLES)

    def load(cls, root, *, resolve_cad=True):
        return original.__func__(cls, copies.redirect(root), resolve_cad=resolve_cad)

    Project.load = classmethod(load)
    yield copies
    Project.load = original
    if old_templates is None:
        os.environ.pop("R3_EXAMPLES_DIR", None)
    else:
        os.environ["R3_EXAMPLES_DIR"] = old_templates


@pytest.fixture(scope="module", autouse=True)
def _example_copy_per_module(request: pytest.FixtureRequest, _examples_without_build: _ExampleCopies):
    _examples_without_build.module = request.module.__name__
    yield


@dataclass
class WiringRun:
    root: Path
    project: Any  # r3_model.project.Project
    bundle: dict[str, Any]  # r3_wiring.stage.route_and_check (plus "drive" for full stage runs)
    result: Any = None  # r3_model.diagnostics.StageResult of a full stage run


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "bench_wiring" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.slow)


def copy_bench(root: Path) -> Path:
    """A fresh bench project: ``robot.yaml`` only (no build artifacts)."""
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BENCH, root, dirs_exist_ok=True, ignore=_SOURCE_ONLY)
    return root


@pytest.fixture(scope="module")
def bench_fresh(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bench-fresh") / "bench"
    shutil.copytree(BENCH, root, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return root


@pytest.fixture(scope="session")
def bench_wiring(tmp_path_factory: pytest.TempPathFactory) -> WiringRun:
    """The full bench wiring stage, once per session (artifacts under ``root/build/wiring``)."""
    from r3_model.project import Project
    from r3_wiring.stage import format_diagnostics, run_stage

    root = tmp_path_factory.mktemp("bench-wiring")
    shutil.copytree(EXAMPLES / "wiring", root, dirs_exist_ok=True, ignore=_SOURCE_ONLY)
    from native_wiring_fixture import publish_wiring_pull
    publish_wiring_pull(root)
    project = Project.load(root)
    result, bundle = run_stage(project)
    print("\n" + format_diagnostics(result))
    print(f"wiring stage: {result.duration_s:.0f} s;", "harness lengths (mm):",
          {k: round(v * 1e3, 1) for k, v in result.metrics["harness_length"].items()})
    return WiringRun(root, project, bundle, result)


@pytest.fixture(scope="session")
def small_wiring(tmp_path_factory: pytest.TempPathFactory) -> WiringRun:
    from r3_model.project import Project
    from r3_wiring.stage import route_and_check
    from test_wiring_support import write_project

    root = write_project(tmp_path_factory.mktemp("small-wiring"), block=True, slack="30 mm")
    project = Project.load(root)
    return WiringRun(root, project, route_and_check(project))


@pytest.fixture(scope="session")
def branch_wiring(tmp_path_factory: pytest.TempPathFactory) -> WiringRun:
    from r3_model.project import Project
    from r3_wiring.stage import route_and_check
    from test_wiring_support import write_branch_project

    root = write_branch_project(tmp_path_factory.mktemp("branch-wiring"))
    project = Project.load(root)
    return WiringRun(root, project, route_and_check(project))


@pytest.fixture(scope="session")
def pcb_project(tmp_path_factory: pytest.TempPathFactory):
    """Generate a passive coupon with KiCad in a temporary project, once per session."""
    from r3_kicad.flow import Flow
    from r3_kicad.library import find_install
    from r3_model.project import Project
    if find_install() is None:
        pytest.skip("KiCad (kicad-cli + pcbnew) not installed")
    root = tmp_path_factory.mktemp("pcb-coupon")
    shutil.copytree(EXAMPLES / "pcb", root, dirs_exist_ok=True, ignore=_SOURCE_ONLY)
    record = Flow(root / "pcb/test-board").run(watch_interval=.05)
    return Project.load(root), record
