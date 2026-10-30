"""Run isolated headless workers and retain resumable per-scenario timings.

Use the interpreter/PYTHONPATH of the checkout being measured. The same driver
can measure a pre-joint-physics checkout with --authored-only.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import time

from r3_model.diagnostics import Diagnostic, StageResult
from r3_model.project import Project
from r3_model.spec import Scenario


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--authored-only", action="store_true")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--full-duration", action="store_true", help="Continue after joint failures (new-layer checkouts only)")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    project = Project.load(args.project)
    scenarios = project.spec.simulation.scenarios
    if not args.authored_only:
        from r3_sim.load_cases import all_scenarios

        scenarios = all_scenarios(project)
    if args.scenario:
        missing = set(args.scenario) - {case.id for case in scenarios}
        if missing:
            parser.error("Unknown scenarios: " + ", ".join(sorted(missing)))
        scenarios = [case for case in scenarios if case.id in args.scenario]
    fingerprint = project.fingerprint()
    folder = project.build_dir("sim-benchmark")
    manifest = folder / "timings.json"
    timings: dict[str, dict] = {}
    started = time.monotonic()

    def run(case: Scenario) -> tuple[str, dict]:
        summary_path = project.build_dir("sim/" + case.id) / "summary.json"
        summary = None
        reused = False
        if args.resume and summary_path.exists():
            candidate = json.loads(summary_path.read_text())
            inputs = candidate.get("inputs", {})
            fixture_current = True
            if case.initial.get("joint_load"):
                from r3_sim.load_cases import FIXTURE_VERSION
                fixture_current = candidate.get("load_case", {}).get("fixture_version") == FIXTURE_VERSION
            if (fixture_current and all(inputs.get(key) == value for key, value in fingerprint.items() if key != "git_commit")
                    and (not args.full_duration or candidate["metrics"]["sim_time_s"] >= float(case.duration) - 0.0005)):
                summary, reused = candidate, True
        code = int(not summary["passed"]) if reused else 0
        worker_started = time.monotonic()
        if summary is None:
            summary_path.unlink(missing_ok=True)
            command = [sys.executable, "-m", "r3_sim_worker", str(project.root), "--scenario", case.id, "--no-render"]
            if args.full_duration and hasattr(project.spec.simulation, "joint_failure_replay_duration"):
                command.append("--full-duration")
            with (folder / (case.id + ".log")).open("w") as log:
                code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
            if summary_path.exists():
                summary = json.loads(summary_path.read_text())
        if summary is None:
            return case.id, {"worker_exit": code, "summary_missing": True, "requested_s": float(case.duration)}
        metrics = summary["metrics"]
        return case.id, {
            "worker_exit": code, "reused": reused, "passed": summary["passed"],
            "stopped_after_joint_failure": any(event.get("event") == "joint_failure_stop" for event in summary.get("events", [])),
            "requested_s": float(case.duration), "simulated_s": metrics["sim_time_s"],
            "wall_s": metrics["wall_time_s"], "setup_s": metrics["setup_time_s"],
            "worker_wall_s": time.monotonic() - worker_started if not reused else None,
            "complete": (metrics["sim_time_s"] >= float(case.duration) - 0.0005
                         and not any(item["code"] == "joint_simulation_unstable"
                                     for item in summary["diagnostics"])),
            "joint_failures": sum(event.get("event") == "joint_failed" for event in summary.get("events", [])),
        }

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run, case) for case in scenarios]
        for future in as_completed(futures):
            name, row = future.result()
            timings[name] = row
            payload = {"inputs": fingerprint, "jobs": args.jobs, "cases": timings,
                       "batch_wall_s": time.monotonic() - started}
            temporary = manifest.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n")
            temporary.replace(manifest)
            print(name, json.dumps(row), flush=True)

    aggregate = StageResult(stage="sim", inputs=fingerprint)
    for case in scenarios:
        path = project.root / "build/sim" / case.id / "summary.json"
        if timings[case.id].get("summary_missing"):
            aggregate.add(Diagnostic.error("scenario_missing", "Worker did not produce a scenario summary", subjects=[case.id]))
            continue
        summary = json.loads(path.read_text())
        aggregate.extend([Diagnostic.model_validate(item) for item in summary["diagnostics"]])
        aggregate.metrics[case.id] = {"passed": summary["passed"], **summary["metrics"]}
        aggregate.artifact(str(path.relative_to(project.root / "build")), "json", case.id + " summary")
    aggregate.metrics["scenarios_total"] = len(scenarios)
    aggregate.metrics["scenarios_passed"] = sum(row.get("passed", False) for row in timings.values())
    aggregate.ok = aggregate.ok and all(row.get("passed", False) for row in timings.values())
    aggregate.duration_s = time.monotonic() - started
    aggregate.write(project.build_dir("sim") / "result.json")
    return int(not aggregate.ok)


if __name__ == "__main__":
    raise SystemExit(main())
