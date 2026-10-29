import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { Diagnostic } from "../api/types.ts";
import { Chip, Empty, ErrorBox, Segmented, SeverityChip } from "../components/ui.tsx";
import { groupDiagnostics, severityCounts } from "../lib/domain.ts";
import { sortedDiagnostics } from "../lib/scene.ts";
import { RobotViewer } from "../viewer/RobotViewer.tsx";
import { useRobotScene } from "../viewer/useRobotScene.ts";

function DataLine({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && typeof v !== "object").slice(0, 8);
  if (!entries.length) return null;
  return <span className="data">{entries.map(([k, v]) => `${k}=${typeof v === "number" ? Number(v.toPrecision(4)) : String(v)}`).join("  ")}</span>;
}

export function DiagnosticsPage() {
  const { pid = "" } = useParams();
  const { data, isPending, error } = useRobotScene(pid);
  const [by, setBy] = useState<"stage" | "severity" | "subject">("stage");
  const [severity, setSeverity] = useState("");
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Diagnostic | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const all = data?.diagnostics ?? [];
  const filtered = useMemo(() => sortedDiagnostics(all, { severity, text }), [all, severity, text]);
  const groups = useMemo(() => groupDiagnostics(filtered, by), [filtered, by]);
  const counts = severityCounts(all);
  const highlight = useMemo(() => selected?.subjects ?? [], [selected]);

  if (error) return <div style={{ padding: 20 }}><ErrorBox error={error} /></div>;
  return (
    <div className="workspace" style={{ gridTemplateColumns: "560px minmax(0, 1fr)" }}>
      <div className="inspector" style={{ borderLeft: 0, borderRight: "1px solid var(--line)" }}>
        <section>
          <div className="row">
            <h2>Diagnostics</h2>
            <span className="spacer" style={{ flex: 1 }} />
            <Chip tone="critical">{counts.error} errors</Chip><Chip tone="warning">{counts.warning} warnings</Chip><Chip tone="info">{counts.info} info</Chip>
          </div>
          <div className="row">
            <Segmented label="Group by" value={by} onChange={setBy} options={[{ value: "stage", label: "Stage" }, { value: "severity", label: "Severity" }, { value: "subject", label: "Subject" }]} />
            <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity filter">
              <option value="">all severities</option><option>error</option><option>warning</option><option>info</option>
            </select>
            <input type="search" placeholder="code, subject, message" value={text} onChange={(e) => setText(e.target.value)} style={{ flex: 1 }} aria-label="Search diagnostics" />
          </div>
        </section>
        {isPending && <section><div className="skeleton" style={{ height: 300 }} /></section>}
        {!isPending && !filtered.length && <section><Empty title="No diagnostics match" /></section>}
        {groups.map((g) => {
          const open = !collapsed.has(g.key);
          return (
            <section key={g.key} style={{ gap: 6 }}>
              <button className="ghost" style={{ justifyContent: "flex-start", padding: "2px 0" }} aria-expanded={open}
                      onClick={() => setCollapsed((c) => { const n = new Set(c); if (n.has(g.key)) n.delete(g.key); else n.add(g.key); return n; })}>
                <SeverityChip severity={g.severity} /><strong className="mono">{g.key}</strong><span className="muted">{g.items.length}</span>
              </button>
              {open && g.items.map((d, i) => (
                <div key={`${d.code}${i}`} className={`diag clickable${selected === d ? " selected" : ""}`} onClick={() => setSelected(selected === d ? null : d)}
                     data-testid="diagnostic">
                  <div className="row"><SeverityChip severity={d.severity} /><code>{d.code}</code><span className="muted small">{d.stage}</span>
                    <span className="spacer" style={{ flex: 1 }} /><span className="small muted mono ellipsis" style={{ maxWidth: 200 }}>{d.subjects.join(", ")}</span></div>
                  <span className="msg">{d.message}</span>
                  {d.hint && <span className="hint">Hint: {d.hint}</span>}
                  {selected === d && <DataLine data={d.data} />}
                </div>
              ))}
            </section>
          );
        })}
      </div>
      {data ? (
        <RobotViewer data={data} highlight={highlight} showJointPanel={false} initialOptions={{ issues: true, interference: true }}
                     toolbarExtra={selected ? (
                       <div className="group">
                         <span className="small" style={{ padding: "0 6px" }}>{selected.code}</span>
                         <Link className="button small" to={`/p/${encodeURIComponent(pid)}/3d?focus=${encodeURIComponent(selected.subjects.join(","))}`}>Open in 3D</Link>
                         <button onClick={() => setSelected(null)}>Clear</button>
                       </div>) : null}
                     status={selected ? `${selected.subjects.length} subject(s): ${selected.subjects.join(", ")}` : "Click a diagnostic to focus its subjects"} />
      ) : <div className="skeleton" style={{ margin: 20 }} />}
    </div>
  );
}

