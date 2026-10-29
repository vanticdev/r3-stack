import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client.ts";
import { useDoctor, useProject, useSpec } from "../api/hooks.ts";
import type { EvidenceStatus, ReleaseItem } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { RunLog } from "../components/Layout.tsx";
import { Card, Chip, EvidenceChip, QueryView, Segmented, StageChip } from "../components/ui.tsx";
import { EVIDENCE_ORDER } from "../lib/domain.ts";
import { formatTarget, formatValue, itemUnit, KIND_LABEL } from "../lib/release.ts";
import { humanize } from "../lib/units.ts";
import { useProjectRuns } from "../state/runs.tsx";

type KindFilter = "all" | ReleaseItem["kind"];

function EvidenceCell({ pid, item }: { pid: string; item: ReleaseItem }) {
  if (!item.evidence.length) return <span className="muted">{item.message || "no evidence yet"}</span>;
  return (
    <div className="stack" style={{ gap: 4 }}>
      {item.evidence.map((e, i) => (
        <div key={i} className="stack" style={{ gap: 0 }}>
          <span className="row" style={{ gap: 6 }}>
            <strong>{e.stage}</strong><code>{e.check}</code>
            {e.estimated && <Chip tone="warning">estimated</Chip>}
            {e.stale && <Chip tone="stale">stale inputs</Chip>}
          </span>
          {e.artifact && <a href={api.artifactUrl(pid, e.artifact)} target="_blank" rel="noreferrer" className="mono">{e.artifact}</a>}
          {e.inputs?.robot_yaml_sha256 && <span className="muted">inputs <code>{e.inputs.robot_yaml_sha256}</code></span>}
          {e.detail && <span className="muted">{e.detail}</span>}
        </div>
      ))}
      {item.message && !item.evidence.some((e) => e.detail === item.message) && <span className="muted">{item.message}</span>}
    </div>
  );
}

export function OverviewPage() {
  const { pid = "" } = useParams();
  const project = useProject(pid);
  const spec = useSpec(pid);
  const doctor = useDoctor();
  const { runs, active, start } = useProjectRuns(pid);
  const [filter, setFilter] = useState<"all" | EvidenceStatus>("all");
  const [kind, setKind] = useState<KindFilter>("all");

  return (
    <QueryView query={project} height={400}>
      {(p) => {
        const items = p.release.items.slice().sort((a, b) => EVIDENCE_ORDER.indexOf(a.status) - EVIDENCE_ORDER.indexOf(b.status));
        const shown = items.filter((i) => (filter === "all" || i.status === filter) && (kind === "all" || i.kind === kind));
        const warnings = (doctor.data?.stages ?? []).filter((s) => s.level !== "full");
        const verified = p.release.counts.verified ?? 0;
        return (
          <div className="page">
            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
              <div className="card kpi" data-testid="release-gate">
                <span className="label">Release gate</span>
                <span className="value">{p.release.fabrication_ready ? "Fabrication ready" : "Not ready"}</span>
                <span className="sub row">
                  {p.release.fabrication_ready ? <Chip tone="good">all verified</Chip> : <Chip tone="critical">{p.release.blocking.length} blocking</Chip>}
                  <span>{verified}/{p.release.items.length} verified</span>
                </span>
              </div>
              {(["failed", "stale", "estimated", "not_run"] as EvidenceStatus[]).map((s) => (
                s === "stale" && !p.release.counts.stale ? null : (
                  <button key={s} className={`card kpi${filter === s ? " selected" : ""}`} style={{ textAlign: "left", cursor: "pointer" }}
                          onClick={() => setFilter(filter === s ? "all" : s)} aria-pressed={filter === s}>
                    <span className="label">{humanize(s)}</span>
                    <span className="value">{p.release.counts[s] ?? 0}</span>
                    <span className="sub"><EvidenceChip status={s} /></span>
                  </button>
                )
              ))}
            </div>

            <div className="grid side">
              <Card title="Requirement evidence"
                    subtitle={<>Every requirement, scenario expectation and stage, with the check that proves it · inputs <code>{p.release.inputs.robot_yaml_sha256}</code></>}
                    actions={
                      <div className="row">
                        <Segmented label="Kind" value={kind} onChange={setKind}
                                   options={[{ value: "all", label: "All" }, { value: "requirement", label: "Requirements" },
                                             { value: "scenario_expectation", label: "Scenarios" }, { value: "stage", label: "Stages" }]} />
                        <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} aria-label="Status filter">
                          <option value="all">all statuses</option>
                          {EVIDENCE_ORDER.map((s) => <option key={s} value={s}>{humanize(s)}</option>)}
                        </select>
                      </div>
                    }
                    flush>
                <div className="table-wrap" style={{ maxHeight: 620 }}>
                  <table data-testid="evidence-matrix">
                    <thead><tr><th>Status</th><th>Item</th><th className="num">Target</th><th className="num">Measured</th><th>Evidence</th></tr></thead>
                    <tbody>
                      {shown.map((item) => (
                        <tr key={item.id}>
                          <td><EvidenceChip status={item.status} /></td>
                          <td>
                            <div>{humanize(item.subject)}</div>
                            <div className="muted small"><span className="badge">{KIND_LABEL[item.kind]}</span> <span className="mono">{item.id}</span></div>
                          </td>
                          <td className="num">{formatTarget(item)}</td>
                          <td className="num">{item.evidence.length ? item.evidence.map((e, i) => <div key={i}>{formatValue(e.value, itemUnit(item))}</div>) : "—"}</td>
                          <td className="small"><EvidenceCell pid={pid} item={item} /></td>
                        </tr>
                      ))}
                      {!shown.length && <tr><td colSpan={5} className="muted">No items match.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </Card>

              <div className="stack" style={{ gap: 16, alignContent: "start" }}>
                <Card title="Pipeline" subtitle="Stage results under build/"
                      actions={<button className="primary small" disabled={!!active} onClick={() => void start({})}><Icon name="play" size={12} />Run</button>}>
                  <div className="stack">
                    {p.stages.map((s) => (
                      <div key={s.stage} className="row" style={{ justifyContent: "space-between" }}>
                        <StageChip stage={s.stage} status={active?.stage === s.stage ? "running" : s.stale && s.status !== "missing" ? "stale" : s.status} />
                        <span className="small muted">
                          {s.counts?.error ? `${s.counts.error} E ` : ""}{s.counts?.warning ? `${s.counts.warning} W ` : ""}
                          {s.duration_s ? `· ${s.duration_s.toFixed(1)} s` : s.status === "missing" ? "not run" : ""}
                        </span>
                        <button className="small ghost" disabled={!!active} onClick={() => void start({ stages: [s.stage] })} aria-label={`Run ${s.stage}`}>
                          <Icon name="refresh" size={12} />
                        </button>
                      </div>
                    ))}
                    {runs[0] && <RunLog run={runs[0]} />}
                  </div>
                </Card>
                <Card title="Links">
                  <div className="stack small">
                    <LinkRow label="Onshape document" href={p.links.onshape} />
                    <LinkRow label="GitHub repository" href={p.links.github} />
                    <LinkRow label="HTML report" href={p.report_url} />
                    <Link to={`/p/${encodeURIComponent(pid)}/diagnostics`}>All diagnostics →</Link>
                  </div>
                </Card>
                <Card title="Environment" subtitle="r3 doctor: stages that cannot run at full fidelity here">
                  {doctor.isPending ? <div className="skeleton" style={{ height: 60 }} /> : warnings.length ? (
                    <div className="stack small">
                      {warnings.map((w) => (
                        <div key={w.stage} className="stack" style={{ gap: 1 }}>
                          <span className="row"><Chip tone={w.level === "unavailable" ? "critical" : "warning"}>{w.stage}</Chip><span className="muted">{w.level}</span></span>
                          {w.missing.length > 0 && <span className="muted">missing: {w.missing.join(", ")}</span>}
                        </div>
                      ))}
                      <Link to={`/p/${encodeURIComponent(pid)}/settings`}>Doctor details →</Link>
                    </div>
                  ) : <Chip tone="good">all stages at full fidelity</Chip>}
                </Card>
                <Card title="robot.yaml">
                  <dl className="kv">
                    <dt>Components</dt><dd>{p.summary.components ?? "—"}</dd>
                    <dt>Harnesses / wires</dt><dd>{p.summary.harnesses ?? "—"} / {p.summary.wires ?? "—"}</dd>
                    <dt>Bindings</dt><dd>{p.summary.bindings ?? "—"}</dd>
                    <dt>Scenarios</dt><dd>{p.summary.scenarios?.length ?? "—"}</dd>
                    {Object.entries(spec.data?.spec.requirements ?? {}).map(([k, v]) => (
                      <div key={k} style={{ display: "contents" }}><dt>{humanize(k)}</dt><dd>{String(v)}</dd></div>
                    ))}
                  </dl>
                </Card>
              </div>
            </div>
          </div>
        );
      }}
    </QueryView>
  );
}

function LinkRow({ label, href }: { label: string; href?: string | null }) {
  return (
    <div className="row" style={{ justifyContent: "space-between" }}>
      <span className="secondary">{label}</span>
      {href ? <a href={href} target="_blank" rel="noreferrer" className="row">open <Icon name="external" size={12} /></a> : <span className="muted">not linked</span>}
    </div>
  );
}
