import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { useElectrical, useModel, useSpec } from "../api/hooks.ts";
import type { CurrentRow, ElectricalTableRow, ModelGraph, OperatingPoint, RuntimeInfo } from "../api/types.ts";
import { TimeChart } from "../components/TimeChart.tsx";
import { Card, Chip, Gallery, Kpi, QueryView, RatioBar, Segmented } from "../components/ui.tsx";
import { batteryStates, layoutTree, pinNet, powerTree, rowsForState, type PowerTree } from "../lib/domain.ts";
import { fmt, humanize, pct, quantity } from "../lib/units.ts";

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function PowerTreeDiagram({ tree, point, graph }: { tree: PowerTree; point: OperatingPoint | null; graph: ModelGraph | null }) {
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
  if (!tree.nodes.length) return <span className="muted">No rails in the model graph.</span>;
  return (
    <div style={{ overflowX: "auto" }}>
      <svg className="power-tree" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label="Power tree" style={{ display: "block", margin: "0 auto" }}>
        {tree.edges.map((e, i) => {
          const a = pos(e.from), b = pos(e.to);
          const back = layout[e.to]!.depth <= layout[e.from]!.depth;
          const x1 = a.x + W / 2, y1 = back ? a.y : a.y + H, x2 = b.x + W / 2, y2 = back ? b.y + H : b.y;
          const current = point?.pin_currents?.[e.sink];
          const net = graph ? pinNet(graph, e.sink) : null;
          const voltage = net ? point?.nets?.[net] : undefined;
          const label = `${voltage !== undefined ? fmt(voltage, "V") : `${e.nominal} V`}${current !== undefined ? ` · ${fmt(Math.abs(current), "A")}` : ""}`;
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
              <text x={p.x + 10} y={p.y + 33} className="node-sub">{n.label.length > 23 ? `${n.label.slice(0, 22)}…` : n.label}</text>
            </g>
          );
        })}
      </svg>
      <p className="small muted" style={{ textAlign: "center" }}>Labels: sink voltage · sink current. Dashed: supply back up the tree (e.g. Pi 3V3 feeding the HAT).</p>
    </div>
  );
}

function RatingTable({ rows, limit = 12, label }: { rows: CurrentRow[]; limit?: number; label: string }) {
  const [all, setAll] = useState(false);
  const sorted = useMemo(() => rows.slice().sort((a, b) => (b.continuous_ratio ?? 0) - (a.continuous_ratio ?? 0)), [rows]);
  const shown = all ? sorted : sorted.slice(0, limit);
  if (!rows.length) return <span className="muted">No worst-case data; run the electrical stage.</span>;
  return (
    <div className="stack">
      <table aria-label={label}>
        <thead><tr><th>{label}</th><th>Worst continuous vs rating</th><th className="num">Current</th><th className="num">Rating</th><th>Point</th></tr></thead>
        <tbody>
          {shown.map((r) => {
            const rating = r.ampacity ?? r.rating ?? 0;
            const current = Math.abs(r.continuous_current);
            return (
              <tr key={r.id}>
                <td className="mono small">{r.id}{r.harness ? <span className="muted"> · {r.harness}</span> : null}</td>
                <td style={{ minWidth: 180 }}>
                  <RatioBar value={current} limit={rating} burst={r.burst_current !== null && r.burst_current !== undefined ? Math.abs(r.burst_current) : undefined}
                            burstLimit={r.burst_rating ?? rating} />
                </td>
                <td className="num">{fmt(current, "A")}{r.burst_current !== null && r.burst_current !== undefined && <div className="muted small">burst {fmt(Math.abs(r.burst_current), "A")}</div>}</td>
                <td className="num">{fmt(rating, "A")}{r.burst_rating !== undefined && <div className="muted small">burst {fmt(r.burst_rating, "A")}</div>}
                  {r.burst_ratio !== null && r.burst_ratio !== undefined && r.ampacity !== undefined && <div className="muted small">burst ×{r.burst_ratio.toFixed(2)}</div>}</td>
                <td className="small muted">{humanize(r.continuous_point ?? "")}{r.burst_point && <div>{humanize(r.burst_point)}</div>}</td>
              </tr>
            );
          })}
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
  const [state, setState] = useState<string | null>(null);
  const [pointId, setPointId] = useState<string | null>(null);

  return (
    <QueryView query={electrical} height={400}>
      {(e) => {
        if (!e.operating_points) return <Card title="Electrical"><span className="muted">No operating points yet; run the electrical stage.</span></Card>;
        const states = batteryStates(e.table);
        const current = state ?? states[0]?.state ?? "full";
        const rows = rowsForState(e.table, current);
        const selectedRow: ElectricalTableRow | undefined = rows.find((r) => r.id === pointId) ?? rows.find((r) => r.operating_point === "full_throttle") ?? rows[0];
        const selected = selectedRow ? e.operating_points.operating_points[selectedRow.id] ?? null : null;
        const graph = model.data?.graph ?? null;
        const tree = powerTree(e.power_tree.rails, graph?.components ?? null);
        const runtime = e.runtime;
        const brownout = (e.power_tree.brownout as { pi?: { worst?: { point: string; voltage: number; margin: number } } } | null)?.pi?.worst;
        const esc = e.thermal?.esc as { bec_current?: number; bec_rating?: number; bec_point?: string } | undefined;
        const battery = e.thermal?.battery as { peak_current?: number; peak_point?: string; c_rating_limit?: number } | undefined;
        const requirement = quantity(spec.data?.spec.requirements?.min_runtime ?? runtime?.requirement ?? NaN);
        const railNets = e.power_tree.rails.map((r) => ({ ...r, net: graph ? pinNet(graph, r.source) : null }));
        return (
          <div className="page">
            <div className="page-head">
              <div className="stack" style={{ gap: 2 }}>
                <h1>Electrical</h1>
                <p>Static worst-case operating points of the DC network (ngspice cross-check), using routed wire lengths.</p>
              </div>
              <div className="spacer" />
              <span className="small secondary">Battery state</span>
              <Segmented label="Battery state" value={current} onChange={(s) => { setState(s); setPointId(null); }}
                         options={states.map((s) => ({ value: s.state, label: `${s.state} (${pct(s.soc)} SOC)` }))} />
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
              <Card title="Power tree" subtitle={selectedRow ? `Rail voltage and sink current at ${humanize(selectedRow.operating_point)} @ ${selectedRow.battery_state}` : ""}>
                <PowerTreeDiagram tree={tree} point={selected} graph={graph} />
              </Card>
              <Card title="Rails" subtitle="Nominal and limits from the graph; measured at each operating point" flush>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Rail</th><th className="num">Nominal</th>{rows.map((r) => <th key={r.id} className="num">{humanize(r.operating_point)}</th>)}</tr></thead>
                    <tbody>
                      {railNets.map((r) => (
                        <tr key={r.source}>
                          <td><span className="mono">{r.net ?? r.source}</span><div className="muted small">{r.source} → {r.sinks.length} sinks</div></td>
                          <td className="num">{fmt(r.nominal, "V")}<div className="muted small">{fmt(r.min, "V")}–{fmt(r.max, "V")}</div></td>
                          {rows.map((row) => {
                            const v = r.net ? row.rails?.[r.net] : undefined;
                            const low = v !== undefined && v < r.min * 0.95;
                            return <td key={row.id} className="num">{v === undefined ? "—" : low ? <Chip tone="critical">{fmt(v, "V")}</Chip> : fmt(v, "V")}</td>;
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
                    {rows.map((row) => {
                      const p = e.operating_points!.operating_points[row.id];
                      const drop = Object.entries(p?.harness_drop ?? {}).sort((a, b) => b[1].fraction - a[1].fraction)[0];
                      return (
                        <tr key={row.id} className={`clickable${selectedRow?.id === row.id ? " selected" : ""}`} onClick={() => setPointId(row.id)}>
                          <td><strong>{humanize(row.operating_point)}</strong><div className="muted small mono">{row.id}</div></td>
                          <td><Chip tone={row.kind === "burst" ? "warning" : "muted"}>{row.kind}</Chip></td>
                          <td className="num">{fmt(row.battery_voltage, "V")}</td>
                          <td className="num">{fmt(row.battery_current !== null ? Math.abs(row.battery_current) : null, "A")}</td>
                          <td className="num">{fmt(row.motor_current !== null ? Math.abs(row.motor_current) : null, "A")}</td>
                          <td className="num">{fmt(num(p?.components.motor?.torque), "N·m")}</td>
                          <td className="num">{fmt(num(p?.components.esc?.temperature_c), "°C")}</td>
                          <td className="num">{drop ? `${pct(drop[1].fraction, 2)} ` : "—"}{drop && <span className="muted small">{drop[0]}</span>}</td>
                          <td className="small">{p?.events?.length ? <Chip tone="critical">{p.events.length} event(s)</Chip> : <span className="muted">none</span>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>

            <div className="grid cols-2">
              <Card title="Wire current vs ampacity" subtitle="Bar: worst continuous current; dashed: burst; tick: rating"><RatingTable rows={e.wires} label="Wire" /></Card>
              <Card title="Connector contacts vs rating" subtitle="Worst continuous current per contact; dashed: burst vs burst rating"><RatingTable rows={e.connectors} label="Contact" /></Card>
            </div>

            <div className="grid cols-2">
              <Card title="Runtime" subtitle={runtime ? `${fmt(runtime.capacity_ah, "")} Ah · software cutoff ${fmt(runtime.cutoff_voltage, "V")}` : ""}>
                {runtime?.curve?.length ? <RuntimeChart curve={runtime.curve} /> : <span className="muted">No runtime curve.</span>}
              </Card>
              <Card title="Plots"><Gallery images={e.images} /></Card>
            </div>
          </div>
        );
      }}
    </QueryView>
  );
}

function RuntimeChart({ curve }: { curve: NonNullable<RuntimeInfo["curve"]> }) {
  const t = useMemo(() => curve.map((c) => c.time / 60), [curve]);
  const series = useMemo(() => [
    { label: "cruise", values: curve.map((c) => c.cruise_voltage), color: "var(--series-1)", unit: "V" },
    { label: "full throttle", values: curve.map((c) => c.full_throttle_voltage), color: "var(--series-2)", unit: "V" },
  ], [curve]);
  return <TimeChart title="Battery terminal voltage over the discharge" t={t} series={series} xUnit="min" height={200} syncKey="r3-runtime" />;
}
