"""Fake pipeline stages for the API run tests (imported by the spawned run worker).

``run`` prints, logs, reports progress and adds a diagnostic, so every event
type of the run SSE stream appears. ``R3_FAKE_STAGE_SLEEP`` makes it hang (for
cancellation). ``crash`` raises, to exercise ``stage_crashed``.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project


def run(project: Project, progress: Callable[[str], None] | None = None) -> StageResult:
    result = StageResult(stage="model", inputs=project.fingerprint())
    print("fake stage: checking", project.spec.robot.name)
    logging.getLogger("r3.fake").info("fake stage logging works")
    if progress:
        progress("halfway")
    time.sleep(float(os.environ.get("R3_FAKE_STAGE_SLEEP", "0")))
    result.add(Diagnostic.warning("fake_warning", "a fake warning", subjects=["battery"], data={"value": 1.5}))
    result.metrics["answer"] = 42
    out = project.build_dir("model")
    (out / "fake.txt").write_text("fake artifact\n")
    result.artifact("model/fake.txt", "log", "fake artifact")
    return result


def crash(project: Project) -> StageResult:
    raise RuntimeError("fake stage exploded")
