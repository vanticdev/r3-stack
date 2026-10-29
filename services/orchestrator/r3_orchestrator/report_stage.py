"""``report`` stage: ``build/report/index.html`` and ``build/report/summary.json``.

Reads every persisted stage result plus scenario summaries and images under
``build/``. The HTML is self-contained (inline CSS, PNGs embedded as data
URIs up to a size limit, larger ones linked) so it can be attached to CI runs.
``summary.json`` is the machine-readable twin consumed by CI job summaries
and the web dashboard (``apps/web``); paths in it are relative to ``build/``.
"""

from __future__ import annotations

import csv
import base64
import html
import json
import time
from pathlib import Path
from typing import Any

from r3_model.diagnostics import StageResult
from r3_model.project import Project

from .purchasing import purchase_summary

from . import stages as stage_runner
from .hints import hint_for
from .release import is_stale

STAGE = "report"
SUMMARY_SCHEMA = "r3/report-summary@1"
EMBED_LIMIT = 1_500_000  # bytes per PNG embedded into index.html


def _scenario_results(build: Path) -> list[dict[str, Any]]:
    scenarios = []
    for path in sorted(build.glob("sim/*/summary.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        passed = data.get("passed", data.get("ok"))
        scalars = {k: v for k, v in data.items() if isinstance(v, (int, float, str, bool)) and k not in ("passed", "ok")}
        checks = data.get("checks") or data.get("expect_results") or data.get("expect") or {}
        scenarios.append({"id": data.get("scenario", path.parent.name), "passed": passed, "path": str(path.relative_to(build)),
                          "metrics": scalars, "checks": checks if isinstance(checks, (dict, list)) else {}})
    return scenarios


def _images(build: Path) -> list[str]:
    return sorted(str(p.relative_to(build)) for p in build.rglob("*.png") if not p.relative_to(build).parts[0] == STAGE)


def build_summary(project: Project, results: dict[str, StageResult]) -> dict[str, Any]:
    build = project.root / "build"
    fingerprint = project.fingerprint()
    diagnostics = [{**d.model_dump(mode="json"), "hint": hint_for(d.code, d.hint)}
                   for name, r in results.items() if name != STAGE for d in r.diagnostics]
    stage_rows = []
    for name in stage_runner.stage_names():
        r = results.get(name)
        stage_rows.append({"stage": name, "status": stage_runner.status_of(r), "ok": None if r is None else r.ok,
                           "stale": None if r is None else is_stale(r.inputs, fingerprint),
                           "counts": stage_runner.counts(r.diagnostics) if r else {}, "metrics": r.metrics if r else {},
                           "duration_s": r.duration_s if r else None,
                           "artifacts": [a.model_dump(mode="json") for a in r.artifacts] if r else []})
    counts = {"error": 0, "warning": 0, "info": 0}
    for d in diagnostics:
        counts[d["severity"]] += 1
    scenarios = _scenario_results(build)
    artifacts = {"graph": "model/graph.json" if (build / "model/graph.json").exists() else None,
                 "routes": "wiring/routes.json" if (build / "wiring/routes.json").exists() else None,
                 "cad_model": "cad/cad_model.json" if (build / "cad/cad_model.json").exists() else None,
                 "operating_points": "electrical/operating_points.json" if (build / "electrical/operating_points.json").exists() else None,
                 "images": _images(build)}
    return {
        "schema": SUMMARY_SCHEMA,
        "purchases": purchase_summary(project),
        "generated_at": time.time(),
        "robot": project.spec.robot.model_dump(mode="json"),
        "inputs": fingerprint,
        "ok": counts["error"] == 0 and all(s["status"] in ("ok", "unavailable") for s in stage_rows if s["status"] != "missing")
              and all(s.get("passed") is not False for s in scenarios),
        "counts": counts,
        "stages": stage_rows,
        "diagnostics": diagnostics,
        "scenarios": scenarios,
        "artifacts": artifacts,
        "assumptions": [{"stage": name, **a.model_dump(mode="json")} for name, r in results.items() for a in r.assumptions],
    }


_CSS = """
body{font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;color:#1c2024;background:#f6f7f9}
header{background:#14181d;color:#fff;padding:18px 28px}header h1{margin:0;font-size:20px}header p{margin:4px 0 0;color:#aab3bd}
main{padding:20px 28px;max-width:1400px}section{background:#fff;border:1px solid #e1e4e8;border-radius:8px;padding:16px 20px;margin-bottom:18px}
h2{font-size:16px;margin:0 0 12px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eceef1;vertical-align:top}
th{font-weight:600;color:#57606a;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.error,.failed{background:#ffe3e3;color:#a4161a}.warning,.unavailable,.stale{background:#fff3cd;color:#7a5600}.info,.missing{background:#e7f0ff;color:#1f4e9a}
.ok,.passed{background:#dcf5e3;color:#1a6b34}code{font:12px ui-monospace,Menlo,monospace;background:#f0f2f4;padding:1px 4px;border-radius:3px}
.hint{color:#57606a;font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}
figure{margin:0}figure img{width:100%;border:1px solid #e1e4e8;border-radius:6px;background:#fff}figcaption{font-size:12px;color:#57606a}
.kpi{display:flex;gap:28px}.kpi div{font-size:24px;font-weight:600}.kpi span{display:block;font-size:12px;color:#57606a;font-weight:400}
"""


def _pill(text: str, cls: str | None = None) -> str:
    return f'<span class="pill {html.escape(cls or text)}">{html.escape(text)}</span>'


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (dict, list)):
        return f"<code>{html.escape(json.dumps(value, default=str)[:300])}</code>"
    return html.escape(str(value))


def render_html(summary: dict[str, Any], build: Path) -> str:
    esc = html.escape
    robot = summary["robot"]
    counts = summary["counts"]
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>R3 report: {esc(robot['name'])}</title>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{_CSS}</style></head><body>",
             f"<header><h1>R3 validation report: {esc(robot['name'])} v{esc(robot.get('version', ''))}</h1>"
             f"<p>{esc(robot.get('description', '').strip())}</p>"
             f"<p>robot.yaml sha256 <code>{esc(summary['inputs'].get('robot_yaml_sha256', ''))}</code> "
             f"commit <code>{esc(summary['inputs'].get('git_commit', 'n/a')[:12])}</code> · generated "
             f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(summary['generated_at']))}</p></header><main>"]
    purchases = summary.get("purchases", {})
    parts.append("<section><h2>Parts to buy</h2><p>" + esc(str(purchases.get("to_buy_totals", {})))
                 + " (" + str(purchases.get("unpriced_to_buy", 0)) + " unpriced)</p><table>")
    for row in purchases.get("lines", []):
        parts.append(f"<tr><td>{esc(row['component'])}</td><td>{esc(row['status'])}</td><td>{esc(str(row['purchase_cost']))}</td></tr>")
    parts.append("</table></section>")
    verdict = "passed" if summary["ok"] else "failed"
    parts.append(f"<section><h2>Overall {_pill(verdict)}</h2><div class='kpi'>"
                 f"<div>{counts['error']}<span>errors</span></div><div>{counts['warning']}<span>warnings</span></div>"
                 f"<div>{counts['info']}<span>info</span></div><div>{len(summary['scenarios'])}<span>scenarios</span></div>"
                 f"<div>{len(summary['artifacts']['images'])}<span>images</span></div></div></section>")

    rows = []
    for s in summary["stages"]:
        c = s["counts"] or {}
        stale = " " + _pill("stale") if s.get("stale") else ""
        metrics = ", ".join(f"{esc(k)}={_fmt(v)}" for k, v in list((s["metrics"] or {}).items())[:8])
        duration = "" if s["duration_s"] is None else f"{s['duration_s']:.2f} s"
        rows.append(f"<tr><td><b>{esc(s['stage'])}</b></td><td>{_pill(s['status'])}{stale}</td><td>{c.get('error', '')}</td>"
                    f"<td>{c.get('warning', '')}</td><td>{duration}</td>"
                    f"<td class='hint'>{metrics}</td></tr>")
    parts.append("<section><h2>Stages</h2><table><tr><th>Stage</th><th>Status</th><th>Errors</th><th>Warnings</th><th>Time</th>"
                 f"<th>Metrics</th></tr>{''.join(rows)}</table></section>")

    order = {"error": 0, "warning": 1, "info": 2}
    stage_index = {name: i for i, name in enumerate(stage_runner.ALL_STAGES)}
    diags = sorted(summary["diagnostics"], key=lambda d: (order[d["severity"]], stage_index.get(d["stage"], 99), d["code"]))
    rows = [f"<tr><td>{_pill(d['severity'])}</td><td>{esc(d['stage'])}</td><td><code>{esc(d['code'])}</code></td>"
            f"<td>{esc(', '.join(d['subjects']))}</td><td>{esc(d['message'])}"
            f"{'<div class=hint>' + esc(d['hint'][:400]) + '</div>' if d.get('hint') else ''}</td>"
            f"<td class='hint'>{_fmt(d['data']) if d['data'] else ''}</td></tr>" for d in diags]
    parts.append(f"<section><h2>Diagnostics ({len(diags)})</h2><table><tr><th>Severity</th><th>Stage</th><th>Code</th><th>Subjects</th>"
                 f"<th>Message</th><th>Data</th></tr>{''.join(rows) or '<tr><td colspan=6>No diagnostics.</td></tr>'}</table></section>")

    if summary["scenarios"]:
        rows = []
        for sc in summary["scenarios"]:
            state = "passed" if sc["passed"] else ("failed" if sc["passed"] is False else "missing")
            metrics = ", ".join(f"{esc(k)}={_fmt(v)}" for k, v in list(sc["metrics"].items())[:10])
            rows.append(f"<tr><td><b>{esc(str(sc['id']))}</b></td><td>{_pill(state)}</td><td class='hint'>{metrics}</td>"
                        f"<td class='hint'>{_fmt(sc['checks']) if sc['checks'] else ''}</td></tr>")
        parts.append("<section><h2>Scenarios</h2><table><tr><th>Scenario</th><th>Result</th><th>Metrics</th><th>Checks</th></tr>"
                     f"{''.join(rows)}</table></section>")

    figures = []
    for rel in summary["artifacts"]["images"]:
        path = build / rel
        if path.stat().st_size <= EMBED_LIMIT:
            src = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
        else:
            src = "../" + rel
        figures.append(f"<figure><a href='../{esc(rel)}'><img src='{src}' alt='{esc(rel)}' loading='lazy'></a>"
                       f"<figcaption>{esc(rel)}</figcaption></figure>")
    if figures:
        parts.append(f"<section><h2>Images</h2><div class='grid'>{''.join(figures)}</div></section>")

    if summary["assumptions"]:
        rows = [f"<tr><td>{esc(a['stage'])}</td><td>{esc(a['domain'])}</td><td>{esc(a['statement'])}</td></tr>" for a in summary["assumptions"]]
        parts.append("<section><h2>Assumptions (what a pass does not prove)</h2><table><tr><th>Stage</th><th>Domain</th>"
                     f"<th>Statement</th></tr>{''.join(rows)}</table></section>")
    parts.append("</main></body></html>")
    return "".join(parts)


def run(project: Project) -> StageResult:
    start = time.time()
    result = StageResult(stage=STAGE, inputs=project.fingerprint())
    results = stage_runner.load_results(project)
    results.pop(STAGE, None)
    summary = build_summary(project, results)
    out = project.build_dir(STAGE)
    with (out / "bom.csv").open("w", newline="") as handle:
        rows = summary["purchases"]["lines"]
        writer = csv.DictWriter(handle, fieldnames=["component", "type", "owned", "note", "status", "unit_price", "currency", "purchase_cost"])
        writer.writeheader()
        writer.writerows(rows)
    result.artifact("report/bom.csv", "csv", "Component purchase BOM (owned parts included)")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out / "index.html").write_text(render_html(summary, project.root / "build"))
    result.artifact("report/summary.json", "json", "Machine-readable validation summary")
    result.artifact("report/index.html", "html", "Self-contained validation report")
    stale = [s["stage"] for s in summary["stages"] if s.get("stale")]
    if stale:
        result.assume("report", f"Results for {', '.join(stale)} were computed from older project/catalog/model-bank inputs.")
    result.metrics = {"errors": summary["counts"]["error"], "warnings": summary["counts"]["warning"],
                      "scenarios": len(summary["scenarios"]), "images": len(summary["artifacts"]["images"]), "ok": summary["ok"]}
    result.duration_s = time.time() - start
    return result
