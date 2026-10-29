import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { useSoftware } from "../api/hooks.ts";
import type { FirmwareLogLine, SoftwareResponse } from "../api/types.ts";
import { Card, Chip, Empty, QueryView, Segmented } from "../components/ui.tsx";

const LEVELS = ["all", "error", "warning", "info", "debug"] as const;
type Level = (typeof LEVELS)[number];

export function SoftwarePage() {
  const { pid = "" } = useParams();
  const software = useSoftware(pid);
  return (
    <QueryView query={software} height={400}>
      {(s) => (
        <div className="page">
          <div className="page-head">
            <div className="stack" style={{ gap: 2 }}>
              <h1>Software</h1>
              <p>Each software handle traced to the computer pin, the net it drives and the device pins it reaches (through series resistors on the HAT).</p>
            </div>
          </div>
          <Card title="Binding map" subtitle="handle → Pi pin → net → device pin" flush>
            <div className="table-wrap">
              <table data-testid="binding-map">
                <thead><tr><th>Handle</th><th>Interface</th><th>Computer pin</th><th>Net</th><th>Device pins reached</th><th>Expected</th><th>Status</th></tr></thead>
                <tbody>
                  {s.bindings.map((b) => {
                    const ok = b.expects_reached.every((e) => e.reached);
                    return b.pin_map.map((p, i) => {
                      const reached = p.reaches ?? p.devices ?? [];
                      const direct = new Set(p.devices ?? []);
                      return (
                        <tr key={`${b.handle}.${p.pin}`}>
                          {i === 0 && <td rowSpan={b.pin_map.length}><strong className="mono">{b.handle}</strong>
                            <div className="muted small">{b.device}{b.channel !== null && b.channel !== undefined ? ` · ch ${b.channel}` : ""}</div></td>}
                          {i === 0 && <td rowSpan={b.pin_map.length}><Chip tone="info">{b.interface}</Chip></td>}
                          <td className="mono">{p.pin}<div className="muted small">{p.node?.replace(/^C:/, "")}</div></td>
                          <td className="mono">{p.net_name ?? p.error ?? "—"}</td>
                          <td className="mono small">
                            {reached.map((d) => <div key={d}>{d}{!direct.has(d) && <span className="muted"> (via series link)</span>}</div>)}
                            {!reached.length && <span className="muted">—</span>}
                          </td>
                          {i === 0 && <td rowSpan={b.pin_map.length} className="mono small">
                            {b.expects_reached.map((e) => <div key={e.pin}>{e.pin} {e.reached ? "✓" : "✗"}</div>)}</td>}
                          {i === 0 && <td rowSpan={b.pin_map.length}>{ok ? <Chip tone="good">connected</Chip> : <Chip tone="critical">not reached</Chip>}</td>}
                        </tr>
                      );
                    });
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid side">
            <FirmwareLogs logs={s.firmware_logs} />
            <Card title="Firmware modules" subtitle="BLITZ modules under backend/">
              {s.modules.length ? (
                <div className="stack">
                  {s.modules.map((m) => (
                    <div key={m.name} className="stack" style={{ gap: 4 }}>
                      <div className="row"><strong className="mono">{m.name}</strong><Chip tone="muted">{m.language}</Chip><span className="muted small">runs on {m.runs_on}</span>
                        {m.exists === false && <Chip tone="critical">folder missing</Chip>}</div>
                      <span className="small muted mono">{m.path} · entry {m.entrypoint}</span>
                      {m.files && <div className="row small">{m.files.filter((f) => !f.includes("/_vendor/")).map((f) => <code key={f} className="badge">{f.split("/").pop()}</code>)}</div>}
                    </div>
                  ))}
                  {s.deploy_log_url && <a href={s.deploy_log_url} target="_blank" rel="noreferrer">Fleet deploy log →</a>}
                </div>
              ) : <Empty title="No software modules" />}
            </Card>
          </div>
        </div>
      )}
    </QueryView>
  );
}

function FirmwareLogs({ logs }: { logs: SoftwareResponse["firmware_logs"] }) {
  const [scenario, setScenario] = useState<string | null>(null);
  const [level, setLevel] = useState<Level>("all");
  const [text, setText] = useState("");
  const chosen = logs.find((l) => l.scenario === scenario) ?? logs[0];
  const lines = useMemo(() => (chosen?.firmware_logs ?? []).filter((l: FirmwareLogLine) => (level === "all" || l.level === level) &&
    (!text || `${l.message} ${JSON.stringify(l.fields ?? {})}`.toLowerCase().includes(text.toLowerCase()))), [chosen, level, text]);
  return (
    <Card title="Firmware log" subtitle={chosen ? `scenario ${chosen.scenario}: the real controller against the twin HAL` : "no simulation run yet"}
          actions={<div className="row">
            {logs.length > 1 && <select value={chosen?.scenario} onChange={(e) => setScenario(e.target.value)} aria-label="Scenario">{logs.map((l) => <option key={l.scenario}>{l.scenario}</option>)}</select>}
            <input type="search" placeholder="filter" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter log" />
            <Segmented label="Level" value={level} onChange={setLevel} options={LEVELS.map((l) => ({ value: l, label: l }))} />
          </div>} flush>
      <div className="run-log" style={{ maxHeight: 440, borderRadius: 0 }} data-testid="firmware-log">
        {lines.map((l, i) => (
          <div key={i} className={l.level === "error" ? "err" : ""}>
            <span className="muted">{l.t.toFixed(3).padStart(8)} s </span>
            <span style={{ color: l.level === "warning" ? "var(--warning)" : undefined }}>{l.level.padEnd(7)}</span> {l.message}
            {l.fields && Object.keys(l.fields).length > 0 && <span className="muted"> {Object.entries(l.fields).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" ")}</span>}
          </div>
        ))}
        {!lines.length && <span className="muted">No log lines.</span>}
        {chosen?.software_log_tail && (
          <div className="muted" style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 6, whiteSpace: "pre-wrap" }}>
            {chosen.software_log_tail}
            {chosen.software_log_url && <div><a href={chosen.software_log_url} target="_blank" rel="noreferrer">software.log →</a></div>}
          </div>
        )}
      </div>
    </Card>
  );
}
