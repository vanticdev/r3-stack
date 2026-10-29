import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { useScenario, useScenarios, useSpec } from "../api/hooks.ts";
import type { PlaybackRecording, ScenarioListItem, ScenarioResponse } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { TimeChart, type ChartSeries } from "../components/TimeChart.tsx";
import { Timeline } from "../components/Timeline.tsx";
import { Card, Chip, Empty, ErrorBox, Gallery, QueryView } from "../components/ui.tsx";
import { fmt, humanize, quantity } from "../lib/units.ts";
import { usePlayback, type Playback } from "../state/playback.ts";
import { useProjectRuns } from "../state/runs.tsx";
import { RobotViewer } from "../viewer/RobotViewer.tsx";
import { useRobotScene } from "../viewer/useRobotScene.ts";

interface ChartDef {
  title: string;
  series: ChartSeries[];
  unit: string;
  limit?: { value: number; label: string };
}

/** Chart definitions for the recorded channels present in this recording (one unit per chart). */
export function chartDefs(recording: PlaybackRecording, minClearance?: number): ChartDef[] {
  const s = recording.series;
  const line = (key: string, label: string, color: string, unit: string, scale = 1, dash = false): ChartSeries | null =>
    s[key] ? { label, values: scale === 1 ? s[key]! : s[key]!.map((v) => v * scale), color, unit, dash } : null;
  const defs: (ChartDef | null)[] = [
    { title: "Speed", unit: "m/s", series: [line("speed", "speed", "var(--series-1)", "m/s")].filter((x) => !!x) },
    { title: "Battery voltage", unit: "V", series: [line("battery_v", "pack", "var(--series-1)", "V")].filter((x) => !!x) },
    { title: "Battery current", unit: "A", series: [line("battery_i", "battery", "var(--series-2)", "A")].filter((x) => !!x) },
    { title: "State of charge", unit: "%", series: [line("soc", "SOC", "var(--series-3)", "%", 100)].filter((x) => !!x) },
    { title: "Rails", unit: "V", series: [line("rail_5v", "5V (HAT)", "var(--series-1)", "V"), line("bec_v", "BEC 6V", "var(--series-2)", "V")].filter((x) => !!x) },
    { title: "Motor current", unit: "A", series: [line("motor_current", "motor", "var(--series-2)", "A")].filter((x) => !!x) },
    { title: "Steering", unit: "rad", series: [line("steer", "actual", "var(--series-1)", "rad"), line("steer_target", "target", "var(--series-2)", "rad", 1, true)].filter((x) => !!x) },
    { title: "RSSI", unit: "dBm", series: [line("rssi", "RSSI", "var(--series-7)", "dBm")].filter((x) => !!x) },
    { title: "Link quality", unit: "%", series: [line("lq", "LQ", "var(--series-3)", "%")].filter((x) => !!x) },
    { title: "Wire clearance", unit: "m", series: [line("wire_clearance", "min clearance", "var(--series-1)", "m")].filter((x) => !!x),
      ...(minClearance ? { limit: { value: minClearance, label: "required" } } : {}) },
  ];
  return defs.filter((d): d is ChartDef => !!d && d.series.length > 0);
}

function ScenarioCard({ item, selected, onSelect, onRun, busy }: {
  item: ScenarioListItem; selected: boolean; onSelect: () => void; onRun: () => void; busy: boolean;
}) {
  const last = item.last;
  return (
    <div className={`card scenario-card${selected ? " selected" : ""}`} onClick={onSelect} role="button" tabIndex={0}
         onKeyDown={(e) => e.key === "Enter" && onSelect()} data-testid="scenario-card">
      <div className="row">
        <strong className="mono">{item.id}</strong>
        <span className="spacer" style={{ flex: 1 }} />
        {!last ? <Chip tone="muted">not run</Chip> : last.stale ? <Chip tone="stale">stale</Chip> : last.passed ? <Chip tone="good">pass</Chip> : <Chip tone="critical">fail</Chip>}
      </div>
      <span className="small secondary" style={{ minHeight: 34 }}>{item.description}</span>
      <div className="row small">
        <span className="muted">{fmt(item.duration, "s")}</span>
        {last && <span className="muted">{last.checks.filter((c) => c.pass).length}/{last.checks.length} checks</span>}
        <span className="spacer" style={{ flex: 1 }} />
        <button className="small" disabled={busy} onClick={(e) => { e.stopPropagation(); onRun(); }}><Icon name="play" size={11} />Run</button>
      </div>
    </div>
  );
}

function ClipPlayer({ url, playback, duration }: { url: string; playback: Playback; duration: number }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = video.current;
    if (!v || !Number.isFinite(v.duration) || v.duration === 0) return;
    // The clip spans the scenario; map scenario time onto it proportionally.
    const target = (playback.t / duration) * v.duration;
    if (Math.abs(v.currentTime - target) > 0.12) v.currentTime = target;
  }, [playback.t, duration]);
  return <video ref={video} src={url} muted playsInline preload="metadata" controls={false} style={{ width: "100%", borderRadius: 6, background: "#000" }} />;
}

function ScenarioDetail({ pid, response }: { pid: string; response: ScenarioResponse }) {
  const { data } = useRobotScene(pid);
  const recording = response.recording;
  const t = recording?.series.t ?? [];
  const start = t[0] ?? 0;
  const end = t[t.length - 1] ?? response.summary.duration;
  const playback = usePlayback(start, end);
  const [follow, setFollow] = useState(true);
  const spec = useSpec(pid);
  const minClearance = quantity(spec.data?.spec.requirements?.min_wire_clearance_moving);
  const charts = useMemo(() => (recording ? chartDefs(recording, Number.isFinite(minClearance) ? minClearance : undefined) : []), [recording, minClearance]);
  const scrub = (value: number) => { playback.pause(); playback.seek(value); };
  const summary = response.summary;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid side">
        <Card flush title={`Playback · ${summary.scenario}`} subtitle={recording ? `${recording.bodies.names.length} bodies · ${recording.cables.length} cables · ${recording.bodies.t.length} frames at ${recording.hz} Hz` : "no recording"}
              actions={<label className="check small"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />Follow vehicle</label>}>
          <div style={{ height: 470 }}>
            {!recording ? <Empty title="No recording">The scenario summary exists but recording.npz is missing.</Empty>
              : !data ? <div className="skeleton" style={{ height: "100%" }} />
              : <RobotViewer data={data} playback={{ recording, t: playback.t, follow }} showJointPanel={false} />}
          </div>
          {recording && <Timeline playback={playback} start={start} end={end} />}
        </Card>
        <div className="stack" style={{ gap: 16, alignContent: "start" }}>
          <Card title="Checks">
            <table>
              <tbody>
                {summary.checks.map((c) => (
                  <tr key={c.key}>
                    <td>{c.pass ? <Chip tone="good">pass</Chip> : <Chip tone="critical">fail</Chip>}</td>
                    <td><span className="mono">{c.key}</span>{c.detail && <div className="muted small">{c.detail}</div>}</td>
                    <td className="num">{String(c.measured)}<div className="muted small">expect {String(c.expected)}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title="Metrics">
            <dl className="kv">
              {Object.entries(summary.metrics).filter(([, v]) => typeof v !== "object" || v === null).slice(0, 18).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}><dt>{humanize(k)}</dt><dd>{typeof v === "number" ? fmt(v) : String(v)}</dd></div>
              ))}
            </dl>
          </Card>
          {response.clip_url && <Card title="MP4 clip" subtitle="MuJoCo offscreen render, synced to the timeline"><ClipPlayer url={response.clip_url} playback={playback} duration={end} /></Card>}
        </div>
      </div>

      {recording && (
        <Card title="Time series" subtitle="Drag on any chart to scrub; all charts and the 3D view share the playhead">
          <div className="grid cols-3" data-testid="sim-charts">
            {charts.map((c) => (
              <TimeChart key={c.title} title={c.title} t={recording.series.t} series={c.series} unit={c.unit} cursorT={playback.t} onScrub={scrub} limit={c.limit} />
            ))}
          </div>
        </Card>
      )}
      <Card title="Renders"><Gallery images={response.renders} /></Card>
    </div>
  );
}

export function SimulationPage() {
  const { pid = "" } = useParams();
  const scenarios = useScenarios(pid);
  const [params, setParams] = useSearchParams();
  const { start, active } = useProjectRuns(pid);
  const [error, setError] = useState<unknown>(null);
  const firstRun = scenarios.data?.find((s) => s.last)?.id ?? scenarios.data?.[0]?.id ?? null;
  const selected = params.get("scenario") ?? firstRun;
  const scenario = useScenario(pid, selected && scenarios.data?.find((s) => s.id === selected)?.last ? selected : null);
  const run = (id: string) => { setError(null); start({ stages: ["sim"], scenario: id }).catch(setError); };
  return (
    <div className="page">
      <div className="page-head">
        <div className="stack" style={{ gap: 2 }}>
          <h1>Simulation</h1>
          <p>Lockstep co-simulation: MuJoCo vehicle and elastic cables, ngspice electrical network, radio link, sensors, and the real firmware on the twin HAL.</p>
        </div>
      </div>
      {error ? <ErrorBox error={error} /> : null}
      <QueryView query={scenarios} height={120}>
        {(list) => (
          <div className="grid cols-4">
            {list.map((s) => <ScenarioCard key={s.id} item={s} selected={s.id === selected} busy={!!active}
                                           onSelect={() => setParams({ scenario: s.id }, { replace: true })} onRun={() => run(s.id)} />)}
          </div>
        )}
      </QueryView>
      {selected && !scenarios.data?.find((s) => s.id === selected)?.last ? (
        <Card><Empty title={`${selected} has not been run`}><button className="primary" onClick={() => run(selected)} disabled={!!active}>Run {selected}</button></Empty></Card>
      ) : selected ? (
        <QueryView query={scenario} height={480}>{(response) => <ScenarioDetail key={selected} pid={pid} response={response} />}</QueryView>
      ) : null}
    </div>
  );
}
