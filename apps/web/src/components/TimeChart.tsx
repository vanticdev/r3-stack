// uPlot time-series chart: one y-axis, thin 2px lines, a crosshair tooltip, a playhead at `cursorT`,
// and click/drag to scrub. Charts sharing `syncKey` move their crosshairs together.
import { useEffect, useMemo, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { cssVar, useThemeVersion } from "../state/prefs.ts";
import { fmt } from "../lib/units.ts";

export interface ChartSeries {
  label: string;
  values: readonly (number | null)[];
  /** CSS colour or a var(--series-n) reference. */
  color: string;
  unit?: string;
  dash?: boolean;
}

function resolveColor(color: string): string {
  const match = color.match(/^var\((--[\w-]+)\)$/);
  return match ? cssVar(match[1]!) || "#888" : color;
}

/** Valid finite range of a series list, padded; null when nothing is plottable. */
export function seriesRange(series: readonly ChartSeries[], limits: readonly number[] = []): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const s of series) for (const v of s.values) if (typeof v === "number" && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  for (const l of limits) if (Number.isFinite(l)) { lo = Math.min(lo, l); hi = Math.max(hi, l); }
  if (!Number.isFinite(lo)) return null;
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1;
  return [lo - pad, hi + pad];
}

/** Tick labels with just enough decimals for the tick spacing (no duplicate labels, no "-0"). */
export function tickLabels(values: readonly number[]): string[] {
  const step = values.length > 1 ? Math.abs(values[1]! - values[0]!) : Math.abs(values[0] ?? 1);
  const decimals = step > 0 ? Math.min(8, Math.max(0, -Math.floor(Math.log10(step) + 1e-9))) : 0;
  return values.map((v) => {
    const text = v.toFixed(decimals);
    return Number(text) === 0 ? "0" : text;
  });
}

export function TimeChart({ title, t, series, cursorT, onScrub, height = 150, syncKey = "r3-sim", limit, unit, xUnit = "s" }: {
  title: string;
  t: readonly number[];
  series: ChartSeries[];
  cursorT?: number;
  onScrub?: (t: number) => void;
  height?: number;
  syncKey?: string;
  /** Horizontal requirement line (e.g. min clearance). */
  limit?: { value: number; label: string };
  unit?: string;
  /** Unit of the x values (tick labels and tooltip). */
  xUnit?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const cursorRef = useRef(cursorT);
  const scrubRef = useRef(onScrub);
  const tooltip = useRef<HTMLDivElement>(null);
  cursorRef.current = cursorT;
  scrubRef.current = onScrub;
  const theme = useThemeVersion();
  const range = useMemo(() => seriesRange(series, limit ? [limit.value] : []), [series, limit]);
  const current = useMemo(() => {
    if (cursorT === undefined || !t.length || !series[0]) return null;
    let i = 0;
    while (i < t.length - 1 && t[i + 1]! <= cursorT) i++;
    return series.map((s) => s.values[i] ?? null);
  }, [cursorT, t, series]);

  useEffect(() => {
    const el = host.current;
    if (!el || !t.length || !range) return;
    const text = cssVar("--text-muted");
    const grid = cssVar("--line");
    const playhead = cssVar("--accent");
    const limitColor = cssVar("--critical");
    const data: uPlot.AlignedData = [Float64Array.from(t), ...series.map((s) => Float64Array.from(s.values, (v) => (typeof v === "number" && Number.isFinite(v) ? v : NaN)))];
    let dragging = false;
    const scrubAt = (u: uPlot, clientX: number) => {
      const rect = u.over.getBoundingClientRect();
      const value = u.posToVal(Math.min(Math.max(clientX - rect.left, 0), rect.width), "x");
      scrubRef.current?.(value);
    };
    const opts: uPlot.Options = {
      width: el.clientWidth || 400,
      height,
      padding: [6, 8, 0, 0],
      cursor: { sync: { key: syncKey }, points: { size: 7 }, drag: { x: false, y: false } },
      legend: { show: false },
      scales: { x: { time: false }, y: { range: () => range } },
      axes: [
        { stroke: text, grid: { stroke: grid, width: 1 }, ticks: { show: false }, size: 26, font: "11px system-ui", values: (_u, v) => v.map((x) => `${x} ${xUnit}`) },
        { stroke: text, grid: { stroke: grid, width: 1 }, ticks: { show: false }, size: 58, font: "11px system-ui", values: (_u, v) => tickLabels(v) },
      ],
      series: [{}, ...series.map((s) => ({ label: s.label, stroke: resolveColor(s.color), width: 2, dash: s.dash ? [5, 4] : undefined, points: { show: false } }))],
      hooks: {
        draw: [(u) => {
          const ctx = u.ctx;
          ctx.save();
          ctx.setLineDash([]); // dashed series styles must not leak onto the overlays
          if (limit) {
            const y = u.valToPos(limit.value, "y", true);
            ctx.strokeStyle = limitColor;
            ctx.setLineDash([4, 4]);
            ctx.lineWidth = 1.5 * devicePixelRatio;
            ctx.beginPath(); ctx.moveTo(u.bbox.left, y); ctx.lineTo(u.bbox.left + u.bbox.width, y); ctx.stroke();
            ctx.setLineDash([]);
          }
          const tc = cursorRef.current;
          if (tc !== undefined) {
            const x = u.valToPos(tc, "x", true);
            ctx.strokeStyle = playhead;
            ctx.lineWidth = 2 * devicePixelRatio;
            ctx.beginPath(); ctx.moveTo(x, u.bbox.top); ctx.lineTo(x, u.bbox.top + u.bbox.height); ctx.stroke();
          }
          ctx.restore();
        }],
        setCursor: [(u) => {
          const tip = tooltip.current;
          const idx = u.cursor.idx;
          if (!tip) return;
          if (idx === null || idx === undefined || u.cursor.left === undefined || u.cursor.left < 0) { tip.style.display = "none"; return; }
          tip.style.display = "block";
          tip.style.left = `${Math.min(u.cursor.left + 60, u.over.clientWidth - 40)}px`;
          tip.style.top = `${(u.cursor.top ?? 0) + 4}px`;
          tip.textContent = `${(u.data[0][idx] ?? 0).toFixed(2)} ${xUnit} · ${series.map((s, k) => `${s.label} ${fmt(u.data[k + 1]?.[idx] as number, s.unit ?? unit ?? "")}`).join(" · ")}`;
        }],
        ready: [(u) => {
          u.over.addEventListener("mousedown", (e) => { dragging = true; scrubAt(u, e.clientX); });
          u.over.addEventListener("mousemove", (e) => { if (dragging) scrubAt(u, e.clientX); });
          window.addEventListener("mouseup", () => (dragging = false));
        }],
      },
    };
    const instance = new uPlot(opts, data, el);
    plot.current = instance;
    const observer = new ResizeObserver(() => instance.setSize({ width: el.clientWidth, height }));
    observer.observe(el);
    return () => { observer.disconnect(); instance.destroy(); plot.current = null; };
  }, [t, series, height, syncKey, range, limit, theme, unit, xUnit]);

  useEffect(() => { plot.current?.redraw(false, false); }, [cursorT]);

  const plottable = !!range && t.length > 1;
  return (
    <div className="chart" data-testid={`chart-${title}`}>
      <div className="chart-title">
        <strong>{title}</strong>
        {series.length > 1 && (
          <span className="legend">{series.map((s) => <span key={s.label}><i style={{ background: resolveColor(s.color) }} />{s.label}</span>)}</span>
        )}
        {current && <span className="value">{series.map((s, i) => fmt(current[i], s.unit ?? unit ?? "")).join(" / ")}</span>}
      </div>
      {plottable ? (
        <div ref={host} style={{ position: "relative", height }}>
          <div ref={tooltip} className="chart-tooltip" style={{ display: "none" }} />
        </div>
      ) : (
        <div className="empty" style={{ height, padding: 0 }}>Not recorded in this scenario</div>
      )}
    </div>
  );
}
