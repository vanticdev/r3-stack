"""Run project-authored Python tests without modifying or importing platform tests."""

from __future__ import annotations

import os
import logging
import math
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project

from .environment import r3_pythonpath

_LOG = logging.getLogger(__name__)
_PROGRESS_INTERVAL = 30.0


def run(project: Project, timeout: float | None = None) -> StageResult:
    result = StageResult(stage="tests", inputs=project.fingerprint())
    timeout = project.spec.testing.timeout if timeout is None else timeout
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        result.add(Diagnostic.error("project_tests_invalid", "Project test timeout must be finite and positive"))
        return result
    result.metrics["timeout_s"] = timeout
    directory = project.root / "tests"
    if not directory.exists() or not list(directory.rglob("test_*.py")):
        result.add(Diagnostic.info("not_applicable", "No project-authored tests under tests/test_*.py"))
        return result
    root = project.root.resolve()
    for path in directory.rglob("*"):
        if path.is_symlink() and root not in path.resolve().parents:
            result.add(Diagnostic.error("project_tests_invalid", "Test source points outside this project", subjects=[str(path.relative_to(root))]))
            return result
    output = project.build_dir("tests")
    report, log = output / "junit.xml", output / "pytest.log"
    report.unlink(missing_ok=True)
    # Explicit config and root prevent the containing platform's test discovery
    # or old robot fixtures from becoming part of an independent project suite.
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", "-c", os.devnull,
               "--rootdir", str(root), "--junitxml", str(report), str(directory)]
    started = time.monotonic()
    try:
        with log.open("w") as stream:
            process = subprocess.Popen(command, cwd=root, env={**os.environ, "PYTHONPATH": r3_pythonpath(), "R3_PROJECT": str(root)},
                                       stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    process.wait(timeout=max(0, min(_PROGRESS_INTERVAL, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - started
                    _LOG.info("Project tests still running: %.1f s elapsed, %.1f s budget; %d bytes in tests/pytest.log",
                              elapsed, timeout, log.stat().st_size)
                    if time.monotonic() < deadline:
                        continue
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait()
                    raise
        if not report.exists():
            raise ValueError(f"pytest did not produce a report (exit {process.returncode}); see tests/pytest.log")
        suites = ET.parse(report).getroot()
        cases = list(suites.iter("testcase"))
        skipped = sum(case.find("skipped") is not None for case in cases)
        failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
        result.metrics.update(collected=len(cases), executed=len(cases) - skipped, skipped=skipped, failed=failed,
                              returncode=process.returncode)
        result.metrics["cases"] = {
            f"{case.get('classname', '')}::{case.get('name', '')}": {
                "status": "skipped" if case.find("skipped") is not None else
                          "failed" if case.find("failure") is not None or case.find("error") is not None else "passed"}
            for case in cases}
        if process.returncode or failed or len(cases) == skipped:
            result.add(Diagnostic.error("project_tests_failed", f"Project tests: exit {process.returncode}, {failed} failures, "
                                        f"{len(cases) - skipped} executed; see tests/pytest.log",
                                        data={key: value for key, value in result.metrics.items() if isinstance(value, (int, float))}))
    except subprocess.TimeoutExpired:
        result.add(Diagnostic.error("project_tests_timeout", f"Project tests exceeded {timeout:g} seconds",
                                    data={"timeout_s": timeout}, hint=(
                                        "Inspect tests/pytest.log. For a legitimately longer native experiment, set testing.timeout "
                                        "through the spec tools; preserve test assertions and physical scenario durations.")))
    except (OSError, ValueError, ET.ParseError) as error:
        result.add(Diagnostic.error("project_tests_failed", str(error)))
    result.duration_s = time.monotonic() - started
    result.artifact("tests/pytest.log", "text", "Project-authored pytest output")
    if report.exists():
        result.artifact("tests/junit.xml", "xml", "Project test results")
    return result
