"""Run the cad stage on the RC car (headless Onshape session) and print diagnostics.

    python .r3-runs/cad_attempt.py [--force]
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

from r3_model.project import Project
from r3_onshape.stage import run

if "--force" in sys.argv:
    os.environ["R3_CAD_FORCE"] = "1"
project = Project.load(Path(__file__).resolve().parents[1] / "examples" / "robots" / "rc-car")
result = run(project)
result.write(project.build_dir("cad") / "stage_result.json")
print(json.dumps(result.metrics, indent=1, default=str))
print(Counter((d.severity.value, d.code) for d in result.diagnostics))
for d in result.diagnostics:
    if d.severity.value != "info":
        print(d.severity.value, d.code, d.message[:300])
print("OK" if result.ok else "FAILED")
