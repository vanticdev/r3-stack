import { Fragment, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useElectrical, useModel, useProject, useSpec } from "../api/hooks.ts";
import type { CurrentRow, ElectricalResponse, ElectricalTableRow, ModelGraph, OperatingPoint, RuntimeInfo } from "../api/types.ts";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { TimeChart } from "../components/TimeChart.tsx";
import { Card, CheckRow, Details, Gallery, QueryView, RatioBar, Segmented, StaleNotice } from "../components/ui.tsx";
import { batteryStates, drawPowerTree, pinNet, powerTree, railFlag, railLimits, rowsForState, type PowerTree } from "../lib/domain.ts";
import { BATTERY_STATE_LABEL, diagnosticHeadline, opLabel, partLabel } from "../lib/labels.ts";
import { fmt, humanize, pct, quantity } from "../lib/units.ts";
import { useProjectRuns } from "../state/runs.tsx";

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const pointName = (row: Pick<ElectricalTableRow, "operating_point">) => humanize(row.operating_point);
const stateName = (state: string, soc?: number) => `${BATTERY_STATE_LABEL[state] ?? `${state} charge`}${soc !== undefined ? ` (${pct(soc)} SOC)` : ""}`;

// ------------------------------------------------------------------ power tree

function PowerTreeDiagram({ tree, point, graph, selected, onSelect }: {
  tree: PowerTree; point: OperatingPoint | null; graph: ModelGraph | null; selected: string | null; onSelect: (id: string | null) => void;
}) {
  const drawing = useMemo(() => drawPowerTree(tree), [tree]);
  if (!tree.nodes.length) return <span className="muted">No rails in the model graph.</span>;
  const W = 112, H = 38;
  const railVoltage = (pin: string, nominal: number) => {
    const net = graph ? pinNet(graph, pin) : null;
    const v = net ? point?.nets?.[net] : undefined;
    return v !== undefined ? fmt(v, "V") : `${nominal} V nominal`;
  };
  const current = (pin: string) => { const i = point?.pin_currents?.[pin]; return i !== undefined ? fmt(Math.abs(i), "A") : ""; };
  const line = (pts: [number, number][]) => `M${pts.map(([x, y]) => `${x},${y}`).join(" L")}`;
  return (
    <div className="power-tree-wrap">
      <svg className="power-tree" viewBox={`0 0 ${drawing.width} ${drawing.height}`} width={drawing.width} height={drawing.height} role="img" aria-label="Power tree">
        <defs>
          <marker id="pt-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L8,4 L0,8 z" className="arrow" />
          </marker>
        </defs>
        {drawing.buses.map((b) => (
          <g key={b.source}>
            <path d={line(b.stem)} />
            {b.bus && <path d={line(b.bus)} />}
            {b.drops.map((d) => <path key={d.sink} d={line(d.path)} markerEnd="url(#pt-arrow)" />)}
            {b.junctions.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={3} className="junction" />)}
            <text x={b.labelAt[0]} y={b.labelAt[1]} className="rail">{railVoltage(b.source, b.nominal)}</text>
            {b.drops.map((d) => {
              const [x, y] = d.path[d.path.length - 1]!;
              const i = current(d.sink);
              return i ? <text key={`${d.sink}:i`} x={x + 6} y={y - 6} className="sub">{i}</text> : null;
            })}
          </g>
        ))}
        {drawing.backEdges.map((e) => (
          <g key={`${e.source}>${e.sink}`}>
            <path d={line(e.path)} strokeDasharray="4 3" markerEnd="url(#pt-arrow)" />
            <text x={e.labelAt[0]} y={e.labelAt[1]} className="rail">{railVoltage(e.source, e.nominal)}</text>
          </g>
        ))}
        {drawing.nodes.map((n) => {
          const powered = num(point?.components?.[n.id]?.powered as unknown);
          const off = powered === 0 || point?.components?.[n.id]?.powered === false;
          return (
            <g key={n.id} className={`node${selected === n.id ? " selected" : ""}${off ? " off" : ""}`} onClick={() => onSelect(selected === n.id ? null : n.id)} role="button" aria-label={partLabel(n.id)}>
              <rect x={n.x} y={n.y} width={W} height={H} rx={6} />
              <text x={n.x + W / 2} y={n.y + H / 2 + 4} textAnchor="middle" fontWeight={600}>{partLabel(n.id)}{off ? " · off" : ""}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function NodeDetail({ id, graph, point, tree }: { id: string; graph: ModelGraph | null; point: OperatingPoint | null; tree: PowerTree }) {
  const comp = graph?.components[id];
  const state = point?.components?.[id] ?? {};
  const supplies = tree.edges.filter((e) => e.to === id);
  const feeds = tree.edges.filter((e) => e.from === id);
  const values = Object.entries(state).filter(([, v]) => typeof v === "number" || typeof v === "boolean").slice(0, 8);
  return (
    <div className="stack small node-detail">
      <strong>{partLabel(id)}</strong>
      {comp && <span className="muted">{comp.name}</span>}
      <dl className="kv">
        {supplies.map((e) => <Fragment key={e.sink}><dt>Supplied at {e.sink}</dt><dd>{(() => { const n = graph ? pinNet(graph, e.sink) : null; const v = n ? point?.nets?.[n] : undefined; return v !== undefined ? fmt(v, "V") : "—"; })()}{point?.pin_currents?.[e.sink] !== undefined ? ` · ${fmt(Math.abs(point.pin_currents[e.sink]!), "A")}` : ""}</dd></Fragment>)}
        {feeds.length > 0 && <><dt>Feeds</dt><dd>{[...new Set(feeds.map((e) => partLabel(e.to)))].join(", ")}</dd></>}
        {values.map(([k, v]) => <Fragment key={k}><dt>{humanize(k)}</dt><dd>{typeof v === "boolean" ? (v ? "yes" : "no") : fmt(v as number)}</dd></Fragment>)}
      </dl>
    </div>
  );
}

// ------------------------------------------------------------------ worst-case tables

/** "distribution.P1|supply.B#1" → "distribution.P1 ↔ supply.B · pin 1". */
const contactLabel = (id: string) => id.replace(/#(\w+)$/, " · pin $1").split("|").join(" ↔ ");

function RatingTable({ rows, limit = 6, label }: { rows: CurrentRow[]; limit?: number; label: string }) {
  const [all, setAll] = useState(false);
  const sorted = useMemo(() => rows.slice().sort((a, b) => (b.continuous_ratio ?? 0) - (a.continuous_ratio ?? 0)), [rows]);
  const shown = all ? sorted : sorted.slice(0, limit);
  if (!rows.length) return <span className="muted">No worst-case data; run the electrical stage.</span>;
  return (
    <div className="stack">
      <div className="table-wrap">
        <table aria-label={label}>
          <thead><tr><th>{label}</th><th>Worst continuous vs rating</th><th className="num">Current</th><th className="num">Rating</th><th>At</th></tr></thead>
          <tbody>
            {shown.map((r) => {
              const rating = r.ampacity ?? r.rating ?? 0;
              const current = Math.abs(r.continuous_current);
              const burst = r.burst_current !== null && r.burst_current !== undefined ? Math.abs(r.burst_current) : undefined;
              return (
                <tr key={r.id}>
                  <td className="small"><span className="mono">{contactLabel(r.id)}</span>{r.harness ? <div className="muted">{partLabel(r.harness)}</div> : null}</td>
                  <td style={{ minWidth: 160 }}><RatioBar value={current} limit={rating} burst={burst} burstLimit={r.burst_rating ?? rating} /></td>
                  <td className="num">{fmt(current, "A")}{burst !== undefined && <div className="muted small" title={r.burst_point ? `burst at ${opLabel(r.burst_point)}` : undefined}>burst {fmt(burst, "A")}</div>}</td>
                  <td className="num">{fmt(rating, "A")}{r.burst_rating !== undefined && <div className="muted small">burst {fmt(r.burst_rating, "A")}</div>}</td>
                  <td className="small muted">{opLabel(r.continuous_point ?? "")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > limit && <button className="small ghost" onClick={() => setAll((a) => !a)}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>}
    </div>
  );
}

// ------------------------------------------------------------------ failures first

interface Finding { ok: boolean; title: string; detail?: string; point?: string }

function worstCaseFindings(e: ElectricalResponse, requirement: number): Finding[] {
  const out: Finding[] = [];
  const brownout = (e.power_tree.brownout as { pi?: { worst?: { point: string; voltage: number; margin: number } } } | null)?.pi?.worst;
  if (brownout) {
    const threshold = brownout.voltage - brownout.margin;
    out.push(brownout.margin < 0
      ? { ok: false, title: `Pi supply reaches ${fmt(brownout.voltage, "V")} at ${opLabel(brownout.point)}`, detail: `brownout below ${fmt(threshold, "V")}`, point: brownout.point }
      : { ok: true, title: `Pi supply stays ≥ ${fmt(brownout.voltage, "V")}`, detail: `${fmt(brownout.margin, "V")} above brownout, worst at ${opLabel(brownout.point)}`, point: brownout.point });
  }
  const esc = e.thermal?.esc as { bec_current?: number; bec_rating?: number; bec_point?: string } | undefined;
  if (esc?.bec_current !== undefined && esc.bec_rating) {
    const over = esc.bec_current - esc.bec_rating;
    out.push(over > 0
      ? { ok: false, title: `ESC BEC exceeds its ${fmt(esc.bec_rating, "A")} rating by ${fmt(over, "A")}`, detail: `${fmt(esc.bec_current, "A")} at ${opLabel(esc.bec_point ?? "")}`, point: esc.bec_point }
      : { ok: true, title: `ESC BEC load ${fmt(esc.bec_current, "A")}`, detail: `rating ${fmt(esc.bec_rating, "A")}`, point: esc.bec_point });
  }
  const battery = e.thermal?.battery as { peak_current?: number; peak_point?: string; c_rating_limit?: number } | undefined;
  if (battery?.peak_current !== undefined && battery.c_rating_limit) {
    const ok = battery.peak_current <= battery.c_rating_limit;
    out.push({ ok, title: ok ? `Peak battery current ${fmt(battery.peak_current, "A")}` : `Battery current exceeds its C rating: ${fmt(battery.peak_current, "A")}`,
               detail: `C-rating limit ${fmt(battery.c_rating_limit, "A")} · at ${opLabel(battery.peak_point ?? "")}`, point: battery.peak_point });
  }
  if (e.runtime && Number.isFinite(requirement)) {
    const ok = e.runtime.runtime >= requirement;
    out.push({ ok, title: `Runtime ${fmt(e.runtime.runtime, "s")}`, detail: `required ≥ ${fmt(requirement, "s")} at the duty-cycle mix` });
  }
  return out;
}

export function ElectricalPage() {
  const { pid = "" } = useParams();
  const electrical = useElectrical(pid);
  const model = useModel(pid);
  const spec = useSpec(pid);
  const project = useProject(pid);
  const { start, active } = useProjectRuns(pid);
  const [state, setState] = useState<string | null>(null);
  const [pointId, setPointId] = useState<string | null>(null);
  const [node, setNode] = useState<string | null>(null);
  const [openRail, setOpenRail] = useState<string | null>(null);

  return (
    <QueryView query={electrical} height={400}>
      {(e) => {
        if (!e.operating_points) return <Card title="Electrical"><span className="muted">No operating points yet; run the electrical stage.</span></Card>;
        const states = batteryStates(e.table);
        const current = state ?? states[0]?.state ?? "full";
        const rows = rowsForState(e.table, current);
        const selectedRow = rows.find((r) => r.id === pointId) ?? rows.find((r) => r.operating_point === "full_throttle") ?? rows[0];
        const selected = selectedRow ? e.operating_points.operating_points[selectedRow.id] ?? null : null;
        const graph = model.data?.graph ?? null;
        const tree = powerTree(e.power_tree.rails, graph?.components ?? null);
        const requirement = quantity(spec.data?.spec.requirements?.min_runtime ?? e.runtime?.requirement ?? NaN);
        const findings = worstCaseFindings(e, requirement);
        const failures = findings.filter((f) => !f.ok);
        const passes = findings.filter((f) => f.ok);
        const covered = new Set(["bec_overload", "brownout_risk", "pin_overcurrent", "runtime_estimate"]);
        const otherErrors = (e.result?.diagnostics ?? []).filter((d) => d.severity === "error" && !covered.has(d.code));
        const stage = project.data?.stages.find((s) => s.stage === "electrical");
        const soc = states.find((s) => s.state === current)?.soc;
        const condition = selectedRow ? `${pointName(selectedRow)} · ${stateName(current, soc)}` : stateName(current, soc);
        const show = (point?: string) => {
          if (!point) return;
          const [, st] = point.split("@");
          if (st) setState(st);
          setPointId(point);
          document.getElementById("operating-point")?.scrollIntoView({ behavior: "smooth", block: "start" });
        };
        return (
          <div className="page">
            {stage?.stale && <StaleNotice what="Electrical results" since={stage.started_at} onRerun={() => void start({ stages: ["electrical"] })} rerunLabel="Rerun electrical" busy={!!active} />}

            <section className="stack" style={{ gap: 12 }}>
              <div className="section-title"><h2>Worst cases across all battery states</h2></div>
              <Card>
                <div className="check-list" data-testid="electrical-findings">
                  {failures.map((f) => (
                    <CheckRow key={f.title} outcome="failed" title={f.title} result={f.detail}
                              action={f.point ? <button className="small" onClick={() => show(f.point)}>Show circuit</button> : null} />
                  ))}
                  {otherErrors.map((d, i) => <CheckRow key={`${d.code}${i}`} outcome="failed" title={<Markdown inline>{diagnosticHeadline(d)}</Markdown>}>{d.hint && <Markdown>{`Fix: ${d.hint}`}</Markdown>}</CheckRow>)}
                  {!failures.length && !otherErrors.length && <CheckRow outcome="passed" title="No electrical limit is exceeded at any operating point" />}
                </div>
                {passes.length > 0 && (
                  <div className="passing-row">
                    {passes.map((f) => <span key={f.title}><Icon name="check" size={12} />{f.title}{f.detail ? <span className="muted"> · {f.detail}</span> : null}</span>)}
                  </div>
                )}
              </Card>
              <div className="grid cols-2">
                <Card title="Wire current vs ampacity" subtitle="Bar: worst continuous current · dashed: burst · tick: rating" flush><RatingTable rows={e.wires} label="Wire" /></Card>
                <Card title="Connector contacts vs rating" subtitle="Worst continuous current per contact · dashed: burst vs burst rating" flush><RatingTable rows={e.connectors} label="Contact" /></Card>
              </div>
            </section>

            <section className="stack" style={{ gap: 12 }} id="operating-point">
              <div className="section-title op-head">
                <h2>Selected operating point</h2>
                <div className="row op-controls">
                  <Segmented label="Battery state" value={current} onChange={(s) => { setState(s); setPointId(null); }}
                             options={states.map((s) => ({ value: s.state, label: `${humanize(s.state)} · ${pct(s.soc)}` }))} />
                  <select value={selectedRow?.id ?? ""} onChange={(ev) => setPointId(ev.target.value)} aria-label="Operating point">
                    {rows.map((r) => <option key={r.id} value={r.id}>{pointName(r)}</option>)}
                  </select>
                </div>
              </div>
              <div className="grid cols-2">
                <Card title={`Power tree · ${condition}`} subtitle="Arrows point from supply to load; labels give the calculated rail voltage and each load's current. Click a box for details.">
                  <div className="power-tree-layout">
                    <PowerTreeDiagram tree={tree} point={selected} graph={graph} selected={node} onSelect={setNode} />
                    {node && <NodeDetail id={node} graph={graph} point={selected} tree={tree} />}
                  </div>
                </Card>
                <Card title={`Calculated rail voltages · ${stateName(current, soc)}`} subtitle="Click a rail for its pins" flush>
                  <div className="table-wrap">
                    <table data-testid="rail-table">
                      <thead><tr><th>Rail</th><th className="num">Nominal</th><th className="num">Tolerance</th>{rows.map((r) => <th key={r.id} className="num">{pointName(r)}</th>)}</tr></thead>
                      <tbody>
                        {e.power_tree.rails.map((r) => {
                          const net = graph ? pinNet(graph, r.source) : null;
                          const limits = railLimits(r);
                          const stated = limits.loStated || limits.hiStated;
                          return (
                            <Fragment key={r.source}>
                              <tr className="clickable" onClick={() => setOpenRail(openRail === r.source ? null : r.source)} aria-expanded={openRail === r.source}>
                                <td><strong>{net ?? r.source}</strong><div className="muted small">from {partLabel(r.source.split(".")[0]!)}</div></td>
                                <td className="num">{fmt(r.nominal, "V")}</td>
                                <td className="num small">{stated ? `${fmt(limits.lo, "V")} – ${fmt(limits.hi, "V")}` : <span className="muted">not specified<div>±5 % assumed</div></span>}</td>
                                {rows.map((row) => {
                                  const v = net ? row.rails?.[net] : undefined;
                                  const flag = railFlag(v, limits);
                                  return (
                                    <td key={row.id} className={`num${row.id === selectedRow?.id ? " current-col" : ""}`}>
                                      {v === undefined ? "—" : flag ? <span className="clearance critical">{fmt(v, "V")} {flag === "low" ? "▼ low" : "▲ high"}</span> : fmt(v, "V")}
                                    </td>
                                  );
                                })}
                              </tr>
                              {openRail === r.source && (
                                <tr className="detail-row"><td colSpan={3 + rows.length} className="small">
                                  {r.source} → {r.sinks.join(", ")}
                                </td></tr>
                              )}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </div>

              <Card title={`Operating points · ${stateName(current, soc)}`} subtitle="Click a row to show it in the power tree" flush>
                <div className="table-wrap">
                  <table data-testid="operating-points">
                    <thead><tr><th>Point</th><th className="num">Battery</th><th className="num">Battery current</th><th className="num">Motor current</th>
                      <th className="num">Motor torque</th><th className="num">ESC temperature</th><th className="num">Worst harness drop</th><th>Events</th></tr></thead>
                    <tbody>
                      {rows.map((row) => {
                        const p = e.operating_points!.operating_points[row.id];
                        const drop = Object.entries(p?.harness_drop ?? {}).sort((a, b) => b[1].fraction - a[1].fraction)[0];
                        return (
                          <tr key={row.id} className={`clickable${selectedRow?.id === row.id ? " selected" : ""}`} onClick={() => setPointId(row.id)}>
                            <td><strong>{pointName(row)}</strong>{row.kind === "burst" && <div className="muted small">short burst</div>}</td>
                            <td className="num">{fmt(row.battery_voltage, "V")}</td>
                            <td className="num">{fmt(row.battery_current !== null ? Math.abs(row.battery_current) : null, "A")}</td>
                            <td className="num">{fmt(row.motor_current !== null ? Math.abs(row.motor_current) : null, "A")}</td>
                            <td className="num">{fmt(num(p?.components.motor?.torque), "N·m")}</td>
                            <td className="num">{fmt(num(p?.components.esc?.temperature_c), "°C")}</td>
                            <td className="num">{drop ? `${pct(drop[1].fraction, 2)} ` : "—"}{drop && <div className="muted small">{partLabel(drop[0])}</div>}</td>
                            <td className="small">{p?.events?.length ? <strong>{p.events.length} {p.events.length === 1 ? "event" : "events"}</strong> : <span className="muted">none</span>}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            </section>

            <div className="grid cols-2">
              <Card title="Battery voltage over the discharge" subtitle={e.runtime ? `${fmt(e.runtime.capacity_ah, "")} Ah · software cutoff ${fmt(e.runtime.cutoff_voltage, "V")}` : ""}>
                {e.runtime?.curve?.length ? <RuntimeChart curve={e.runtime.curve} /> : <span className="muted">No runtime curve.</span>}
              </Card>
              <Card title="Plots"><Gallery images={e.images} /></Card>
            </div>
            <Details summary="How these values are calculated">
              <span className="small">Static operating points of the DC network with routed wire lengths, cross-checked in ngspice. Runtime integrates the duty-cycle mix over the battery's discharge curve.
                Diagnostics: <Link to={`/p/${encodeURIComponent(pid)}/diagnostics?stage=electrical`}>electrical</Link>.</span>
            </Details>
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
  return <TimeChart title="Terminal voltage" t={t} series={series} xUnit="min" height={200} syncKey="r3-runtime" />;
}
