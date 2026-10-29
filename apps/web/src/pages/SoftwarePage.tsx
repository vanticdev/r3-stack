import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { useSoftware } from "../api/hooks.ts";
import { Card, Chip, Empty, QueryView, Segmented } from "../components/ui.tsx";

const LEVELS = ["all", "error", "warning", "info", "debug"] as const;

export function SoftwarePage() {
  const { pid = "" } = useParams();
  const software = useSoftware(pid);
  const [level, setLevel] = useState<(typeof LEVELS)[number]>("all");
  const [text, setText] = useState("");
  return (
    <QueryView query={software} height={400}>
      {(s) => (
        <div className="page">
          <div className="page-head">
            <div className="stack" style={{ gap: 2 }}>
              <h1>Software</h1>
              <p>Each software handle traced to the computer pin, the net it drives and the device pins on that net (through series resistors on the HAT).</p>
            </div>
          </div>
          <Card title="Binding map" subtitle="handle → Pi pin → net → device pin" flush>
            <div className="table-wrap">
              <table data-testid="binding-map">
                <thead><tr><th>Handle</th><th>Interface</th><th>Computer pin</th><th>Logical pin</th><th>Net</th><th>Device pins on the net</th><th>Expected</th><th>Status</th></tr></thead>
                <tbody>
                  {s.bindings.map((b) => b.pins.map((p, i) => (
                    <tr key={`${b.handle}.${p.pin}`}>
                      {i === 0 && <td rowSpan={b.pins.length}><strong className="mono">{b.handle}</strong><div className="muted small">{b.device}{b.channel !== null && b.channel !== undefined ? ` · ch ${b.channel}` : ""}</div></td>}
                      {i === 0 && <td rowSpan={b.pins.length}><Chip tone="info">{b.interface}</Chip></td>}
                      <td className="mono">{p.pin}</td>
                      <td className="mono">{p.logical ?? <span className="muted">unmapped</span>}</td>
                      <td className="mono">{p.net ?? "—"}</td>
                      <td className="mono small">{p.devices.filter((d) => !d.startsWith("hat.") || b.expects.includes(d)).join(", ") || "—"}
                        {p.devices.some((d) => d.startsWith("hat.") && !b.expects.includes(d)) && <div className="muted">via {p.devices.filter((d) => d.startsWith("hat.") && !b.expects.includes(d)).join(", ")}</div>}</td>
                      {i === 0 && <td rowSpan={b.pins.length} className="mono small">{b.expects.join(", ")}</td>}
                      {i === 0 && <td rowSpan={b.pins.length}>{b.ok ? <Chip tone="good">connected</Chip> : <Chip tone="critical">not reached</Chip>}</td>}
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid side">
            <FirmwareLog logs={s.firmware_logs} source={s.logs_source} level={level} setLevel={setLevel} text={text} setText={setText} raw={s.software_log} />
            <Card title="Firmware modules" subtitle="BLITZ modules under backend/">
              {s.modules.length ? (
                <div className="stack">
                  {s.modules.map((m) => (
                    <div key={m.name} className="stack" style={{ gap: 4 }}>
                      <div className="row"><strong className="mono">{m.name}</strong><Chip tone="muted">{m.language}</Chip><span className="muted small">runs on {m.runs_on}</span></div>
                      <span className="small muted mono">{m.path} · entry {m.entrypoint}</span>
                      {m.files && <div className="row small">{m.files.map((f) => <code key={f} className="badge">{f}</code>)}</div>}
                    </div>
                  ))}
                </div>
              ) : <Empty title="No software modules" />}
            </Card>
          </div>
        </div>
      )}
    </QueryView>
  );
}

function FirmwareLog({ logs, source, level, setLevel, text, setText, raw }: {
  logs: { t: number; level: string; message: string; fields?: Record<string, unknown> }[]; source?: string | null; raw?: string | null;
  level: (typeof LEVELS)[number]; setLevel: (l: (typeof LEVELS)[number]) => void; text: string; setText: (t: string) => void;
}) {
  const shown = useMemo(() => logs.filter((l) => (level === "all" || l.level === level) &&
    (!text || `${l.message} ${JSON.stringify(l.fields ?? {})}`.toLowerCase().includes(text.toLowerCase()))), [logs, level, text]);
  return (
    <Card title="Firmware log" subtitle={source ? `from ${source} (real controller against the twin HAL)` : "no simulation run yet"}
          actions={<div className="row"><input type="search" placeholder="filter" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter log" />
            <Segmented label="Level" value={level} onChange={setLevel} options={LEVELS.map((l) => ({ value: l, label: l }))} /></div>} flush>
      <div className="run-log" style={{ maxHeight: 420, borderRadius: 0 }} data-testid="firmware-log">
        {shown.map((l, i) => (
          <div key={i} className={l.level === "error" ? "err" : ""}>
            <span className="muted">{l.t.toFixed(3).padStart(8)} s </span>
            <span style={{ color: l.level === "warning" ? "var(--warning)" : undefined }}>{l.level.padEnd(7)}</span> {l.message}
            {l.fields && Object.keys(l.fields).length > 0 && <span className="muted"> {Object.entries(l.fields).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" ")}</span>}
          </div>
        ))}
        {!shown.length && <span className="muted">No log lines.</span>}
        {raw && <div className="muted" style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 6, whiteSpace: "pre-wrap" }}>{raw}</div>}
      </div>
    </Card>
  );
}
