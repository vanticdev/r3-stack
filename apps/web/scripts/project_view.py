"""Use the production read-only CAD/model projections in the development mock API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def view(root: Path, project_id: str, section: str) -> dict[str, Any]:
    from r3_orchestrator.api.projects import ProjectRef, cad_view, model_view

    ref = ProjectRef(project_id, root.resolve(), root.resolve().parent)
    return {"cad": cad_view, "model": model_view}[section](ref)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("project_id")
    parser.add_argument("section", choices=("cad", "model"))
    args = parser.parse_args()
    print(json.dumps(view(args.project, args.project_id, args.section), allow_nan=False))
