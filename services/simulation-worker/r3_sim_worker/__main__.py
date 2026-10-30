"""CLI: ``python -m r3_sim_worker <project> [--scenario id] [--no-render] [--software-cmd CMD ...]``.

``--software-cmd`` consumes the rest of the command line, e.g.
``--software-cmd python tests/sim_test_controller.py``. Without it the
project's own ``software.modules[0]`` entrypoint runs. Exit status is 0 when
every selected scenario passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys

from r3_model.project import Project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="r3_sim_worker", description="Run R3 digital-twin scenarios.")
    parser.add_argument("project", help="project directory containing robot.yaml")
    parser.add_argument("--scenario", action="append", help="scenario id (repeatable); default: all")
    parser.add_argument("--no-render", action="store_true", help="skip MuJoCo offscreen renders")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--full-duration", action="store_true", help="Continue failed assemblies for the entire requested scenario duration")
    parser.add_argument("--software-cmd", nargs=argparse.REMAINDER, help="robot software command (rest of the line)")
    args = parser.parse_args(argv)
    from r3_sim.headless import configure

    configure()
    project = Project.load(args.project)
    if args.full_duration:
        project.spec.simulation.joint_failure_replay_duration = None
    from r3_sim.stage import run

    progress = None if args.quiet else (lambda message: print(message, file=sys.stderr, flush=True))
    result = run(project, scenarios=args.scenario, software_cmd=args.software_cmd or None, render=not args.no_render,
                 progress=progress)
    for scenario, metrics in result.metrics.items():
        if isinstance(metrics, dict):
            print(f"{'PASS' if metrics.get('passed') else 'FAIL'}  {scenario}")
    for diagnostic in result.diagnostics:
        print(f"  [{diagnostic.severity.value}] {diagnostic.code}: {diagnostic.message}")
    print(f"result: {project.root / 'build' / 'sim' / 'result.json'}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
