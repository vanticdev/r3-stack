"""``python -m r3_wiring <project>``: run the wiring stage and print its diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from r3_model.project import Project

from .stage import format_diagnostics, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R3 wiring stage")
    parser.add_argument("project", type=Path)
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args(argv)
    result = run(Project.load(args.project), render=not args.no_render)
    print(format_diagnostics(result))
    metrics = result.metrics
    print(json.dumps({"ok": result.ok, "duration_s": round(result.duration_s, 1),
                      "harness_length_mm": {k: round(v * 1e3, 1) for k, v in metrics["harness_length"].items()},
                      "worst_static_clearance": metrics["worst_static_clearance"],
                      "worst_dynamic_clearance": metrics["worst_dynamic_clearance"],
                      "diagnostic_counts": metrics["diagnostic_counts"]}, indent=2, default=float))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
