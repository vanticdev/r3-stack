"""``python -m r3_connectors <project> [--no-render]``: run the connectors stage."""

from __future__ import annotations

import argparse
import sys

from r3_model.project import Project

from .stage import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="r3_connectors", description="Connector/pin geometry, models and renders")
    parser.add_argument("project", nargs="?", default=".")
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args(argv)
    result = run(Project.load(args.project), render=not args.no_render)
    for d in result.diagnostics:
        print(f"[{d.severity.value:7s}] {d.code}: {d.message}")
    print({k: v for k, v in result.metrics.items()})
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
