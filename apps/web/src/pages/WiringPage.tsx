import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client.ts";
import { keys, useElectrical, useProject, useScenario, useScenarios, useSpec } from "../api/hooks.ts";
import type { Diagnostic, Vec3 } from "../api/types.ts";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { TimeChart } from "../components/TimeChart.tsx";
import { Timeline } from "../components/Timeline.tsx";
import { BomTable } from "../components/BomTable.tsx";
import { Card, Empty, ErrorBox, Gallery, RatioBar, Segmented, StaleNotice } from "../components/ui.tsx";
import { wireRows, type WireRow } from "../lib/domain.ts";
import { diagnosticFacts, diagnosticHeadline, partLabel, plural } from "../lib/labels.ts";
import { initialHandles, routePatch } from "../lib/routeEdit.ts";
import { wireColor } from "../lib/scene.ts";
import { fmt, quantity } from "../lib/units.ts";
import { usePlayback } from "../state/playback.ts";
import { useProjectRuns } from "../state/runs.tsx";
import type { HandleRef } from "../viewer/RouteEditor.tsx";
import { RobotViewer, type Marker } from "../viewer/RobotViewer.tsx";
import { useRobotScene } from "../viewer/useRobotScene.ts";

const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Clearance value with its verdict in words and colour (never colour alone). */
function Clearance({ value, required, component }: { value: number | null; required: number; component?: string | null }) {
  if (value === null) return <span className="muted">—</span>;
  const tone = value < 0 ? "critical" : value < required ? "critical" : "good";
  return (
    <span className={`clearance ${tone}`} title={component ? `to ${partLabel(component)}` : undefined}>
      {value < 0 ? `${fmt(-value, "m")} inside` : fmt(value, "m")}{component ? <span className="muted"> · {partLabel(component)}</span> : null}
    </span>
  );
}

/** Depth of a wiring failure, for ranking: penetration and negative distances first, then the smallest clearance. */
function failureDepth(d: Diagnostic): number {
  const pen = num(d.data.penetration);
  if (pen !== null) return -pen;
  const dist = num(d.data.min_distance);
  if (dist !== null) return dist;
  const ratio = num(d.data.ratio);
  return ratio !== null ? 1 + ratio : 10;
}

type Tab = "wires" | "bom" | "motion" | "images";

export function WiringPage() {
  const { pid = "" } = useParams();
  const { data, wiring, isPending, error } = useRobotScene(pid);
  const electrical = useElectrical(pid);
  const spec = useSpec(pid);
  const project = useProject(pid);
  const client = useQueryClient();
  const { start, active } = useProjectRuns(pid);
  const [harness, setHarness] = useState<string | null>(null);
  const [selectedWire, setSelectedWire] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<{ via: Vec3[]; ties: Vec3[] } | null>(null);
  const [handle, setHandle] = useState<HandleRef | null>(null);
  const [tab, setTab] = useState<Tab>("wires");
  const [showObstacles, setShowObstacles] = useState(true);
  const [fit, setFit] = useState(0);
  const [locate, setLocate] = useState<Marker | null>(null);

  const required = wiring?.clearances.requirement ?? (quantity(spec.data?.spec.requirements?.min_wire_clearance_moving ?? "8 mm") || 0.008);
  const rows = useMemo<WireRow[]>(() => wireRows({
    routes: wiring?.routes ?? null, detail: wiring?.routing_detail ?? null,
    currents: electrical.data?.wires ?? [], slack: wiring?.slack_sim ?? null,
  }), [wiring, electrical.data]);
  const wiringDiagnostics = useMemo(() => (data?.diagnostics ?? []).filter((d) => d.stage === "wiring"), [data]);
  const harnesses = useMemo(() => {
    const specHarnesses = spec.data?.spec.electrical?.harnesses ?? [];
    return specHarnesses.map((h) => {
      const own = rows.filter((r) => r.harness === h.id);
      const routed = wiring?.routes?.harnesses?.[h.id];
      const clearances = own.flatMap((r) => [r.clearance, r.slackClearance]).filter((v): v is number => v !== null);
      const worstWire = own.reduce<WireRow | null>((w, r) => (r.clearance !== null && (!w || r.clearance < (w.clearance ?? Infinity)) ? r : w), null);
      const errors = wiringDiagnostics.filter((d) => d.severity === "error" && (d.subjects.includes(h.id) || own.some((r) => d.subjects.includes(r.id)))).length;
      return {
        id: h.id, label: partLabel(h.id), description: h.description, wires: own.length || h.wires.length, length: routed?.length ?? null,
        ties: routed?.tie_points.length ?? 0, slack: quantity(h.route?.slack ?? 0.015), clearance: clearances.length ? Math.min(...clearances) : null,
        worstWire, errors, connectors: Object.keys(h.connectors ?? {}),
        mass: (wiring?.routing_detail?.harness_mass?.[h.id]?.mass as number | undefined) ?? null,
      };
    }).sort((a, b) => b.errors - a.errors || (a.clearance ?? Infinity) - (b.clearance ?? Infinity) || a.label.localeCompare(b.label));
  }, [spec.data, rows, wiring, wiringDiagnostics]);
  const worst = useMemo(() => wiringDiagnostics.filter((d) => d.severity === "error").sort((a, b) => failureDepth(a) - failureDepth(b))[0] ?? null, [wiringDiagnostics]);
  const stage = project.data?.stages.find((s) => s.stage === "wiring");

  useEffect(() => { setEditing(false); setDraft(null); setHandle(null); }, [harness]);

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

  const selected = harnesses.find((h) => h.id === harness) ?? null;
  const routed = harness ? wiring?.routes?.harnesses?.[harness] : undefined;
  const ends: [Vec3, Vec3] | null = routed?.bundle_path?.length ? [routed.bundle_path[0]!, routed.bundle_path[routed.bundle_path.length - 1]!] : null;
  // Focus: the selected wire, or the harness wires plus the parts they come closest to (the obstacles).
  const highlight = useMemo(() => {
    if (selectedWire) return [selectedWire];
    if (!harness) return [];
    const own = rows.filter((r) => r.harness === harness);
    const obstacles = showObstacles ? [...new Set(own.flatMap((r) => [r.clearanceComponent, r.slackComponent]).filter((c): c is string => !!c))] : [];
    return [...own.map((r) => r.id), ...obstacles];
  }, [harness, selectedWire, rows, showObstacles]);
  const markers = useMemo<Marker[]>(() => {
    if (locate) return [locate];
    const wire = selectedWire ?? selected?.worstWire?.id;
    const point = wire ? wiring?.routing_detail?.wire_min_clearance?.[wire] : undefined;
    return point?.position ? [{ position: point.position, label: `${fmt(point.distance, "m")} to ${partLabel(point.component)}` }] : [];
  }, [locate, selectedWire, selected, wiring]);
  const totalCut = rows.reduce((s, r) => s + (r.cutLength ?? 0), 0);
  const errorCount = wiringDiagnostics.filter((d) => d.severity === "error").length;

  const locateWorst = () => {
    if (!worst) return;
    const facts = diagnosticFacts(worst);
    const cable = worst.subjects.find((s) => harnesses.some((h) => h.id === s)) ?? null;
    const wire = typeof worst.data.worst_wire === "string" ? worst.data.worst_wire : null;
    setHarness(cable); setSelectedWire(wire);
    setLocate(facts.position ? { position: facts.position, label: facts.amount ?? undefined } : null);
    setFit((f) => f + 1);
  };
  const choose = (id: string | null) => { setHarness(id); setSelectedWire(null); setLocate(null); setFit((f) => f + 1); };

  if (error) return <ErrorBox error={error} />;
  return (
    <div className="page">
      <div className="readiness">
        <div className="summary-line">
          <span className="lead">{harnesses.length} harnesses · {rows.length} wires</span>
          <span className="secondary">{fmt(totalCut, "m")} total cut length · clearance to moving parts required ≥ {fmt(required, "m")}</span>
        </div>
        {stage?.stale && <StaleNotice what="Wiring results" since={stage.started_at} onRerun={() => void start({ stages: ["wiring"] })} rerunLabel="Rerun wiring" busy={!!active} />}
        {worst ? (
          <div className="failing-row">
            <Icon name="x" size={13} />
            <span><strong>{stage?.stale ? "Previous run, worst failure: " : "Worst failure: "}{diagnosticHeadline(worst)}</strong>
              {diagnosticFacts(worst).time !== null && <span className="muted"> · at {diagnosticFacts(worst).time!.toFixed(3)} s</span>}</span>
            <button className="small" onClick={locateWorst}><Icon name="target" size={12} />Locate interference</button>
            <Link className="button small" to={`/p/${encodeURIComponent(pid)}/diagnostics?stage=wiring`}>View {plural(errorCount, "error")}</Link>
          </div>
        ) : wiring?.routes ? <span className="secondary">No wiring errors.</span> : <span className="secondary">Wiring has not been routed yet.</span>}
      </div>

      <div className="harness-layout">
        <Card title="Harnesses" flush className="harness-list">
          <div className="list" data-testid="harness-list">
            {harnesses.map((h) => (
              <button key={h.id} className={`list-row${harness === h.id ? " selected" : ""}`} onClick={() => choose(harness === h.id ? null : h.id)} aria-pressed={harness === h.id}>
                <span className={`dot-mark${h.errors ? " critical" : h.clearance !== null && h.clearance < required ? " warning" : " good"}`} />
                <span className="stack" style={{ gap: 0, flex: 1, minWidth: 0 }}>
                  <span>{h.label}</span>
                  <span className="muted small">{plural(h.wires, "wire")}{h.errors ? ` · ${plural(h.errors, "error")}` : ""}</span>
                </span>
                <Clearance value={h.clearance} required={required} />
              </button>
            ))}
          </div>
          {selected && (
            <div className="harness-detail">
              {selected.description && <Markdown className="secondary small">{selected.description}</Markdown>}
              <dl className="kv">
                <dt>Length</dt><dd>{fmt(selected.length, "m")}</dd>
                <dt>Ties</dt><dd>{plural(selected.ties, "tie")}</dd>
                <dt>Slack</dt><dd>{fmt(selected.slack, "m")}</dd>
                {selected.mass !== null && <><dt>Mass</dt><dd>{fmt(selected.mass, "kg")}</dd></>}
                <dt>Connectors</dt><dd>{selected.connectors.join(", ") || "—"}</dd>
                {selected.worstWire && <><dt>Closest approach</dt><dd><Clearance value={selected.worstWire.clearance} required={required} component={selected.worstWire.clearanceComponent} /></dd></>}
              </dl>
            </div>
          )}
        </Card>

        <Card flush className="harness-view"
              title={harness ? partLabel(harness) : "All harnesses"}
              subtitle={editing ? "Click a handle, then drag the gizmo. Orange: route waypoints · green: tie points." : harness ? undefined : "Select a harness to frame it and see its closest approach."}
              actions={harness && editing ? (
                <div className="row">
                  <button className="small" onClick={() => setDraft((d) => d && { ...d, via: [...d.via, d.via[d.via.length - 1] ?? ends?.[1] ?? [0, 0, 0.05]] })}><Icon name="plus" size={12} />Waypoint</button>
                  <button className="small" onClick={() => setDraft((d) => d && { ...d, via: d.via.slice(0, -1) })} disabled={!draft?.via.length}>Remove last</button>
                  <button className="small ghost" onClick={() => { setEditing(false); setDraft(null); }}>Cancel</button>
                  <button className="small primary" onClick={() => save.mutate()} disabled={save.isPending || !!active}>Save &amp; re-route</button>
                </div>
              ) : null}>
          <div className="harness-canvas">
            {isPending || !data ? <div className="skeleton" style={{ height: "100%" }} /> : (
              <RobotViewer data={data} highlight={highlight} showJointPanel={false} initialOptions={{ xray: true }} markers={markers} fitRequest={fit}
                           selected={selectedWire ? { kind: "wire", id: selectedWire } : null}
                           onSelect={(s) => { setLocate(null); if (s?.kind === "wire") { setSelectedWire(s.id); setHarness(data.wires.find((w) => w.id === s.id)?.harness ?? null); } else setSelectedWire(null); }}
                           toolbarStart={harness && !editing ? (
                             <div className="group">
                               <button onClick={() => setFit((f) => f + 1)}>Fit harness</button>
                               <button className={showObstacles ? "on" : ""} aria-pressed={showObstacles} onClick={() => setShowObstacles((v) => !v)}>Show obstacles</button>
                               <button onClick={startEdit} disabled={!spec.data || !routed}>Edit route</button>
                             </div>
                           ) : null}
                           editor={editing && draft && harness ? { harness, via: draft.via, ties: draft.ties, ends, onChange: (via, ties) => setDraft({ via, ties }), onActiveChange: setHandle } : null} />
            )}
          </div>
          {editing && draft && (
            <div className="point-inspector" aria-label="Route points">
              {[...draft.via.map((p, i) => ({ kind: "via" as const, i, p })), ...draft.ties.map((p, i) => ({ kind: "tie" as const, i, p }))].map(({ kind, i, p }) => (
                <span key={`${kind}${i}`} className={handle?.kind === kind && handle.index === i ? "on" : ""}>
                  {kind === "via" ? `Waypoint ${i + 1}` : `Tie ${i + 1}`}: {p.map((v) => (v * 1000).toFixed(1)).join(", ")} mm
                </span>
              ))}
              {!draft.via.length && !draft.ties.length && <span className="muted">No waypoints yet. Add one, then drag it.</span>}
            </div>
          )}
          {save.isError && (
            <div style={{ padding: 10 }}>
              <ErrorBox error={save.error} />
              {save.error instanceof ApiError && save.error.code === "spec_conflict" && (
                <button className="small" style={{ marginTop: 6 }} onClick={() => { void spec.refetch(); save.reset(); }}>Reload robot.yaml</button>
              )}
            </div>
          )}
          {save.isSuccess && <div style={{ padding: "8px 14px" }} className="small">Saved robot.yaml; the wiring stage is re-running.</div>}
        </Card>
      </div>

      <Card flush title={
        <div className="row">
          <Segmented label="Wiring tables" value={tab} onChange={setTab}
                     options={[{ value: "wires", label: "Wires" }, { value: "bom", label: "Harness BOM" }, { value: "motion", label: "Cable motion" }, { value: "images", label: "Plots" }]} />
        </div>}
            actions={
              <label className="row small scope">
                <span className="muted">Scope</span>
                {tab === "wires" ? (
                  <select value={harness ?? ""} onChange={(e) => choose(e.target.value || null)} aria-label="Scope">
                    <option value="">All harnesses</option>
                    {harnesses.map((h) => <option key={h.id} value={h.id}>{h.label}</option>)}
                  </select>
                ) : <strong>{tab === "motion" ? "All harnesses, one scenario" : "All harnesses"}</strong>}
              </label>}>
        {tab === "wires" && (
          <div className="table-wrap" style={{ maxHeight: 520 }}>
            <table data-testid="wire-table">
              <thead><tr><th>Wire</th><th>Harness</th><th className="num">AWG</th><th className="num">Cut length</th><th>Current vs ampacity</th>
                <th className="num">Clearance, static</th><th className="num">Tightest bend</th><th className="num">Clearance, cable motion</th></tr></thead>
              <tbody>
                {rows.filter((r) => !harness || r.harness === harness).map((r) => (
                  <tr key={r.id} className={`clickable${selectedWire === r.id ? " selected" : ""}`} onClick={() => { setSelectedWire(r.id); setHarness(r.harness); setLocate(null); }}>
                    <td><span className="swatch" style={{ background: hex(wireColor(r.color)) }} /> <span className="mono">{r.id}</span> <span className="muted small">{r.signal}</span></td>
                    <td className="small">{partLabel(r.harness)}</td>
                    <td className="num">{r.awg}</td>
                    <td className="num">{fmt(r.cutLength, "m")}</td>
                    <td style={{ minWidth: 200 }}>
                      {r.current !== null && r.ampacity ? (
                        <div className="row" style={{ flexWrap: "nowrap" }}><RatioBar value={r.current} limit={r.ampacity} /><span className="num small">{fmt(r.current, "A")} / {fmt(r.ampacity, "A")}</span></div>
                      ) : <span className="muted">—</span>}
                    </td>
                    <td className="num"><Clearance value={r.clearance} required={required} component={r.clearanceComponent} /></td>
                    <td className="num">{r.bendRadius !== null && r.bendRequired ? (
                      <span className={`clearance ${r.bendRadius >= r.bendRequired ? "good" : "critical"}`}>{fmt(r.bendRadius, "m")}<span className="muted"> · min {fmt(r.bendRequired, "m")}</span></span>
                    ) : <span className="muted">—</span>}</td>
                    <td className="num"><Clearance value={r.slackClearance} required={required} component={r.slackComponent} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tab === "bom" && <BomTable rows={wiring?.bom ?? []} />}
        {tab === "motion" && <CableMotionPanel pid={pid} required={required} />}
        {tab === "images" && <div style={{ padding: 12 }}><Gallery images={wiring?.images ?? []} /></div>}
      </Card>
    </div>
  );
}

/** Cable motion: MuJoCo elastic-cable bodies from a recorded project scenario,
 * synchronized with the wire-clearance chart. */
function CableMotionPanel({ pid, required }: { pid: string; required: number }) {
  const scenarios = useScenarios(pid);
  const { data } = useRobotScene(pid);
  const candidates = (scenarios.data?.scenarios ?? []).filter((s) => s.last?.recording);
  const preferred = candidates[0];
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
  if (!candidates.length) return <Empty title="No cable recordings">Run the steering sweep scenario to record cable motion.</Empty>;
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="row">
        <span className="small secondary">Recorded in scenario</span>
        <select value={chosen ?? ""} onChange={(e) => setScenarioId(e.target.value)} aria-label="Scenario">
          {candidates.map((s) => <option key={s.id} value={s.id}>{partLabel(s.id)}</option>)}
        </select>
        {scenario.data?.last?.stale && <span className="tag outdated"><Icon name="clock" size={11} />Previous run · inputs changed</span>}
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
