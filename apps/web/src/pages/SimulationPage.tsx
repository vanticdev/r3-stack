import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { useScenario, useScenarios, useSpec } from "../api/hooks.ts";
import type { PlaybackRecording, ScenarioCheck, ScenarioListItem, ScenarioResponse } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { TimeChart, type ChartSeries } from "../components/TimeChart.tsx";
import { Timeline } from "../components/Timeline.tsx";
import { Card, CheckRow, Details, Empty, ErrorBox, Gallery, QueryView } from "../components/ui.tsx";
import { partLabel, plural } from "../lib/labels.ts";
import { formatTarget, formatValue, itemLabel, itemUnit } from "../lib/release.ts";
import { fmt, quantity, timeAgo } from "../lib/units.ts";
import { usePlayback, type Playback } from "../state/playback.ts";
import { useProjectRuns } from "../state/runs.tsx";
import { RobotViewer } from "../viewer/RobotViewer.tsx";
import { useRobotScene } from "../viewer/useRobotScene.ts";

type ChartGroup = "motion" | "power" | "radio" | "wiring";
const GROUP_LABEL: Record<ChartGroup, string> = { motion: "Motion", power: "Power", radio: "Radio", wiring: "Wiring" };

interface ChartDef {
  title: string;
  group: ChartGroup;
  series: ChartSeries[];
  unit: string;
  limit?: { value: number; label: string };
}

/** Chart definitions for the recorded channels present in this recording (one unit per chart). */
export function chartDefs(recording: PlaybackRecording, minClearance?: number): ChartDef[] {
  const s = recording.series;
  const line = (key: string, label: string, color: string, unit: string, scale = 1, dash = false): ChartSeries | null =>
    s[key] ? { label, values: scale === 1 ? s[key]! : s[key]!.map((v) => (v === null ? null : v * scale)), color, unit, dash } : null;
  const defs: (ChartDef | null)[] = [
    { title: "Speed", group: "motion", unit: "m/s", series: [line("speed", "speed", "var(--series-1)", "m/s")].filter((x) => !!x) },
    { title: "Steering", group: "motion", unit: "rad", series: [line("steer", "actual", "var(--series-1)", "rad"), line("steer_target", "target", "var(--series-2)", "rad", 1, true)].filter((x) => !!x) },
    { title: "Battery voltage", group: "power", unit: "V", series: [line("battery_v", "pack", "var(--series-1)", "V")].filter((x) => !!x) },
    { title: "Battery current", group: "power", unit: "A", series: [line("battery_i", "battery", "var(--series-2)", "A")].filter((x) => !!x) },
    { title: "State of charge", group: "power", unit: "%", series: [line("soc", "SOC", "var(--series-3)", "%", 100)].filter((x) => !!x) },
    { title: "Rails", group: "power", unit: "V", series: [line("rail_5v", "5V (HAT)", "var(--series-1)", "V"), line("bec_v", "BEC 6V", "var(--series-2)", "V")].filter((x) => !!x) },
    { title: "Motor current", group: "power", unit: "A", series: [line("motor_current", "motor", "var(--series-2)", "A")].filter((x) => !!x) },
    { title: "RSSI", group: "radio", unit: "dBm", series: [line("rssi", "RSSI", "var(--series-7)", "dBm")].filter((x) => !!x) },
    { title: "Link quality", group: "radio", unit: "%", series: [line("lq", "LQ", "var(--series-3)", "%")].filter((x) => !!x) },
    { title: "Wire clearance", group: "wiring", unit: "m", series: [line("wire_clearance", "min clearance", "var(--series-1)", "m")].filter((x) => !!x),
      ...(minClearance ? { limit: { value: minClearance, label: "required" } } : {}) },
  ];
  return defs.filter((d): d is ChartDef => !!d && d.series.length > 0);
}

// ------------------------------------------------------------------ checks and metrics

/** Simulation time a check refers to ("100 ms average, at t=5.16s" → 5.16). */
export function checkTime(check: Pick<ScenarioCheck, "detail">): number | null {
  const m = check.detail?.match(/\bt\s*=\s*([\d.]+)\s*s/);
  return m ? Number(m[1]) : null;
}

const asItem = (key: string) => ({ kind: "scenario_expectation" as const, subject: key, id: `scenario.x.${key}` });

/** "Top speed: 8 m/s · required ≥ 8 m/s · margin 0 m/s". */
function checkText(c: ScenarioCheck): { label: string; value: string; target: string; margin: string | null } {
  const item = asItem(c.key);
  const unit = itemUnit(item);
  const target = formatTarget({ ...item, target: c.expected });
  const margin = typeof c.measured === "number" && typeof c.expected === "number" && unit && unit !== "%"
    ? fmt(Math.abs(c.measured - c.expected), unit) : null;
  return { label: itemLabel(item), value: formatValue(c.measured, unit), target, margin };
}

const METRIC_GROUPS: { title: string; keys: string[] }[] = [
  { title: "Motion", keys: ["top_speed_mps", "distance_m", "vehicle_mass_kg", "laps_completed", "max_cross_track_error_m"] },
  { title: "Power", keys: ["min_battery_voltage_v", "final_soc", "energy_wh", "peak_battery_current_a", "peak_motor_current_a", "min_rail_5v", "brownouts"] },
  { title: "Radio", keys: ["max_tx_distance_m", "min_rssi_dbm", "link_lost", "link_margin_at_range_requirement_db", "packets_ok", "packets_lost"] },
  { title: "Wiring", keys: ["min_wire_clearance_m", "min_wire_clearance_wire", "min_wire_clearance_component"] },
  { title: "Run", keys: ["sim_time_s", "wall_time_s", "realtime_factor", "radio_backend"] },
];
const METRIC_LABEL: Record<string, string> = {
  top_speed_mps: "Top speed", distance_m: "Distance", vehicle_mass_kg: "Vehicle mass", min_battery_voltage_v: "Lowest battery voltage",
  final_soc: "Charge at the end", energy_wh: "Energy used", peak_battery_current_a: "Peak battery current", peak_motor_current_a: "Peak motor current",
  min_rail_5v: "Lowest 5 V rail", brownouts: "Brownouts", max_tx_distance_m: "Farthest from transmitter", min_rssi_dbm: "Weakest signal",
  link_lost: "Link lost at", link_margin_at_range_requirement_db: "Link margin at required range", packets_ok: "Packets received", packets_lost: "Packets lost",
  min_wire_clearance_m: "Smallest wire clearance", min_wire_clearance_wire: "Closest wire", min_wire_clearance_component: "Closest part",
  sim_time_s: "Simulated time", wall_time_s: "Wall time", realtime_factor: "Real-time factor", radio_backend: "Radio model",
  laps_completed: "Laps completed", max_cross_track_error_m: "Largest cross-track error",
};
const UNIT_SUFFIX: [RegExp, string][] = [[/_mps$/, "m/s"], [/_m$/, "m"], [/_v$|_5v$/, "V"], [/_a$/, "A"], [/_wh$/, "Wh"], [/_dbm$/, "dBm"], [/_db$/, "dB"], [/_kg$/, "kg"], [/_s$/, "s"]];

export function metricValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "not recorded";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value !== "number") return /^[a-z_]+$/.test(String(value)) ? partLabel(String(value)).replace(/_/g, " ") : String(value);
  if (/soc$|_fraction$/.test(key)) return `${(value * 100).toFixed(1)} %`;
  if (key === "realtime_factor") return `${value}×`;
  const unit = UNIT_SUFFIX.find(([re]) => re.test(key))?.[1] ?? "";
  return fmt(value, unit);
}

function Metrics({ metrics }: { metrics: Record<string, unknown> }) {
  const shown = new Set(METRIC_GROUPS.flatMap((g) => g.keys));
  const rest = Object.entries(metrics).filter(([k, v]) => !shown.has(k) && (typeof v !== "object" || v === null));
  return (
    <div className="metric-groups">
      {METRIC_GROUPS.map((g) => {
        const rows = g.keys.filter((k) => k in metrics);
        if (!rows.length) return null;
        return (
          <div key={g.title}>
            <h3>{g.title}</h3>
            <dl className="kv">
              {rows.map((k) => <div key={k} style={{ display: "contents" }}><dt>{METRIC_LABEL[k] ?? k}</dt><dd>{metricValue(k, metrics[k])}</dd></div>)}
            </dl>
          </div>
        );
      })}
      {rest.length > 0 && (
        <Details summary={`${plural(rest.length, "more value")}`}>
          <dl className="kv">{rest.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{metricValue(k, v)}</dd></div>)}</dl>
        </Details>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ playback pieces

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

function ScenarioDetail({ pid, response, item, onRun, busy }: { pid: string; response: ScenarioResponse; item: ScenarioListItem; onRun: () => void; busy: boolean }) {
  const { data } = useRobotScene(pid);
  const recording = response.recording;
  const t = recording?.series.t ?? [];
  const start = t[0] ?? 0;
  const end = t[t.length - 1] ?? recording?.duration ?? 0;
  const playback = usePlayback(start, end);
  const [follow, setFollow] = useState(true);
  const [groups, setGroups] = useState<Set<ChartGroup>>(new Set(["motion", "power", "radio", "wiring"]));
  const spec = useSpec(pid);
  const minClearance = quantity(spec.data?.spec.requirements?.min_wire_clearance_moving);
  const charts = useMemo(() => (recording ? chartDefs(recording, Number.isFinite(minClearance) ? minClearance : undefined) : []), [recording, minClearance]);
  const available = [...new Set(charts.map((c) => c.group))];
  const seek = (value: number) => { playback.pause(); playback.seek(value); };
  const last = response.last ?? item.last;
  const frames = recording?.frames;
  const has3d = !!frames && !frames.error && !!data;
  const checks = (last?.checks ?? []).slice().sort((a, b) => Number(a.pass) - Number(b.pass));
  const passed = checks.filter((c) => c.pass).length;
  const failed = checks.length - passed;
  const name = partLabel(item.id);

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="readiness">
        <div className="summary-line">
          <span className="lead">{last?.stale ? "Previous run: " : ""}{failed ? `${plural(failed, "check")} failed` : `${plural(passed, "check")} passed`}{failed && passed ? `, ${passed} passed` : ""}</span>
          {last?.stale && <span className="tag outdated"><Icon name="clock" size={11} />Inputs changed since this run</span>}
          <span className="secondary">{item.description}</span>
          <span className="spacer" style={{ flex: 1 }} />
          <button className={last?.stale || !last?.passed ? "primary" : ""} onClick={onRun} disabled={busy}><Icon name="play" size={12} />Rerun {name}</button>
        </div>
        {last?.inputs && <Details summary="Run revision" rows={[["robot.yaml", last.inputs.robot_yaml_sha256], ["Git commit", last.inputs.git_commit], ["CAD revision", last.inputs.cad_revision]]} />}
      </div>

      {recording && (
        <div className="playbar" data-testid="playbar">
          <Timeline playback={playback} start={start} end={end} label={has3d ? undefined : "time series only"} />
        </div>
      )}

      <div className="grid side" style={{ alignItems: "start" }}>
        <div className="stack" style={{ gap: 16 }}>
          {has3d ? (
            <Card flush title="Playback" actions={<label className="check small"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />Follow vehicle</label>}>
              <div style={{ height: 440 }}>
                <RobotViewer data={data!} playback={{ recording: recording!, t: playback.t, follow }} showJointPanel={false} />
              </div>
            </Card>
          ) : (
            <div className="stale-notice neutral" role="status"><Icon name="info" size={14} />
              <span>{recording ? "3D recording unavailable for this run; the time series below are complete." : "This run has no recording; only its checks and metrics are available."}</span></div>
          )}
          {recording && (
            <Card title="Time series" subtitle="Drag on a chart to scrub; every chart and the 3D view share the playhead"
                  actions={<div className="row chips" role="group" aria-label="Chart groups">
                    {available.map((g) => (
                      <button key={g} className={`small${groups.has(g) ? " active" : ""}`} aria-pressed={groups.has(g)}
                              onClick={() => setGroups((s) => { const n = new Set(s); if (n.has(g)) n.delete(g); else n.add(g); return n; })}>{GROUP_LABEL[g]}</button>
                    ))}
                  </div>}>
              <div className="grid cols-2" data-testid="sim-charts">
                {charts.filter((c) => groups.has(c.group)).map((c) => (
                  <TimeChart key={c.title} title={c.title} t={recording.series.t} series={c.series} unit={c.unit} cursorT={playback.t} onScrub={seek} limit={c.limit} />
                ))}
              </div>
            </Card>
          )}
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <Card title="Checks">
            <div className="check-list" data-testid="sim-checks">
              {checks.map((c) => {
                const text = checkText(c);
                const at = checkTime(c);
                return (
                  <CheckRow key={c.key} outcome={c.pass ? "passed" : "failed"} title={text.label}
                            result={<>{text.value}<span className="muted"> · required {text.target}</span></>}
                            action={at !== null && recording ? <button className="small ghost" onClick={() => seek(at)} title="Move every view to this moment">t = {at.toFixed(2)} s</button> : null}>
                    {(text.margin || c.detail) && <span>{text.margin ? `margin ${text.margin}` : ""}{text.margin && c.detail ? " · " : ""}{c.detail}</span>}
                  </CheckRow>
                );
              })}
              {!checks.length && <span className="muted">This scenario has no expectations.</span>}
            </div>
          </Card>
          <Card title="Measured"><Metrics metrics={last?.metrics ?? {}} /></Card>
          {last?.clip_url && <Card title="Rendered clip" subtitle="MuJoCo render, follows the playhead"><ClipPlayer url={last.clip_url} playback={playback} duration={end} /></Card>}
        </div>
      </div>
      {(last?.renders?.length || last?.plots?.length) ? (
        <Details summary={`Renders and plots (${(last?.renders?.length ?? 0) + (last?.plots?.length ?? 0)})`}>
          <Gallery images={[...(last?.renders ?? []), ...(last?.plots ?? [])]} />
        </Details>
      ) : null}
    </div>
  );
}

function scenarioStatus(s: ScenarioListItem): { tone: string; text: string } {
  const last = s.last;
  if (!last) return { tone: "", text: "not run" };
  const verdict = last.passed === false ? "failed" : last.passed ? "passed" : "no verdict";
  return { tone: last.passed === false ? "critical" : last.stale ? "warning" : "good", text: last.stale ? `${verdict} · outdated` : verdict };
}

export function SimulationPage() {
  const { pid = "" } = useParams();
  const scenarios = useScenarios(pid);
  const [params, setParams] = useSearchParams();
  const { start, active } = useProjectRuns(pid);
  const [error, setError] = useState<unknown>(null);
  const list = scenarios.data?.scenarios ?? [];
  const firstRun = list.find((s) => s.last?.recording)?.id ?? list.find((s) => s.last)?.id ?? list[0]?.id ?? null;
  const selected = params.get("scenario") ?? firstRun;
  const selectedItem = list.find((s) => s.id === selected);
  const scenario = useScenario(pid, selected && selectedItem?.last ? selected : null);
  const run = (id: string) => { setError(null); start({ stages: ["sim"], scenario: id }).catch(setError); };
  const ranAt = scenarios.data?.result?.started_at;
  return (
    <div className="page">
      <QueryView query={scenarios} height={60}>
        {({ scenarios: items }) => (
          <div className="scenario-bar" role="radiogroup" aria-label="Scenario">
            {items.map((s) => {
              const st = scenarioStatus(s);
              return (
                <button key={s.id} role="radio" aria-checked={s.id === selected} className={`scenario-chip${s.id === selected ? " on" : ""}`}
                        onClick={() => setParams({ scenario: s.id }, { replace: true })} data-testid="scenario-card">
                  <span className={`dot-mark ${st.tone}`} />
                  <span className="name">{partLabel(s.id)}</span>
                  <span className="muted small">{st.text}</span>
                </button>
              );
            })}
            {ranAt ? <span className="muted small" style={{ alignSelf: "center" }}>simulation stage ran {timeAgo(ranAt)}</span> : null}
          </div>
        )}
      </QueryView>
      {error ? <ErrorBox error={error} /> : null}
      {selected && scenarios.data && !selectedItem?.last ? (
        <Card><Empty title={`${partLabel(selected)} has not been run`}>
          {selectedItem?.description && <p>{selectedItem.description}</p>}
          <button className="primary" onClick={() => run(selected)} disabled={!!active}><Icon name="play" size={12} />Run {partLabel(selected)}</button>
        </Empty></Card>
      ) : selected && selectedItem ? (
        <QueryView query={scenario} height={480}>{(response) => <ScenarioDetail key={selected} pid={pid} response={response} item={selectedItem} onRun={() => run(selected)} busy={!!active} />}</QueryView>
      ) : null}
    </div>
  );
}
