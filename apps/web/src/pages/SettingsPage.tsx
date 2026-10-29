import { useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { keys, useDoctor } from "../api/hooks.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, QueryView } from "../components/ui.tsx";
import { useMockBackend, useTheme } from "../state/prefs.ts";

export function SettingsPage() {
  const doctor = useDoctor();
  const client = useQueryClient();
  const [theme, setTheme] = useTheme();
  const mock = useMockBackend();
  const { pid } = useParams();
  // Inside a project the layout already provides the scrolling content area.
  const Wrapper = pid ? "div" : "main";
  return (
    <Wrapper className={pid ? "" : "content"}>
      <div className="page">
        <div className="page-head">
          <div className="stack" style={{ gap: 2 }}>
            <h1>Settings · r3 doctor</h1>
            <p>What this environment can run headless: external tools, GL backends and per-stage readiness.</p>
          </div>
          <div className="spacer" />
          <button onClick={() => void client.invalidateQueries({ queryKey: keys.doctor })}><Icon name="refresh" size={14} />Re-run doctor</button>
        </div>
        <QueryView query={doctor} height={300}>
          {(d) => (
            <>
              <div className="grid cols-4">
                <Card title="Environment">
                  <dl className="kv">
                    <dt>Python</dt><dd>{d.environment.python}</dd>
                    <dt>Platform</dt><dd>{d.environment.platform}</dd>
                    <dt>Display</dt><dd>{d.environment.display ? "yes" : "headless"}</dd>
                    <dt>Worker image</dt><dd>{d.environment.worker_image ? "yes" : "no"}</dd>
                    <dt>CI</dt><dd>{d.environment.ci ? "yes" : "no"}</dd>
                  </dl>
                </Card>
                <Card title="API">
                  <div className="stack small">
                    {mock ? <Chip tone="warning">mock API (dev server)</Chip> : <Chip tone="good">r3 serve</Chip>}
                    <span className="muted">The dev server proxies <code>/api</code> to <code>R3_API_URL</code> and falls back to the build/ mock when it is down.</span>
                  </div>
                </Card>
                <Card title="Agents">
                  <div className="stack small">
                    {Object.entries(d.agents ?? {}).map(([k, v]) => (
                      <div key={k} className="row"><Chip tone={v.available ? "good" : "warning"}>{k}</Chip><span className="muted">{v.version ?? v.detail ?? ""}</span></div>
                    ))}
                    {!d.agents && <span className="muted">not reported</span>}
                  </div>
                </Card>
                <Card title="Appearance">
                  <div className="row">
                    <button className={theme === "dark" ? "active" : ""} onClick={() => setTheme("dark")}><Icon name="moon" size={14} />Dark</button>
                    <button className={theme === "light" ? "active" : ""} onClick={() => setTheme("light")}><Icon name="sun" size={14} />Light</button>
                  </div>
                </Card>
              </div>
              <div className="grid cols-2">
                <Card title="Stages" flush>
                  <table data-testid="doctor-stages">
                    <thead><tr><th>Stage</th><th>Level</th><th>Headless</th><th>Missing</th></tr></thead>
                    <tbody>
                      {d.stages.map((s) => (
                        <tr key={s.stage}>
                          <td><strong>{s.stage}</strong><div className="muted small">{s.note}</div></td>
                          <td><Chip tone={s.level === "full" ? "good" : s.level === "degraded" ? "warning" : "critical"}>{s.level}</Chip></td>
                          <td>{s.headless ? "yes" : "no"}</td>
                          <td className="small">{s.missing.join(", ") || <span className="muted">—</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
                <Card title="Tools" flush>
                  <table>
                    <thead><tr><th>Tool</th><th>Status</th><th>Version</th><th>Detail</th></tr></thead>
                    <tbody>
                      {Object.values(d.tools).map((t) => (
                        <tr key={t.name}>
                          <td className="mono">{t.name}</td>
                          <td>{t.available ? <Chip tone="good">available</Chip> : <Chip tone="muted">missing</Chip>}</td>
                          <td className="mono small">{t.version ?? "—"}</td>
                          <td className="small muted">{t.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              </div>
            </>
          )}
        </QueryView>
      </div>
    </Wrapper>
  );
}
