// Inspector beside the 3D view: a searchable parts list (with an Errors filter) when nothing is selected;
// for a selected part, wire or interference, its diagnostics first, then catalog, mass and CAD data.
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { useCatalog } from "../api/hooks.ts";
import type { Diagnostic } from "../api/types.ts";
import { DiagnosticList } from "../components/DiagnosticList.tsx";
import { Details, Segmented } from "../components/ui.tsx";
import { partLabel } from "../lib/labels.ts";
import { fmt, humanize } from "../lib/units.ts";
import type { Selection } from "./RobotViewer.tsx";
import type { RobotData } from "./useRobotScene.ts";

function Kv({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  );
}

const worstFirst = (items: Diagnostic[]) => items.slice().sort((a, b) => rank(a) - rank(b));
const rank = (d: Diagnostic) => (d.severity === "error" ? 0 : d.severity === "warning" ? 1 : 2);

/** Diagnostics of a part: its own, plus those on its pins ("esc.BEC"). */
export function partDiagnostics(data: RobotData, id: string): Diagnostic[] {
  const own = data.bySubject[id] ?? [];
  const pins = Object.entries(data.bySubject).filter(([s]) => s.startsWith(`${id}.`)).flatMap(([, d]) => d);
  return worstFirst([...new Set([...own, ...pins])]);
}

function PartsList({ data, onSelect }: { data: RobotData; onSelect: (s: Selection) => void }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "errors">("all");
  const rows = useMemo(() => data.components.map((c) => {
    const diags = partDiagnostics(data, c.id);
    return { id: c.id, label: partLabel(c.id), name: c.name, errors: diags.filter((d) => d.severity === "error").length, warnings: diags.filter((d) => d.severity === "warning").length };
  }).sort((a, b) => b.errors - a.errors || b.warnings - a.warnings || a.label.localeCompare(b.label)), [data]);
  const withErrors = rows.filter((r) => r.errors > 0).length;
  const q = query.trim().toLowerCase();
  const shown = rows.filter((r) => (filter === "all" || r.errors > 0) && (!q || `${r.id} ${r.label} ${r.name}`.toLowerCase().includes(q)));
  return (
    <>
      <section className="inspector-head">
        <div className="row">
          <h2>Parts</h2><span className="muted small">{data.components.length}</span>
          <span className="spacer" style={{ flex: 1 }} />
          <Segmented label="Parts filter" value={filter} onChange={setFilter}
                     options={[{ value: "all", label: "All" }, { value: "errors", label: `Errors · ${withErrors}` }]} />
        </div>
        <input type="search" placeholder="Search parts" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search parts" />
      </section>
      <div className="list parts-list" data-testid="parts-list">
        {shown.map((r) => (
          <button key={r.id} className="list-row" onClick={() => onSelect({ kind: "component", id: r.id })}>
            <span className={`dot-mark${r.errors ? " critical" : r.warnings ? " warning" : ""}`} />
            <span className="stack" style={{ gap: 0, minWidth: 0, flex: 1 }}>
              <span>{r.label}</span>
              <span className="muted small ellipsis" title={r.name}>{r.name}</span>
            </span>
            {r.errors > 0 && <span className="small" style={{ color: "var(--critical)" }}>{r.errors} {r.errors === 1 ? "error" : "errors"}</span>}
          </button>
        ))}
        {!shown.length && <span className="muted small" style={{ padding: 12 }}>No parts match.</span>}
      </div>
    </>
  );
}

export function Inspector({ data, selection, onClear, onSelect, interference = null }: {
  data: RobotData; selection: Selection | null; onClear: () => void;
  onSelect?: (s: Selection) => void;
  /** index into data.interference of the selected interference region */
  interference?: number | null;
}) {
  const { pid = "" } = useParams();
  const catalog = useCatalog(pid);
  const overlap = interference !== null ? data.interference[interference] : undefined;

  if (overlap) {
    const diags = worstFirst([...new Set([...partDiagnostics(data, overlap.a), ...partDiagnostics(data, overlap.b)])])
      .filter((d) => d.subjects.includes(overlap.a) || d.subjects.includes(overlap.b));
    return (
      <div className="inspector" aria-label="Inspector" data-testid="inspector">
        <section>
          <div className="row"><h2>Interference</h2><span className="spacer" style={{ flex: 1 }} /><button className="small ghost" onClick={onClear}>Close</button></div>
          <span className="secondary">{partLabel(overlap.a)} overlaps {partLabel(overlap.b)}</span>
          <Kv rows={[["Overlap", overlap.extent.map((v) => (v * 1000).toFixed(1)).join(" × ") + " mm"],
                     ["Centre", overlap.center.map((v) => (v * 1000).toFixed(1)).join(", ") + " mm"]]} />
          <div className="row small">
            {onSelect && <button className="small" onClick={() => onSelect({ kind: "component", id: overlap.a })}>Inspect {partLabel(overlap.a)}</button>}
            {onSelect && <button className="small" onClick={() => onSelect({ kind: "component", id: overlap.b })}>Inspect {partLabel(overlap.b)}</button>}
          </div>
        </section>
        <section><h3>Diagnostics</h3><DiagnosticList items={diags} empty="No diagnostics name these parts." /></section>
      </div>
    );
  }

  if (!selection) {
    return (
      <div className="inspector" aria-label="Inspector">
        {onSelect ? <PartsList data={data} onSelect={onSelect} /> : <section><span className="muted">Click a part or wire to inspect it.</span></section>}
      </div>
    );
  }

  if (selection.kind === "wire") {
    const w = data.wires.find((x) => x.id === selection.id);
    const route = data.routes?.wires[selection.id];
    return (
      <div className="inspector" aria-label="Inspector" data-testid="inspector">
        <section>
          <div className="row"><h2>{selection.id}</h2><span className="spacer" style={{ flex: 1 }} /><button className="small ghost" onClick={onClear}>Close</button></div>
          {w && <span className="secondary">{partLabel(w.harness)} · {w.signal || "wire"}</span>}
        </section>
        <section><h3>Diagnostics</h3><DiagnosticList items={worstFirst(data.bySubject[selection.id] ?? [])} /></section>
        <section>
          {w ? (
            <Kv rows={[
              ["Harness", partLabel(w.harness)], ["Gauge", `${w.awg} AWG`],
              ["Colour", <span key="c"><span className="swatch" style={{ background: `#${w.color.toString(16).padStart(6, "0")}` }} /> {w.colorName}</span>],
              ["Routed length", fmt(route?.routed_length, "m")], ["Cut length", fmt(route?.cut_length, "m")],
              ["Outer diameter", fmt(w.radius * 2, "m")], ["Path", w.routed ? `${w.path.length} points` : "straight estimate (not routed)"],
            ]} />
          ) : <span className="muted">Unknown wire.</span>}
        </section>
      </div>
    );
  }

  const c = data.components.find((x) => x.id === selection.id);
  const part = data.cadParts[selection.id];
  const entry = c ? catalog.data?.components.find((x) => x.id === c.type) : undefined;
  const source = data.massSource[selection.id] ?? part?.mass_status ?? "catalog";
  const diags = partDiagnostics(data, selection.id);
  return (
    <div className="inspector" aria-label="Inspector" data-testid="inspector">
      <section>
        <div className="row"><h2>{partLabel(selection.id)}</h2><span className="spacer" style={{ flex: 1 }} /><button className="small ghost" onClick={onClear}>Close</button></div>
        {c && <span className="secondary">{c.name}</span>}
      </section>
      <section>
        <h3>Diagnostics{diags.length ? ` · ${diags.length}` : ""}</h3>
        <DiagnosticList items={diags} empty="No diagnostics for this part." />
      </section>
      <section>
        <h3>Mass</h3>
        <Kv rows={[
          ["Mass", fmt(part?.mass ?? c?.mass, "kg")],
          ["Source", source.startsWith("cad") ? "CAD geometry" : source === "catalog" ? "catalog" : humanize(source)],
          ["Material", part?.material],
          ["Density", part?.density ? `${part.density.toFixed(0)} kg/m³` : null],
          ["Catalog mass", entry?.mass !== undefined && entry?.mass !== null ? fmt(entry.mass, "kg") : null],
        ]} />
      </section>
      <section>
        <h3>Catalog</h3>
        {catalog.isPending ? <div className="skeleton" style={{ height: 60 }} /> : entry ? (
          <>
            <Kv rows={[
              ["Name", entry.name], ["Part number", entry.part_number || null],
              ["Envelope", entry.size ? `${entry.shape ?? ""} ${entry.size.map((v) => (v * 1000).toFixed(1)).join(" × ")} mm` : null],
              ["Data source", entry.provenance ?? null],
            ]} />
            {Object.keys(entry.connectors).length > 0 && (
              <div className="small">
                <strong>Connectors</strong>
                {Object.entries(entry.connectors).map(([id, type]) => (
                  <div key={id} className="row" style={{ justifyContent: "space-between" }}><span>{id}</span><span className="muted">{type}</span></div>
                ))}
              </div>
            )}
          </>
        ) : <span className="muted small">No catalog entry for {c?.type}.</span>}
      </section>
      {c && (
        <section>
          <Details rows={[
            ["Id", selection.id], ["Catalog type", c.type], ["Mount", c.mount],
            ["Position (mm)", c.xyz.map((v) => (v * 1000).toFixed(1)).join(", ")],
            ["Behavior model", entry?.behavior ?? null],
            ["CAD mesh", part?.mesh ? `${part.mesh} (${part.mesh_triangles ?? "?"} triangles)` : "none (no pulled mesh)"],
          ]} />
          {entry && Object.keys(entry.pins).length > 0 && (
            <Details summary={`Pins (${Object.keys(entry.pins).length})`}>
              <div className="small">
                {Object.entries(entry.pins).map(([id, role]) => (
                  <div key={id} className="row" style={{ justifyContent: "space-between" }}><code>{id}</code><span className="muted">{humanize(role)}</span></div>
                ))}
              </div>
            </Details>
          )}
        </section>
      )}
    </div>
  );
}
