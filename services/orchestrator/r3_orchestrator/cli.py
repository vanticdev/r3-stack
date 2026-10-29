"""``r3`` command line: the same operations as the MCP server, for humans and CI.

Human-readable output by default; ``--json`` prints the structured payload for
agents. Exit codes: 0 success, 1 diagnostics with errors (or a failed
operation), 2 usage errors and refused actions (missing confirmation).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Sequence

from . import api
from .stages import STAGES

EXIT_OK, EXIT_ERRORS, EXIT_USAGE = 0, 1, 2
_SEVERITY_MARK = {"error": "E", "warning": "W", "info": "i"}


# ----------------------------------------------------------------- printing
def _print_diagnostics(diagnostics: list[dict[str, Any]], show_info: bool = False) -> None:
    for d in diagnostics:
        if d["severity"] == "info" and not show_info:
            continue
        subjects = f" [{', '.join(d['subjects'])}]" if d["subjects"] else ""
        print(f"  {_SEVERITY_MARK[d['severity']]} {d['stage']:<10} {d['code']}{subjects}")
        print(f"      {d['message']}")


def _print_results(payload: dict[str, Any]) -> None:
    print(f"{payload['robot']}  ({payload['project']})")
    for stage in payload["stages"]:
        c = stage.get("counts", {})
        print(f"  {stage['stage']:<10} {stage['status']:<12} {c.get('error', 0)} errors, {c.get('warning', 0)} warnings"
              f"  {stage.get('duration_s', 0):.2f} s")
    if payload["diagnostics"]:
        print("diagnostics:")
        _print_diagnostics(payload["diagnostics"])
    c = payload["counts"]
    print(f"{'PASS' if payload['ok'] else 'FAIL'}: {c['error']} errors, {c['warning']} warnings, {c['info']} info")


def _print_status(payload: dict[str, Any]) -> None:
    robot, summary = payload["robot"], payload["summary"]
    print(f"{robot['name']} v{robot['version']}  ({payload['project']})")
    git = payload.get("git") or {}
    if git:
        print(f"  git {git['branch']} @ {git['commit'][:12]}  ({git['dirty_files']} uncommitted files)")
    print(f"  robot.yaml sha256 {payload['inputs']['robot_yaml_sha256']}")
    print(f"  {summary['components']} components, {summary['harnesses']} harnesses / {summary['wires']} wires, "
          f"{summary['boards']} boards, {summary['bindings']} bindings, scenarios: {', '.join(summary['scenarios'])}")
    for key, value in (payload.get("lock") or {}).items():
        print(f"  lock {key}: {value.get('revision', value) if isinstance(value, dict) else value}")
    print("stages:")
    for stage in payload["stages"]:
        if stage["status"] == "missing":
            print(f"  {stage['stage']:<10} not run")
            continue
        c = stage["counts"]
        print(f"  {stage['stage']:<10} {stage['status']:<12} {c['error']} errors, {c['warning']} warnings"
              f"{'  (stale: robot.yaml changed)' if stage['stale'] else ''}")


def _print_diagnose(payload: dict[str, Any]) -> None:
    if not payload["subjects"]:
        print("No errors or warnings. Run `r3 validate` after changing robot.yaml.")
        return
    for group in payload["subjects"]:
        entries = group["diagnostics"]
        errors = sum(1 for e in entries if e["severity"] == "error")
        print(f"{group['subject']}: {errors} errors, {len(entries) - errors} warnings")
        for e in entries:
            also = f" (with {', '.join(e['also'])})" if e["also"] else ""
            print(f"  {_SEVERITY_MARK[e['severity']]} [{e['stage']}] {e['code']}{also}")
            print(f"      {e['message']}")
            if e.get("hint"):
                print(f"      hint: {e['hint'].splitlines()[0] if e['code'] != 'stage_crashed' else e['hint'].strip().splitlines()[-1]}")
    print(f"{payload['errors']} errors across {len(payload['subjects'])} subjects")


def _print_generic(payload: dict[str, Any]) -> None:
    if "stages" in payload and "diagnostics" in payload:
        _print_results(payload)
        return
    for key, value in payload.items():
        print(f"{key}: {value if not isinstance(value, (dict, list)) else json.dumps(value, indent=2, default=str)}")


# ----------------------------------------------------------------- commands
def _scenarios(args: argparse.Namespace) -> list[str] | None:
    return args.scenario or None


def cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    stages = [s for item in (args.stage or []) for s in item.split(",") if s] or None
    return api.validate(args.project, stages, scenarios=_scenarios(args))


def _single(stage: str) -> Callable[[argparse.Namespace], dict[str, Any]]:
    def command(args: argparse.Namespace) -> dict[str, Any]:
        return api.run_stage(args.project, stage, scenarios=getattr(args, "scenario", None) or None)
    return command


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    return api.status(args.project)


def cmd_diagnose(args: argparse.Namespace) -> dict[str, Any]:
    return api.diagnose(args.project, include_warnings=not args.errors_only)


def cmd_deploy(args: argparse.Namespace) -> dict[str, Any]:
    if args.target == "sim":
        return api.deploy_sim(args.project, _scenarios(args))
    return api.deploy_fleet(args.project, confirm=args.yes, deploy_args=args.deploy_args, force=args.force)


def cmd_create(args: argparse.Namespace) -> dict[str, Any]:
    return api.create_project(args.name, parent=args.dir, template=args.template, github=args.github, private=args.private,
                              confirm=args.github, platform_url=args.platform_url)


PRINTERS: dict[str, Callable[[dict[str, Any]], None]] = {"status": _print_status, "diagnose": _print_diagnose}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", "-C", default=None, help="Project directory (default: $R3_PROJECT or nearest robot.yaml).")
    common.add_argument("--json", action="store_true", help="Print structured JSON for agents.")
    parser = argparse.ArgumentParser(prog="r3", parents=[common], description="R3 robot pipeline: validate, simulate, report, deploy.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("validate", parents=[common], help="Run pipeline stages in order and write the report.")
    p.add_argument("--stage", action="append", metavar="NAME",
                   help=f"Stage(s) to run, repeatable or comma separated: {', '.join(s.name for s in STAGES)}.")
    p.add_argument("--scenario", action="append", help="Restrict the sim stage to these scenario ids.")
    p.set_defaults(func=cmd_validate)

    for group, action, stage, text in (("cad", "sync", "cad", "Sync CAD from Onshape and extract mass properties."),
                                       ("pcb", "build", "pcb", "Generate KiCad boards and run ERC/DRC."),
                                       ("wiring", "route", "wiring", "Route harnesses in 3D and check clearances.")):
        g = sub.add_parser(group, parents=[common], help=text)
        gsub = g.add_subparsers(dest="action", required=True)
        gsub.add_parser(action, parents=[common], help=text).set_defaults(func=_single(stage))

    sim = sub.add_parser("sim", parents=[common], help="Digital twin scenarios.")
    simsub = sim.add_subparsers(dest="action", required=True)
    run = simsub.add_parser("run", parents=[common], help="Run scenarios (default: all).")
    run.add_argument("--scenario", action="append", help="Scenario id (repeatable).")
    run.set_defaults(func=_single("sim"))

    sub.add_parser("report", parents=[common], help="Regenerate build/report from stored results.").set_defaults(func=_single("report"))
    sub.add_parser("status", parents=[common], help="Project, revision and stage status.").set_defaults(func=cmd_status)
    p = sub.add_parser("diagnose", parents=[common], help="Errors grouped by subject with hints.")
    p.add_argument("--errors-only", action="store_true")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("deploy", parents=[common], help="Deploy software to the twin (sim) or physical robots (fleet).")
    p.add_argument("--target", choices=["sim", "fleet"], required=True)
    p.add_argument("--scenario", action="append", help="sim: scenario id(s) to run.")
    p.add_argument("--yes", action="store_true", help="fleet: confirm deployment to physical hardware.")
    p.add_argument("--force", action="store_true", help="fleet: deploy even without a clean validation of the current spec.")
    p.add_argument("deploy_args", nargs=argparse.REMAINDER, help="fleet: extra arguments for backend/deploy.py after `--`.")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("create", parents=[common], help="Scaffold a new R3 robot project repository.")
    p.add_argument("name")
    p.add_argument("--from", dest="template", default="rc-car", help="Template robot (default: rc-car).")
    p.add_argument("--dir", default=".", help="Parent directory (default: cwd).")
    p.add_argument("--github", action="store_true", help="Also create the GitHub repository with `gh repo create` and push.")
    visibility = p.add_mutually_exclusive_group()
    visibility.add_argument("--private", dest="private", action="store_true", default=True)
    visibility.add_argument("--public", dest="private", action="store_false")
    p.add_argument("--platform-url", default=None, help="pip URL of r3-platform written into the CI workflow.")
    p.set_defaults(func=cmd_create)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "deploy_args", None) and args.deploy_args[:1] == ["--"]:
        args.deploy_args = args.deploy_args[1:]
    try:
        payload = args.func(args)
    except api.ProjectNotFound as error:
        payload = {"ok": False, "code": "project_not_found", "message": str(error)}
    except KeyError as error:
        payload = {"ok": False, "code": "usage_error", "message": str(error).strip("'\"")}
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif payload.get("code") and not payload.get("ok"):
        print(f"r3: {payload['code']}: {payload.get('message', '')}", file=sys.stderr)
        if payload.get("errors") and isinstance(payload["errors"], list):
            for error in payload["errors"]:
                print(f"  {error}", file=sys.stderr)
    else:
        PRINTERS.get(args.command, _print_generic)(payload)
    if payload.get("ok"):
        return EXIT_OK
    if payload.get("code") in ("confirmation_required", "usage_error", "project_not_found", "validation_required"):
        return EXIT_USAGE
    return EXIT_ERRORS


if __name__ == "__main__":
    raise SystemExit(main())
