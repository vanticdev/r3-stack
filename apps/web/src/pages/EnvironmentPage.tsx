import { useMutation, useQueryClient } from "@tanstack/react-query";

import { agentProviders, api } from "../api/client.ts";
import { keys, useDoctor } from "../api/hooks.ts";
import { Card, CheckRow, Details, ErrorBox } from "../components/ui.tsx";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { AGENT_NAMES, AGENT_SETUP, environmentCauses, optionalMissing } from "../lib/environment.ts";
import { stageLabel } from "../lib/labels.ts";
import { timeAgo } from "../lib/units.ts";
import { useMockBackend } from "../state/prefs.ts";

/** Workspace-level environment: what blocks stages from running here, and how to fix it. */
export function EnvironmentPage() {
  const doctor = useDoctor();
  const client = useQueryClient();
  const mock = useMockBackend();
  const recheck = useMutation({
    mutationFn: () => api.doctor(undefined, true),
    onSuccess: (data) => client.setQueryData(keys.doctor, data),
  });
  const d = doctor.data;
  const causes = d ? environmentCauses(d) : [];
  const optional = d ? optionalMissing(d, causes) : [];
  const agents = agentProviders(d);
  return (
    <main className="content">
      <div className="page" style={{ maxWidth: 1080 }}>
        <div className="page-head">
          <h1>Environment</h1>
          <div className="spacer" />
          <button className="primary" onClick={() => recheck.mutate()} disabled={recheck.isPending || doctor.isFetching}>
            <Icon name="refresh" size={14} />{recheck.isPending ? "Checking…" : "Check environment again"}
          </button>
        </div>
        {recheck.isError && <ErrorBox error={recheck.error} />}

        {doctor.isPending ? <div className="skeleton" style={{ height: 240 }} /> : doctor.isError ? (
          <Card>
            <div className="check-list">
              <CheckRow outcome="failed" title="R3 API unreachable — stages cannot execute">
                <span>Start it from the repository root: <code>r3 serve --projects-root examples/robots</code>, then check again.</span>
                <ErrorBox error={doctor.error} />
              </CheckRow>
            </div>
          </Card>
        ) : d && (
          <>
            <Card title={causes.length ? `${causes.length === 1 ? "1 problem limits" : `${causes.length} problems limit`} what can run here` : "Every stage can run at full fidelity here"}>
              {causes.length ? (
                <div className="check-list" data-testid="environment-causes">
                  {causes.map((c) => (
                    <CheckRow key={c.missing} outcome={c.level === "unavailable" ? "failed" : "warning"} title={c.headline}>
                      <Markdown className="secondary">{`Fix: ${c.fix}`}</Markdown>
                      <span>Affects {c.stages.map((s) => stageLabel(s.stage)).join(", ")}</span>
                      {c.tool?.detail && <Markdown>{`Detected: ${c.tool.detail}`}</Markdown>}
                    </CheckRow>
                  ))}
                </div>
              ) : <span className="muted">All tools the stages need were found{d.checked_at ? ` (checked ${timeAgo(d.checked_at)})` : ""}.</span>}
            </Card>

            <div className="grid cols-2">
              <Card title="Agents">
                <div className="check-list">
                  {agents.map((a) => (
                    <CheckRow key={a.provider} outcome={a.available ? "passed" : "not_run"} title={AGENT_NAMES[a.provider] ?? a.provider}
                              result={a.available ? (a.version ?? "available") : "not available"}>
                      {!a.available && <Markdown>{mock ? "Agent sessions run through r3 serve; start it to use agents." : AGENT_SETUP[a.provider] ?? a.detail}</Markdown>}
                    </CheckRow>
                  ))}
                  {!agents.length && <span className="muted">This server does not report agent CLIs.</span>}
                </div>
              </Card>
              <Card title="This machine">
                <dl className="kv">
                  <dt>API</dt><dd>{mock ? "Mock (dev server, build/ artifacts)" : "r3 serve"}</dd>
                  <dt>Platform</dt><dd>{d.environment.platform}</dd>
                  <dt>Python</dt><dd>{d.environment.python}</dd>
                  <dt>Display</dt><dd>{d.environment.display ? "available" : "none (headless)"}</dd>
                  <dt>R3 worker image</dt><dd>{d.environment.worker_image ? "yes" : "no"}</dd>
                  {d.checked_at && <><dt>Checked</dt><dd>{timeAgo(d.checked_at)}</dd></>}
                </dl>
              </Card>
            </div>

            <Card title="Stage readiness" flush>
              <div className="table-wrap">
                <table data-testid="doctor-stages">
                  <thead><tr><th>Stage</th><th>Readiness</th><th>Needs</th></tr></thead>
                  <tbody>
                    {d.stages.map((s) => (
                      <tr key={s.stage}>
                        <td><strong>{stageLabel(s.stage)}</strong></td>
                        <td>{s.level === "full" ? "Full fidelity" : s.level === "degraded" ? <strong>Reduced fidelity</strong> : <strong style={{ color: "var(--critical)" }}>Cannot run</strong>}
                          {s.missing.length > 0 && <div className="muted small">missing {s.missing.join(", ")}</div>}</td>
                        <td className="small muted"><Markdown>{s.note}</Markdown></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Details summary={`Tools (${Object.values(d.tools).filter((t) => t.available).length} of ${Object.keys(d.tools).length} found${optional.length ? `, ${optional.length} optional missing` : ""})`}>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Tool</th><th>Found</th><th>Version</th><th>Detail</th></tr></thead>
                  <tbody>
                    {Object.values(d.tools).map((t) => (
                      <tr key={t.name}>
                        <td className="mono">{t.name}</td>
                        <td>{t.available ? "yes" : <strong>no</strong>}</td>
                        <td className="mono small">{t.version ?? "—"}</td>
                        <td className="small muted"><Markdown>{t.detail}</Markdown></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Details>
          </>
        )}
      </div>
    </main>
  );
}
