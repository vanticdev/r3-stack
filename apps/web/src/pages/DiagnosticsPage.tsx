import { lazy, Suspense, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useDiagnostics, useProject } from "../api/hooks.ts";
import type { Diagnostic, Severity } from "../api/types.ts";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { Details, ErrorBox, Tabs } from "../components/ui.tsx";
import { diagnosticFacts, diagnosticHeadline, diagnosticType, plural, stageLabel } from "../lib/labels.ts";
import { sortedDiagnostics } from "../lib/scene.ts";
import { timeAgo } from "../lib/units.ts";
import { useProjectRuns } from "../state/runs.tsx";
import type { Marker } from "../viewer/RobotViewer.tsx";
const DiagnosticScene = lazy(() => import("../viewer/DiagnosticScene.tsx"));

const sentenceCase = (t: string) => (t ? t[0]!.toUpperCase() + t.slice(1) : t);
const PAGE = 6;

const RANK: Record<Severity, number> = { error: 0, warning: 1, info: 2 };
const worst = (items: Diagnostic[]): Severity => items.reduce<Severity>((w, d) => (RANK[d.severity] < RANK[w] ? d.severity : w), "info");

interface TypeGroup { type: string; severity: Severity; items: Diagnostic[] }
interface StageGroup { stage: string; severity: Severity; types: TypeGroup[]; count: number }

/** Stage → failure type → findings, worst first at every level. */
export function groupByStageAndType(diagnostics: readonly Diagnostic[]): StageGroup[] {
  const stages = new Map<string, Map<string, Diagnostic[]>>();
  for (const d of diagnostics) {
    const stage = d.stage || "general";
    const types = stages.get(stage) ?? stages.set(stage, new Map()).get(stage)!;
    const type = diagnosticType(d.code);
    (types.get(type) ?? types.set(type, []).get(type)!).push(d);
  }
  return [...stages.entries()].map(([stage, types]) => {
    const list = [...types.entries()].map(([type, items]) => ({ type, severity: worst(items), items: items.slice().sort((a, b) => RANK[a.severity] - RANK[b.severity]) }))
      .sort((a, b) => RANK[a.severity] - RANK[b.severity] || b.items.length - a.items.length);
    const all = list.flatMap((t) => t.items);
    return { stage, severity: worst(all), types: list, count: all.length };
  }).sort((a, b) => RANK[a.severity] - RANK[b.severity] || b.count - a.count);
}

function Row({ d, selected, onSelect, onLocate }: { d: Diagnostic; selected: boolean; onSelect: () => void; onLocate: () => void }) {
  const facts = diagnosticFacts(d);
  return (
    <div className={`diag-row ${d.severity}${selected ? " selected" : ""}`} data-testid="diagnostic">
      <button className="diag-main" onClick={onSelect} aria-expanded={selected}>
        <span className="sev" aria-label={d.severity}><Icon name={d.severity === "error" ? "x" : d.severity === "warning" ? "alert" : "info"} size={11} /></span>
        {facts.cable && facts.amount ? (
          // Geometric findings read as a table: cable · part (or the limit) · how much · when.
          <>
            <span className="headline"><strong>{sentenceCase(facts.cable)}</strong>{facts.part ? <span className="secondary"> · {facts.part}</span> : facts.limit ? <span className="muted"> · {facts.limit}</span> : null}</span>
            <span className="fact">{facts.amount}</span>
            <span className="fact muted time">{facts.time !== null ? `${facts.time.toFixed(3)} s` : ""}</span>
          </>
        ) : <Markdown inline className="headline">{diagnosticHeadline(d)}</Markdown>}
      </button>
      {d.subjects.length > 0 && <button className="small ghost locate" onClick={onLocate} aria-label="Locate in 3D"><span className="long">Locate in 3D</span><span className="short">3D</span></button>}
      {selected && (
        <div className="diag-detail">
          <Markdown>{diagnosticHeadline(d)}</Markdown>
          {d.hint ? <Markdown>{`**Fix:** ${d.hint}`}</Markdown> : <span className="muted">The stage gives no suggested fix for this finding.</span>}
          <Details rows={[["Code", d.code], ["Subjects", d.subjects.join(", ")], ["Data", JSON.stringify(d.data)]]} />
        </div>
      )}
    </div>
  );
}

export function DiagnosticsPage() {
  const { pid = "" } = useParams();
  const { data: diagnostics, isPending, error } = useDiagnostics(pid);
  const project = useProject(pid);
  const { start, active } = useProjectRuns(pid);
  const [params, setParams] = useSearchParams();
  const stageFilter = params.get("stage") ?? "";
  const [severity, setSeverity] = useState("");
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Diagnostic | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"issues" | "3d">("issues");
  const all = diagnostics?.diagnostics ?? [];
  const filtered = useMemo(() => sortedDiagnostics(all, { severity, text }).filter((d) => !stageFilter || d.stage === stageFilter), [all, severity, text, stageFilter]);
  const groups = useMemo(() => groupByStageAndType(filtered), [filtered]);
  const highlight = useMemo(() => selected?.subjects ?? [], [selected]);
  const markers = useMemo<Marker[]>(() => {
    const p = selected ? diagnosticFacts(selected).position : null;
    return p ? [{ position: p, label: diagnosticFacts(selected!).amount ?? "location" }] : [];
  }, [selected]);
  const stages = project.data?.stages ?? [];
  const toggle = (key: string) => setCollapsed((c) => { const n = new Set(c); if (n.has(key)) n.delete(key); else n.add(key); return n; });
  const counts = { error: filtered.filter((d) => d.severity === "error").length, warning: filtered.filter((d) => d.severity === "warning").length };

  if (error) return <div style={{ padding: 20 }}><ErrorBox error={error} /></div>;
  return (
    <div className={`diagnostics-workspace show-${view}`}>
      <Tabs label="View" value={view} onChange={setView} options={[{ value: "issues", label: `Issues · ${filtered.length}` }, { value: "3d", label: "3D" }]} />
      <div className="inspector diag-pane">
        <section className="inspector-head">
          <div className="summary-line">
            <span className="lead">{plural(counts.error, "error")}, {plural(counts.warning, "warning")}</span>
            {stageFilter && <span className="tag">{stageLabel(stageFilter)} only <button className="small ghost" onClick={() => setParams({}, { replace: true })} aria-label="Show all stages">×</button></span>}
          </div>
          <div className="row">
            <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity filter">
              <option value="">All severities</option><option value="error">Errors</option><option value="warning">Warnings</option><option value="info">Info</option>
            </select>
            <input type="search" placeholder="Search part, cable or message" value={text} onChange={(e) => setText(e.target.value)} style={{ flex: 1 }} aria-label="Search diagnostics" />
          </div>
          <Details summary="Run details" rows={[["robot.yaml", project.data?.fingerprint.robot_yaml_sha256], ["Git commit", project.data?.fingerprint.git_commit]]}>
            <div className="small stack" style={{ gap: 2, marginTop: 4 }}>
              {stages.filter((s) => s.status !== "missing").map((s) => (
                <span key={s.stage}>{stageLabel(s.stage)}: ran {timeAgo(s.started_at)}{s.stale ? " · outdated" : ""}</span>
              ))}
            </div>
          </Details>
        </section>
        {isPending && <section><div className="skeleton" style={{ height: 300 }} /></section>}
        {!isPending && !filtered.length && <section><span className="muted">No diagnostics match.</span></section>}
        {groups.map((g) => {
          const row = stages.find((s) => s.stage === g.stage);
          return (
            <section key={g.stage} className="stage-group">
              <div className="row">
                <h2>{stageLabel(g.stage)}</h2><span className="muted small">{g.count}</span>
              </div>
              {row?.stale && (
                <div className="stale-notice">
                  <Icon name="clock" size={14} /><span><strong>{stageLabel(g.stage)} results are out of date.</strong> They predate the current robot.yaml.</span>
                  <button className="small" disabled={!!active} onClick={() => void start({ stages: [g.stage] })}>Rerun {stageLabel(g.stage)}</button>
                </div>
              )}
              {g.types.map((t) => {
                const key = `${g.stage}|${t.type}`;
                const open = !collapsed.has(key);
                return (
                  <div key={key} className="type-group">
                    <button className="type-head" aria-expanded={open} onClick={() => toggle(key)}>
                      <Icon name="chevron" size={12} className={open ? "" : "rot"} />
                      <span className={`dot-mark ${t.severity === "error" ? "critical" : t.severity === "warning" ? "warning" : ""}`} />
                      <strong>{t.type}</strong><span className="muted">· {t.items.length}</span>
                    </button>
                    {open && (expanded.has(key) ? t.items : t.items.slice(0, PAGE)).map((d, i) => (
                      <Row key={`${d.code}${i}`} d={d} selected={selected === d} onSelect={() => setSelected(selected === d ? null : d)}
                           onLocate={() => { setSelected(d); setView("3d"); }} />
                    ))}
                    {open && t.items.length > PAGE && (
                      <button className="small ghost show-more" onClick={() => setExpanded((x) => { const n = new Set(x); if (n.has(key)) n.delete(key); else n.add(key); return n; })}>
                        {expanded.has(key) ? "Show fewer" : `Show all ${t.items.length}`}
                      </button>
                    )}
                  </div>
                );
              })}
            </section>
          );
        })}
      </div>
      <div className="diag-view">
        {selected && (
          <div className="selection-bar">
            <span className="sev-dot" data-severity={selected.severity} />
            <span className="headline">{diagnosticHeadline(selected)}</span>
            {diagnosticFacts(selected).time !== null && <span className="muted">at {diagnosticFacts(selected).time!.toFixed(3)} s</span>}
            <span className="spacer" style={{ flex: 1 }} />
            <Link className="button small" to={`/p/${encodeURIComponent(pid)}/3d?focus=${encodeURIComponent(selected.subjects.join(","))}`}>Open in 3D model</Link>
            <button className="small ghost" onClick={() => setSelected(null)}>Clear</button>
          </div>
        )}
        {view === "3d" || selected ? (
          <Suspense fallback={<div className="skeleton" style={{ margin: 20, flex: 1 }} />}>
            <DiagnosticScene pid={pid} highlight={highlight} markers={markers}
              status={selected ? undefined : "Select a finding to highlight it"} />
          </Suspense>
        ) : <button className="ghost" onClick={() => setView("3d")}>Load 3D context</button>}
      </div>
    </div>
  );
}
