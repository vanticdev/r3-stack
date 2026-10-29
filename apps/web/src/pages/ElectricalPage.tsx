import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { useElectrical, useModel, useSpec } from "../api/hooks.ts";
import type { CurrentRating, ElectricalResponse, ModelGraph, OperatingPoint } from "../api/types.ts";
import { TimeChart } from "../components/TimeChart.tsx";
import { Card, Chip, Gallery, Kpi, QueryView, RatioBar, Segmented } from "../components/ui.tsx";
import { layoutTree, pinNet, pointsForState } from "../lib/domain.ts";
import { fmt, humanize, pct, quantity } from "../lib/units.ts";

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function PowerTreeDiagram({ tree, point, graph }: { tree: ElectricalResponse["power_tree"]; point: OperatingPoint | null; graph: ModelGraph | null }) {
  const layout = useMemo(() => layoutTree(tree), [tree]);
  // Top-down layers: sources on top, each rail's sinks one layer below.
  const W = 150, H = 44, GX = 18, GY = 62;
  const depthMax = Math.max(0, ...Object.values(layout).map((l) => l.depth));
  const perDepth = Array.from({ length: depthMax + 1 }, (_, d) => Object.values(layout).filter((l) => l.depth === d).length);
  const widest = Math.max(1, ...perDepth);
  const width = 20 + widest * (W + GX) - GX;
  const pos = (id: string) => {
    const l = layout[id]!;
    const offset = ((widest - (perDepth[l.depth] ?? 1)) * (W + GX)) / 2;
    return { x: 10 + offset + l.row * (W + GX), y: 10 + l.depth * (H + GY) };
  };
  const height = 20 + (depthMax + 1) * (H + GY) - GY;
  return (
    <div style={{ overflowX: "auto" }}>
      <svg className="power-tree" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label="Power tree" style={{ display: "block", margin: "0 auto" }}>
        {tree.edges.map((e, i) => {
          const a = pos(e.from), b = pos(e.to);
          const back = layout[e.to]!.depth <= layout[e.from]!.depth;
          const x1 = a.x + W / 2, y1 = back ? a.y : a.y + H, x2 = b.x + W / 2, y2 = back ? b.y + H : b.y;
          const sink = e.rail.split(" → ")[1] ?? "";
          const current = point?.pin_currents?.[sink];
          const net = graph ? pinNet(graph, sink) : null;
          const voltage = net ? point?.nets?.[net] : undefined;
          const label = `${voltage !== undefined ? fmt(voltage, "V") : `${e.nominal ?? "?"} V`}${current !== undefined ? ` · ${fmt(Math.abs(current), "A")}` : ""}`;
          return (
            <g key={i}>
              <path d={`M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`} strokeDasharray={back ? "4 4" : undefined} />
              {!back && <text x={x2} y={y2 - 6} textAnchor="middle" className="sub">{label}</text>}
            </g>
          );
        })}
        {tree.nodes.map((n) => {
          const p = pos(n.id);
          const powered = n.id === "pi" ? num(point?.components?.pi?.powered as unknown) : null;
          return (
            <g key={n.id}>
              <rect x={p.x} y={p.y} width={W} height={H} rx={6} />
              <text x={p.x + 10} y={p.y + 18} fontWeight={600}>{n.id}{powered === 0 ? " · OFF" : ""}</text>
              <text x={p.x + 10} y={p.y + 33} className="sub">{n.label.length > 23 ? `${n.label.slice(0, 22)}…` : n.label}</text>
            </g>
          );
        })}
      </svg>
      <p className="small muted" style={{ textAlign: "center" }}>Labels: sink voltage · sink current. Dashed: supply back up the tree (e.g. Pi 3V3 feeding the HAT).</p>
    </div>
  );
}

function RatingTable({ rows, limit = 12, label }: { rows: CurrentRating[]; limit?: number; label: string }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, limit);
  return (
    <div className="stack">
      <table aria-label={label}>
        <thead><tr><th>{label}</th><th>Worst continuous vs rating</th><th className="num">Current</th><th className="num">Rating</th><th>Point</th></tr></thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.id}>
              <td className="mono small">{r.id}{r.harness ? <span className="muted"> · {r.harness}</span> : null}</td>
              <td style={{ minWidth: 180 }}><RatioBar value={r.current} limit={r.rating} burst={r.burst_current} burstLimit={r.burst_rating ?? r.rating} /></td>
              <td className="num">{fmt(r.current, "A")}{r.burst_current !== undefined && <div className="muted small">burst {fmt(r.burst_current, "A")}</div>}</td>
              <td className="num">{fmt(r.rating, "A")}{r.burst_rating !== undefined && <div className="muted small">burst {fmt(r.burst_rating, "A")}</div>}</td>
              <td className="small muted">{humanize(r.point)}{r.burst_point && <div>{humanize(r.burst_point)}</div>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > limit && <button className="small ghost" onClick={() => setAll((a) => !a)}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>}
    </div>
  );
}

export function ElectricalPage() {
  const { pid = "" } = useParams();
  const electrical = useElectrical(pid);
  const model = useModel(pid);
  const spec = useSpec(pid);
  const [state, setState] = useState("full");
  const [pointId, setPointId] = useState<string | null>(null);

  return (
    <QueryView query={electrical} height={400} empty="Run the electrical stage.">
      {(e) => {
        const op = e.operating_points;
        if (!op) return <Card title="Electrical"><span className="muted">No operating points; run the electrical stage.</span></Card>;
        const states = Object.keys((op.battery_states as Record<string, number> | undefined) ?? { full: 1 });
        const points = pointsForState(op, state);
        const selected = points.find(([id]) => id === pointId)?.[1] ?? points.find(([, p]) => p.operating_point === "full_throttle")?.[1] ?? points[0]?.[1] ?? null;
        const graph = model.data?.graph ?? null;
        const runtime = op.runtime;
        const brownout = (op.brownout as { pi?: { worst?: { point: string; voltage: number; margin: number } } } | undefined)?.pi?.worst;
        const esc = op.thermal?.esc as { bec_current?: number; bec_rating?: number; bec_point?: string; temperature_c?: number; limit_c?: number } | undefined;
        const battery = op.thermal?.battery as { peak_current?: number; peak_point?: string; c_rating_limit?: number } | undefined;
        const requirement = quantity(spec.data?.spec.requirements?.min_runtime ?? runtime?.requirement ?? NaN);
        const railNets = (e.rails ?? []).map((r) => ({ ...r, net: graph ? pinNet(graph, r.source) : null }));
        return (
          <div className="page">
            <div className="page-head">
              <div className="stack" style={{ gap: 2 }}>
                <h1>Electrical</h1>
                <p>Static worst-case operating points from the DC network solve (ngspice cross-check), with routed wire lengths.</p>
              </div>
              <div className="spacer" />
              <span className="small secondary">Battery state</span>
              <Segmented label="Battery state" value={state} onChange={(s) => { setState(s); setPointId(null); }}
                         options={states.map((s) => ({ value: s, label: `${s} (${pct((op.battery_states as Record<string, number>)[s])} SOC)` }))} />
            </div>

            <div className="grid cols-4">
              <Kpi label="Runtime (duty-cycle mix)" value={fmt(runtime?.runtime, "s")} sub={`required ${fmt(requirement, "s")}`}
                   tone={runtime && Number.isFinite(requirement) ? (runtime.runtime >= requirement ? "good" : "critical") : undefined} />
              <Kpi label="Peak battery current" value={fmt(battery?.peak_current, "A")} sub={battery ? `${humanize(battery.peak_point ?? "")} · C-limit ${fmt(battery.c_rating_limit, "A")}` : ""}
                   tone={battery?.peak_current !== undefined && battery.c_rating_limit ? (battery.peak_current <= battery.c_rating_limit ? "good" : "critical") : undefined} />
              <Kpi label="Pi supply, worst case" value={fmt(brownout?.voltage, "V")} sub={brownout ? `${humanize(brownout.point)} · margin ${fmt(brownout.margin, "V")}` : ""}
                   tone={brownout ? (brownout.margin >= 0 ? "good" : "critical") : undefined} />
              <Kpi label="ESC BEC load" value={fmt(esc?.bec_current, "A")} sub={esc ? `rating ${fmt(esc.bec_rating, "A")} · ${humanize(esc.bec_point ?? "")}` : ""}
                   tone={esc?.bec_current !== undefined && esc.bec_rating ? (esc.bec_current <= esc.bec_rating ? "good" : "critical") : undefined} />
            </div>

            <div className="grid cols-2">
              <Card title="Power tree" subtitle={selected ? `Rail voltage and sink current at ${humanize(selected.operating_point)} @ ${selected.battery_state}` : ""}>
                <PowerTreeDiagram tree={e.power_tree} point={selected} graph={graph} />
              </Card>
              <Card title="Rails" subtitle="Nominal and limits from the graph; measured at each operating point" flush>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Rail</th><th className="num">Nominal</th>{points.map(([id, p]) => <th key={id} className="num">{humanize(p.operating_point)}</th>)}</tr></thead>
                    <tbody>
                      {railNets.map((r) => (
                        <tr key={r.source}>
                          <td><span className="mono">{r.net ?? r.source}</span><div className="muted small">{r.source} → {r.sinks.length} sinks</div></td>
                          <td className="num">{fmt(r.nominal, "V")}<div className="muted small">{fmt(r.min, "V")}–{fmt(r.max, "V")}</div></td>
                          {points.map(([id, p]) => {
                            const v = r.net ? p.nets[r.net] : undefined;
                            const low = v !== undefined && v < r.min * 0.95;
                            return <td key={id} className="num">{v === undefined ? "—" : low ? <Chip tone="critical">{fmt(v, "V")}</Chip> : fmt(v, "V")}</td>;
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>

            <Card title="Operating points" subtitle="Click a row to show it in the power tree" flush>
              <div className="table-wrap">
                <table data-testid="operating-points">
                  <thead><tr><th>Point</th><th>Kind</th><th className="num">Battery V</th><th className="num">Battery I</th><th className="num">Motor I</th>
                    <th className="num">Motor torque</th><th className="num">ESC temp</th><th className="num">Worst harness drop</th><th>Events</th></tr></thead>
                  <tbody>
                    {points.map(([id, p]) => {
                      const drop = Object.entries(p.harness_drop ?? {}).sort((a, b) => b[1].fraction - a[1].fraction)[0];
                      const batteryI = p.pin_currents?.["battery.POS"];
                      return (
                        <tr key={id} className={`clickable${selected === p ? " selected" : ""}`} onClick={() => setPointId(id)}>
                          <td><strong>{humanize(p.operating_point)}</strong><div className="muted small mono">{id}</div></td>
                          <td><Chip tone={p.kind === "burst" ? "warning" : "muted"}>{p.kind}</Chip></td>
                          <td className="num">{fmt(p.nets.VBAT, "V")}</td>
                          <td className="num">{fmt(batteryI !== undefined ? Math.abs(batteryI) : null, "A")}</td>
                          <td className="num">{fmt(num(p.components.motor?.current), "A")}</td>
                          <td className="num">{fmt(num(p.components.motor?.torque), "N·m")}</td>
                          <td className="num">{fmt(num(p.components.esc?.temperature_c), "°C")}</td>
                          <td className="num">{drop ? `${pct(drop[1].fraction, 2)} ` : "—"}{drop && <span className="muted small">{drop[0]}</span>}</td>
                          <td className="small">{p.events?.length ? <Chip tone="critical">{p.events.length} event(s)</Chip> : <span className="muted">none</span>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>

            <div className="grid cols-2">
              <Card title="Wire current vs ampacity" subtitle="Bar: worst continuous current; dashed: burst; tick: rating"><RatingTable rows={e.wires} label="Wire" /></Card>
              <Card title="Connector contacts vs rating" subtitle="Peak current per contact over all operating points"><RatingTable rows={e.connectors} label="Contact" /></Card>
            </div>

            <div className="grid cols-2">
              <Card title="Runtime" subtitle={runtime ? `${fmt(runtime.capacity_ah, "")} Ah · cutoff ${fmt(runtime.cutoff_voltage, "V")}` : ""}>
                {runtime?.curve?.length ? <RuntimeChart curve={runtime.curve} /> : <span className="muted">No runtime curve.</span>}
              </Card>
              <Card title="Plots"><Gallery images={e.images ?? []} /></Card>
            </div>
          </div>
        );
      }}
    </QueryView>
  );
}

function RuntimeChart({ curve }: { curve: NonNullable<NonNullable<ElectricalResponse["operating_points"]>["runtime"]>["curve"] & object }) {
  const t = useMemo(() => curve.map((c) => c.time / 60), [curve]);
  const series = useMemo(() => [
    { label: "cruise", values: curve.map((c) => c.cruise_voltage), color: "var(--series-1)", unit: "V" },
    { label: "full throttle", values: curve.map((c) => c.full_throttle_voltage), color: "var(--series-2)", unit: "V" },
  ], [curve]);
  return <TimeChart title="Battery terminal voltage over the discharge" t={t} series={series} xUnit="min" height={200} syncKey="r3-runtime" />;
}
