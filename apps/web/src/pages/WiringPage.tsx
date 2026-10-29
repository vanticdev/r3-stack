import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError, api } from "../api/client.ts";
import { keys, useElectrical, useScenario, useScenarios, useSpec } from "../api/hooks.ts";
import type { Vec3 } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { TimeChart } from "../components/TimeChart.tsx";
import { Timeline } from "../components/Timeline.tsx";
import { BomTable } from "../components/BomTable.tsx";
import { Card, Chip, Empty, ErrorBox, Gallery, Kpi, RatioBar, Segmented } from "../components/ui.tsx";
import { wireRows, type WireRow } from "../lib/domain.ts";
import { initialHandles, routePatch } from "../lib/routeEdit.ts";
import { wireColor } from "../lib/scene.ts";
import { fmt, quantity } from "../lib/units.ts";
import { usePlayback } from "../state/playback.ts";
import { useProjectRuns } from "../state/runs.tsx";
import { RobotViewer } from "../viewer/RobotViewer.tsx";
import { useRobotScene } from "../viewer/useRobotScene.ts";

const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;

function ClearanceCell({ value, required, component }: { value: number | null; required: number; component: string | null }) {
  if (value === null) return <span className="muted">—</span>;
  const tone = value < 0 ? "critical" : value < required ? "warning" : "good";
  return (
    <span className="row" style={{ justifyContent: "flex-end", gap: 6 }} title={component ? `to ${component}` : undefined}>
      <Chip tone={tone}>{fmt(value, "m")}</Chip>
      {component && <span className="muted small">{component}</span>}
    </span>
  );
}

export function WiringPage() {
  const { pid = "" } = useParams();
  const { data, wiring, isPending, error } = useRobotScene(pid);
  const electrical = useElectrical(pid);
  const spec = useSpec(pid);
  const client = useQueryClient();
  const { start, active } = useProjectRuns(pid);
  const [harness, setHarness] = useState<string | null>(null);
  const [selectedWire, setSelectedWire] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<{ via: Vec3[]; ties: Vec3[] } | null>(null);
  const [tab, setTab] = useState<"wires" | "bom" | "slack" | "images">("wires");

  const required = wiring?.clearances.requirement ?? (quantity(spec.data?.spec.requirements?.min_wire_clearance_moving ?? "8 mm") || 0.008);
  const rows = useMemo<WireRow[]>(() => wireRows({
    routes: wiring?.routes ?? null, detail: wiring?.routing_detail ?? null,
    currents: electrical.data?.wires ?? [], slack: wiring?.slack_sim ?? null,
  }), [wiring, electrical.data]);
  const harnesses = useMemo(() => {
    const specHarnesses = spec.data?.spec.electrical?.harnesses ?? [];
    return specHarnesses.map((h) => {
      const own = rows.filter((r) => r.harness === h.id);
      const routed = wiring?.routes?.harnesses?.[h.id];
      const clearances = own.map((r) => r.clearance).filter((v): v is number => v !== null);
      const errors = (data?.diagnostics ?? []).filter((d) => d.severity === "error" && d.stage === "wiring" && (d.subjects.includes(h.id) || own.some((r) => d.subjects.includes(r.id)))).length;
      return { id: h.id, description: h.description, wires: own.length || h.wires.length, length: routed?.length ?? null, ties: routed?.tie_points.length ?? 0,
               slack: quantity(h.route?.slack ?? 0.015), clearance: clearances.length ? Math.min(...clearances) : null, errors,
               mass: (wiring?.routing_detail?.harness_mass?.[h.id]?.mass as number | undefined) ?? null,
               hasHints: !!(wiring?.harnesses?.[h.id]?.route.via?.length || h.route?.via?.length || h.route?.tie_points?.length) };
    });
  }, [spec.data, rows, wiring, data]);

  useEffect(() => { setEditing(false); setDraft(null); }, [harness]);

  const startEdit = () => {
    if (!harness || !spec.data) return;
    const routed = wiring?.routes?.harnesses?.[harness];
    setDraft(initialHandles(spec.data.spec, harness, routed?.bundle_path, routed?.tie_points));
    setEditing(true);
  };

  const save = useMutation({
    mutationFn: async () => {
      if (!harness || !draft || !spec.data) throw new Error("nothing to save");
      const response = await api.patchSpec(pid, routePatch(spec.data.spec, harness, draft.via, draft.ties), spec.data.sha256);
      await client.invalidateQueries({ queryKey: keys.section(pid, "spec") });
      await start({ stages: ["wiring"] });
      return response;
    },
    onSuccess: () => { setEditing(false); setDraft(null); },
  });

  const routed = harness ? wiring?.routes?.harnesses?.[harness] : undefined;
  const ends: [Vec3, Vec3] | null = routed?.bundle_path?.length ? [routed.bundle_path[0]!, routed.bundle_path[routed.bundle_path.length - 1]!] : null;
  const highlight = useMemo(() => (selectedWire ? [selectedWire] : harness ? [harness] : []), [harness, selectedWire]);
  const worst = wiring?.clearances.worst_dynamic ?? wiring?.slack_sim?.worst;
  const totalCut = rows.reduce((s, r) => s + (r.cutLength ?? 0), 0);
  const staticMin = rows.reduce<WireRow | null>((w, r) => (r.clearance !== null && (!w || r.clearance < (w.clearance ?? Infinity)) ? r : w), null);

  if (error) return <ErrorBox error={error} />;
  return (
    <div className="page">
      <div className="grid cols-4">
        <Kpi label="Harnesses" value={harnesses.length} sub={`${rows.length} wires · ${fmt(totalCut, "m")} cut length`} />
        <Kpi label="Static clearance to moving parts" value={fmt(staticMin?.clearance, "m")}
             sub={staticMin ? `${staticMin.id} → ${staticMin.clearanceComponent} · need ${fmt(required, "m")}` : ""}
             tone={staticMin?.clearance !== null && staticMin?.clearance !== undefined ? (staticMin.clearance >= required ? "good" : "critical") : undefined} />
        <Kpi label="Dynamic clearance (cable sim)" value={fmt(worst?.min_distance, "m")}
             sub={worst ? `${worst.wire} → ${worst.component} at ${worst.time.toFixed(3)} s` : "slack sim not run"}
             tone={worst ? (worst.min_distance >= required ? "good" : "critical") : undefined} />
        <Kpi label="Wiring diagnostics" value={(data?.diagnostics ?? []).filter((d) => d.stage === "wiring" && d.severity === "error").length}
             sub="errors from the router and slack simulation" />
      </div>

      <div className="grid side">
        <Card title={harness ? `Harness ${harness}` : "Harness routes"} flush
              subtitle={editing ? "Click a handle, drag the gizmo. Orange: route waypoints (via) · green: tie points." : "Select a harness to highlight it; edit its route hints in 3D."}
              actions={harness && (editing ? (
                <div className="row">
                  <button className="small" onClick={() => setDraft((d) => d && { ...d, via: [...d.via, d.via[d.via.length - 1] ?? ends?.[1] ?? [0, 0, 0.05]] })}><Icon name="plus" size={12} />Waypoint</button>
                  <button className="small" onClick={() => setDraft((d) => d && { ...d, via: d.via.slice(0, -1) })} disabled={!draft?.via.length}>Remove last</button>
                  <button className="small ghost" onClick={() => { setEditing(false); setDraft(null); }}>Cancel</button>
                  <button className="small primary" onClick={() => save.mutate()} disabled={save.isPending || !!active}>Save &amp; re-route</button>
                </div>
              ) : <button className="small" onClick={startEdit} disabled={!spec.data || !routed}><Icon name="wire" size={12} />Edit route</button>)}>
          <div style={{ height: 520 }}>
            {isPending || !data ? <div className="skeleton" style={{ height: "100%" }} /> : (
              <RobotViewer data={data} highlight={highlight} showJointPanel={false} initialOptions={{ xray: true }}
                           selected={selectedWire ? { kind: "wire", id: selectedWire } : null}
                           onSelect={(s) => { if (s?.kind === "wire") { setSelectedWire(s.id); setHarness(data.wires.find((w) => w.id === s.id)?.harness ?? null); } else setSelectedWire(null); }}
                           editor={editing && draft && harness ? { harness, via: draft.via, ties: draft.ties, ends, onChange: (via, ties) => setDraft({ via, ties }) } : null} />
            )}
          </div>
          {save.isError && (
            <div style={{ padding: 10 }}>
              <ErrorBox error={save.error} />
              {save.error instanceof ApiError && save.error.code === "spec_conflict" && (
                <button className="small" style={{ marginTop: 6 }} onClick={() => { void spec.refetch(); save.reset(); }}>Reload robot.yaml</button>
              )}
            </div>
          )}
          {save.isSuccess && <div style={{ padding: "8px 14px" }} className="small"><Chip tone="good">saved</Chip> robot.yaml {save.data.sha256.slice(0, 12)} · wiring stage re-running</div>}
        </Card>

        <Card title="Harnesses" flush>
          <div className="table-wrap" style={{ maxHeight: 568 }}>
            <table>
              <thead><tr><th>Harness</th><th className="num">Wires</th><th className="num">Length</th><th className="num">Clearance</th></tr></thead>
              <tbody>
                {harnesses.map((h) => (
                  <tr key={h.id} className={`clickable${harness === h.id ? " selected" : ""}`} onClick={() => { setHarness(harness === h.id ? null : h.id); setSelectedWire(null); }}>
                    <td>
                      <div className="row" style={{ gap: 6 }}><span className="mono">{h.id}</span>{h.errors > 0 && <span className="badge critical" title="wiring errors">{h.errors} E</span>}</div>
                      <div className="muted small">{h.ties} ties · slack {fmt(h.slack, "m")}{h.mass ? ` · ${fmt(h.mass, "kg")}` : ""}{h.hasHints ? " · route hints" : ""}</div>
                    </td>
                    <td className="num">{h.wires}</td>
                    <td className="num">{fmt(h.length, "m")}</td>
                    <td className="num"><ClearanceCell value={h.clearance} required={required} component={null} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card flush title={<Segmented label="Wiring tables" value={tab} onChange={setTab}
                                    options={[{ value: "wires", label: "Wires" }, { value: "bom", label: "Harness BOM" }, { value: "slack", label: "Slack & vibration" }, { value: "images", label: "Plots" }]} />}>
        {tab === "wires" && (
          <div className="table-wrap" style={{ maxHeight: 520 }}>
            <table data-testid="wire-table">
              <thead><tr><th>Wire</th><th>Harness</th><th className="num">AWG</th><th className="num">Cut length</th><th>Current vs ampacity</th>
                <th className="num">Clearance (static)</th><th className="num">Tightest bend</th><th className="num">Clearance (cable sim)</th></tr></thead>
              <tbody>
                {rows.filter((r) => !harness || r.harness === harness).map((r) => (
                  <tr key={r.id} className={`clickable${selectedWire === r.id ? " selected" : ""}`} onClick={() => { setSelectedWire(r.id); setHarness(r.harness); }}>
                    <td><span className="swatch" style={{ background: hex(wireColor(r.color)) }} /> <span className="mono">{r.id}</span> <span className="muted small">{r.signal}</span></td>
                    <td className="mono small">{r.harness}</td>
                    <td className="num">{r.awg}</td>
                    <td className="num">{fmt(r.cutLength, "m")}</td>
                    <td style={{ minWidth: 200 }}>
                      {r.current !== null && r.ampacity ? (
                        <div className="row" style={{ flexWrap: "nowrap" }}><RatioBar value={r.current} limit={r.ampacity} /><span className="num small">{fmt(r.current, "A")} / {fmt(r.ampacity, "A")}</span></div>
                      ) : <span className="muted">—</span>}
                    </td>
                    <td className="num"><ClearanceCell value={r.clearance} required={required} component={r.clearanceComponent} /></td>
                    <td className="num">{r.bendRadius !== null && r.bendRequired ? (
                      <span className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                        <Chip tone={r.bendRadius >= r.bendRequired ? "good" : "critical"}>{fmt(r.bendRadius, "m")}</Chip><span className="muted small">min {fmt(r.bendRequired, "m")}</span>
                      </span>) : <span className="muted">—</span>}</td>
                    <td className="num"><ClearanceCell value={r.slackClearance} required={required} component={r.slackComponent} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tab === "bom" && <BomTable rows={wiring?.bom ?? []} />}
        {tab === "slack" && <SlackPanel pid={pid} required={required} />}
        {tab === "images" && <div style={{ padding: 12 }}><Gallery images={wiring?.images ?? []} /></div>}
      </Card>
    </div>
  );
}

/** Cable slack/vibration playback: MuJoCo elastic-cable bodies from the steering-sweep recording
 * (or a timeline the API attaches to slack_sim), synchronized with the wire-clearance chart. */
function SlackPanel({ pid, required }: { pid: string; required: number }) {
  const scenarios = useScenarios(pid);
  const { data } = useRobotScene(pid);
  const candidates = (scenarios.data?.scenarios ?? []).filter((s) => s.last?.recording);
  const preferred = candidates.find((s) => s.id === "steering_sweep") ?? candidates[0];
  const [scenarioId, setScenarioId] = useState<string | null>(null);
  const chosen = scenarioId ?? preferred?.id ?? null;
  const scenario = useScenario(pid, chosen);
  const recording = scenario.data?.recording ?? null;
  const span = recording ? [recording.series.t[0] ?? 0, recording.series.t[recording.series.t.length - 1] ?? 0] as const : [0, 1] as const;
  const playback = usePlayback(span[0], span[1]);
  const series = useMemo(() => [{ label: "min wire clearance", values: recording?.series.wire_clearance ?? [], color: "var(--series-1)", unit: "m" }], [recording]);
  const limit = useMemo(() => ({ value: required, label: "required" }), [required]);
  const steering = useMemo(() => [{ label: "steer", values: recording?.series.steer ?? [], color: "var(--series-2)", unit: "rad" }], [recording]);

  if (scenarios.isPending) return <div className="skeleton" style={{ height: 300, margin: 12 }} />;
  if (!candidates.length) return <Empty title="No cable recordings">Run the steering_sweep scenario to record MuJoCo cable motion.</Empty>;
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="row">
        <span className="small secondary">Cable motion from scenario</span>
        <select value={chosen ?? ""} onChange={(e) => setScenarioId(e.target.value)} aria-label="Scenario">
          {candidates.map((s) => <option key={s.id}>{s.id}</option>)}
        </select>
        <span className="small muted">{recording?.frames ? `${Object.keys(recording.frames.cables ?? {}).length} MuJoCo elastic-cable spans · ${recording.frames.t.length} frames` : ""}</span>
      </div>
      {scenario.isError && <ErrorBox error={scenario.error} />}
      {scenario.isPending && <div className="skeleton" style={{ height: 420 }} />}
      {recording && data && (
        <div className="grid cols-2">
          <div style={{ height: 420 }}>
            <RobotViewer data={data} playback={{ recording, t: playback.t, follow: true }} initialOptions={{ xray: true }} showJointPanel={false} />
          </div>
          <div className="stack">
            <TimeChart title="Wire clearance to moving parts" t={recording.series.t} series={series} cursorT={playback.t}
                       onScrub={(t) => { playback.pause(); playback.seek(t); }} limit={limit} height={200} syncKey="r3-slack" />
            <TimeChart title="Steering angle" t={recording.series.t} cursorT={playback.t} onScrub={(t) => { playback.pause(); playback.seek(t); }} height={150} syncKey="r3-slack"
                       series={steering} />
          </div>
        </div>
      )}
      {recording && <Timeline playback={playback} start={span[0]} end={span[1]} />}
    </div>
  );
}
