import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client.ts";
import { useDoctor, useProject } from "../api/hooks.ts";
import type { EvidenceStatus, RequirementEvidence } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, EvidenceChip, QueryView, Segmented, StageChip } from "../components/ui.tsx";
import { RunLog } from "../components/Layout.tsx";
import { EVIDENCE_ORDER } from "../lib/domain.ts";
import { fmt, humanize, quantity } from "../lib/units.ts";
import { useProjectRuns } from "../state/runs.tsx";

function evidenceValue(item: RequirementEvidence): string {
  const v = item.evidence?.value;
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return String(v);
  // Scenario expectation keys carry their unit as a suffix (min_wire_clearance_m, min_battery_voltage_v).
  const suffix = item.id.match(/_(m|v|mps|s)$/)?.[1];
  const unit = item.evidence?.unit || item.unit || (suffix ? ({ m: "m", v: "V", mps: "m/s", s: "s" } as Record<string, string>)[suffix]! : "");
  if (unit === "s") return fmt(v, "s");
  if (unit === "m") return fmt(v, "m");
  if (unit === "" && item.id.includes("fraction")) return `${(v * 100).toFixed(2)}%`;
  return fmt(v, unit);
}

function requirementText(item: RequirementEvidence): string {
  const r = item.requirement;
  if (r === null || r === undefined) return "—";
  if (typeof r === "number" && item.id.includes("fraction")) return `${(r * 100).toFixed(1)}%`;
  return String(r);
}

export function OverviewPage() {
  const { pid = "" } = useParams();
  const project = useProject(pid);
  const doctor = useDoctor();
  const { runs, active, start } = useProjectRuns(pid);
  const [filter, setFilter] = useState<"all" | EvidenceStatus>("all");

  return (
    <QueryView query={project} height={400}>
      {(p) => {
        const items = p.release.items.slice().sort((a, b) => EVIDENCE_ORDER.indexOf(a.status) - EVIDENCE_ORDER.indexOf(b.status));
        const shown = filter === "all" ? items : items.filter((i) => i.status === filter);
        const warnings = (doctor.data?.stages ?? []).filter((s) => s.level !== "full");
        return (
          <div className="page">
            <div className="grid cols-4">
              <div className="card kpi" data-testid="release-gate">
                <span className="label">Release gate</span>
                <span className="value">{p.release.fabrication_ready ? "Ready" : "Not ready"}</span>
                <span className="sub row">
                  {p.release.fabrication_ready ? <Chip tone="good">fabrication ready</Chip> : <Chip tone="critical">blocked</Chip>}
                  <span>{p.release.counts.verified ?? 0}/{p.release.items.length} verified</span>
                </span>
              </div>
              {(["failed", "stale", "estimated", "not_run"] as EvidenceStatus[]).map((s) => (
                s === "stale" && !p.release.counts.stale ? null : (
                  <button key={s} className="card kpi" style={{ textAlign: "left", cursor: "pointer" }} onClick={() => setFilter(filter === s ? "all" : s)}>
                    <span className="label">{humanize(s)}</span>
                    <span className="value">{p.release.counts[s] ?? 0}</span>
                    <span className="sub"><EvidenceChip status={s} /></span>
                  </button>
                )
              ))}
            </div>

            <div className="grid side">
              <Card title="Requirement evidence" subtitle={`Every requirement and scenario expectation, with the check that proves it · inputs ${p.release.current_fingerprint}`}
                    actions={<Segmented label="Filter" value={filter} onChange={setFilter}
                                        options={[{ value: "all", label: "All" }, ...EVIDENCE_ORDER.map((s) => ({ value: s, label: humanize(s) }))]} />}
                    flush>
                <div className="table-wrap" style={{ maxHeight: 560 }}>
                  <table data-testid="evidence-matrix">
                    <thead><tr><th>Status</th><th>Requirement</th><th className="num">Required</th><th className="num">Measured</th><th>Evidence</th></tr></thead>
                    <tbody>
                      {shown.map((item) => (
                        <tr key={item.id}>
                          <td><EvidenceChip status={item.status} /></td>
                          <td>
                            <div>{item.label}</div>
                            <div className="muted small mono">{item.id}</div>
                          </td>
                          <td className="num">{requirementText(item)}</td>
                          <td className="num">{evidenceValue(item)}</td>
                          <td className="small">
                            {item.evidence ? (
                              <div className="stack" style={{ gap: 0 }}>
                                <span><strong>{item.evidence.stage}</strong> · <code>{item.evidence.check}</code></span>
                                {item.evidence.artifact && <a href={api.artifactUrl(pid, item.evidence.artifact)} target="_blank" rel="noreferrer" className="mono">{item.evidence.artifact}</a>}
                                {item.evidence.fingerprint && <span className="muted">inputs <code>{item.evidence.fingerprint}</code></span>}
                              </div>
                            ) : <span className="muted">{item.note ?? (item.scenario ? `run scenario ${item.scenario}` : "no evidence yet")}</span>}
                            {item.evidence && item.note && <div className="muted">{item.note}</div>}
                          </td>
                        </tr>
                      ))}
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
                        <StageChip stage={s.stage} status={active?.stage === s.stage ? "running" : s.status} />
                        <span className="small muted">
                          {s.counts.error ? `${s.counts.error} E ` : ""}{s.counts.warning ? `${s.counts.warning} W ` : ""}
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
                    <LinkRow label="Onshape document" href={p.links?.onshape} />
                    <LinkRow label="GitHub repository" href={p.links?.github} />
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
                <RequirementsCard requirements={p.spec.requirements} />
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

function RequirementsCard({ requirements }: { requirements: Record<string, unknown> }) {
  const rows = useMemo(() => Object.entries(requirements), [requirements]);
  return (
    <Card title="robot.yaml requirements">
      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{humanize(k)}</dt>
            <dd title={Number.isFinite(quantity(v)) ? `${quantity(v)} SI` : undefined}>{String(v)}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
