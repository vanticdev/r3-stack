import { Link, useParams } from "react-router-dom";

import { api } from "../api/client.ts";
import { useDoctor, useProject } from "../api/hooks.ts";
import type { ProjectDetail, ReleaseItem } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { RunLog, StageList } from "../components/Layout.tsx";
import { Card, CheckRow, Details, QueryView, type CheckOutcome } from "../components/ui.tsx";
import { environmentCauses } from "../lib/environment.ts";
import { partLabel, stageLabel, stagePage } from "../lib/labels.ts";
import { groupRelease, isOutdated, itemLabel, itemNote, itemOutcome, itemResult, itemScenario, releaseSummary } from "../lib/release.ts";
import { projectStatus } from "../lib/status.ts";
import { useProjectRuns } from "../state/runs.tsx";

/** Technical identity of an item's evidence: ids, checks, artifacts and input hashes, all copyable. */
function EvidenceDetails({ pid, item }: { pid: string; item: ReleaseItem }) {
  const rows: [string, string | null][] = [["Item", item.id]];
  item.evidence.forEach((e, i) => {
    const n = item.evidence.length > 1 ? ` ${i + 1}` : "";
    rows.push([`Check${n}`, `${e.stage}: ${e.check}`], [`Artifact${n}`, e.artifact], [`Inputs${n}`, e.inputs?.robot_yaml_sha256 ?? null]);
  });
  const artifact = item.evidence.find((e) => e.artifact)?.artifact;
  return (
    <Details rows={rows}>
      {artifact && <a href={api.artifactUrl(pid, artifact)} target="_blank" rel="noreferrer" className="small">Open {artifact.split("/").pop()}</a>}
    </Details>
  );
}

const sameEvidence = (a: ReleaseItem, b: ReleaseItem) =>
  b.evidence.length > 0 && b.evidence.every((e) => a.evidence.some((x) => x.stage === e.stage && x.check === e.check));

function outcomeOf(item: ReleaseItem): CheckOutcome {
  const o = itemOutcome(item);
  return o === "passed" && item.status === "estimated" ? "warning" : o;
}

/** "Wire voltage drop" · "4.19% · limit ≤ 3.00%", with freshness stated separately from the result. */
function ItemRow({ pid, item, label, onRerun, busy, children }: {
  pid: string; item: ReleaseItem; label?: string; onRerun: (stage: string) => void; busy: boolean; children?: React.ReactNode;
}) {
  const { value, target } = itemResult(item);
  const outdated = isOutdated(item);
  const stage = item.evidence[0]?.stage ?? (item.kind === "stage" ? item.subject : null);
  const note = itemNote(item);
  const checked = value !== "not checked yet";
  return (
    <CheckRow outcome={outcomeOf(item)} title={label ?? itemLabel(item)}
              result={<>{outdated && checked ? "Previous result: " : ""}{value}{target && item.kind !== "stage" ? <span className="muted"> · required {target}</span> : null}</>}
              action={stage && (outdated || !checked) ? (
                <button className="small" disabled={busy} onClick={() => onRerun(stage)}>{checked ? "Rerun" : "Run"} {stageLabel(stage)}</button>
              ) : stage ? <Link className="small" to={`/p/${encodeURIComponent(pid)}/${stagePage(stage)}`}>{stageLabel(stage)}</Link> : null}>
      {(outdated || item.status === "estimated" || note) && (
        <span className="row" style={{ gap: 8 }}>
          {outdated && <span className="tag outdated"><Icon name="clock" size={11} />Inputs changed · rerun required</span>}
          {item.status === "estimated" && <span className="tag">Estimated inputs</span>}
          {note && <span>{note}</span>}
        </span>
      )}
      {children}
      {item.evidence.length > 0 && <EvidenceDetails pid={pid} item={item} />}
    </CheckRow>
  );
}

function Readiness({ p }: { p: ProjectDetail }) {
  const summary = releaseSummary(p.release.counts, p.release.fabrication_ready);
  const failing = p.release.items.filter((i) => i.status === "failed" && i.kind !== "stage");
  return (
    <div className="readiness" data-testid="release-gate">
      <div className="summary-line">
        <span className="lead">{summary.headline}</span>
        <span className="secondary">{summary.parts.join(" · ")}</span>
      </div>
      {failing.length > 0 && (
        <div className="failing">
          {failing.map((item) => {
            const { value, target } = itemResult(item);
            const stage = item.evidence[0]?.stage;
            return (
              <div key={item.id} className="failing-row">
                <Icon name="x" size={13} />
                <span><strong>{itemLabel(item)}: {value}</strong>, required {target}{itemNote(item) ? <span className="muted"> · {itemNote(item)}</span> : null}</span>
                {stage && <Link to={`/p/${encodeURIComponent(p.id)}/${stagePage(stage)}`}>Open {stageLabel(stage)}</Link>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function OverviewPage() {
  const { pid = "" } = useParams();
  const project = useProject(pid);
  const doctor = useDoctor();
  const { runs, active, start } = useProjectRuns(pid);
  const rerun = (stage: string) => void start({ stages: [stage] });

  return (
    <QueryView query={project} height={400}>
      {(p) => {
        const groups = groupRelease(p.release.items);
        const status = projectStatus(p, active?.stage ?? null);
        const causes = doctor.data ? environmentCauses(doctor.data) : [];
        const busy = !!active;
        return (
          <div className="page">
            <Readiness p={p} />

            <div className="grid side">
              <div className="stack" style={{ gap: 16, alignContent: "start" }}>
                <Card title="Requirements" flush>
                  <div className="check-list padded" data-testid="evidence-matrix">
                    {groups.requirements.map(({ item, related }) => (
                      <ItemRow key={item.id} pid={pid} item={item} onRerun={rerun} busy={busy}>
                        {related.length > 0 && (
                          <div className="related">
                            {related.map((r) => sameEvidence(item, r)
                              ? <span key={r.id} className="small muted">Measured in scenario {partLabel(itemScenario(r))}</span>
                              : <ItemRow key={r.id} pid={pid} item={r} label={`In scenario ${partLabel(itemScenario(r))}`} onRerun={rerun} busy={busy} />)}
                          </div>
                        )}
                      </ItemRow>
                    ))}
                  </div>
                </Card>
                {groups.scenarios.length > 0 && (
                  <Card title="Scenario expectations" flush>
                    <div className="check-list padded">
                      {groups.scenarios.map(({ scenario, items }) => (
                        <div key={scenario} className="scenario-group">
                          <div className="group-head">
                            <Link to={`/p/${encodeURIComponent(pid)}/sim?scenario=${encodeURIComponent(scenario)}`}>{partLabel(scenario)}</Link>
                            <span className="muted small">{items.length} {items.length === 1 ? "expectation" : "expectations"}</span>
                          </div>
                          {items.map((item) => <ItemRow key={item.id} pid={pid} item={item} onRerun={() => void start({ stages: ["sim"], scenario })} busy={busy} />)}
                        </div>
                      ))}
                    </div>
                  </Card>
                )}
              </div>

              <div className="stack" style={{ gap: 16, alignContent: "start" }}>
                <Card title="Pipeline">
                  {/* A problem that affects most stages is stated once; narrower ones sit next to the stage they limit. */}
                  {causes.filter((c) => c.stages.length > 2).map((c) => (
                    <div key={c.missing} className="stale-notice" style={{ marginBottom: 8 }}>
                      <Icon name="alert" size={14} /><span><strong>{c.headline}.</strong> <Link to="/environment">How to fix</Link></span>
                    </div>
                  ))}
                  <StageList pid={pid} stages={status.stages} onRerun={rerun} busy={busy} countsLink
                             extra={(s) => {
                               const cause = causes.find((c) => c.stages.length <= 2 && c.stages.some((x) => x.stage === s.stage));
                               return cause ? <Link to="/environment" className="env-note">{cause.headline.split(" — ")[0]}</Link> : null;
                             }} />
                  {runs[0] && <div style={{ marginTop: 10 }}><RunLog run={runs[0]} /></div>}
                </Card>
                <ProjectFacts p={p} />
              </div>
            </div>
          </div>
        );
      }}
    </QueryView>
  );
}

function ProjectFacts({ p }: { p: ProjectDetail }) {
  const s = p.summary;
  const links: [string, string | null | undefined][] = [["Onshape document", p.links.onshape], ["GitHub repository", p.links.github], ["HTML report", p.report_url]];
  return (
    <Card title="Project">
      <div className="stack">
        {p.robot.description && <p className="secondary small">{p.robot.description}</p>}
        <dl className="kv">
          <dt>Components</dt><dd>{s.components ?? "—"}</dd>
          <dt>Harnesses · wires</dt><dd>{s.harnesses ?? "—"} · {s.wires ?? "—"}</dd>
          {s.boards !== undefined && <><dt>Boards</dt><dd>{s.boards}</dd></>}
          <dt>Software bindings</dt><dd>{s.bindings ?? "—"}</dd>
          <dt>Scenarios</dt><dd>{s.scenarios?.length ?? "—"}</dd>
        </dl>
        <div className="meta-links">
          {links.map(([label, href]) => href
            ? <a key={label} href={href} target="_blank" rel="noreferrer">{label} <Icon name="external" size={11} /></a>
            : <span key={label} className="muted">{label}: not linked</span>)}
        </div>
      </div>
    </Card>
  );
}
