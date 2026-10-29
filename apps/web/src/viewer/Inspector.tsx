// Inspector for a clicked part or wire: catalog data, mass source, CAD properties and diagnostics.
import { useParams } from "react-router-dom";

import { useCatalog } from "../api/hooks.ts";
import { DiagnosticList } from "../components/DiagnosticList.tsx";
import { Chip, Empty } from "../components/ui.tsx";
import { fmt, humanize } from "../lib/units.ts";
import type { Selection } from "./RobotViewer.tsx";
import type { RobotData } from "./useRobotScene.ts";

function Kv({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v ?? "—"}</dd></div>)}
    </dl>
  );
}

export function Inspector({ data, selection, onClear }: { data: RobotData; selection: Selection | null; onClear: () => void }) {
  const { pid = "" } = useParams();
  const catalog = useCatalog(pid);
  if (!selection) {
    const errors = data.diagnostics.filter((d) => d.severity === "error").length;
    return (
      <div className="inspector" aria-label="Inspector">
        <section>
          <h3>Robot</h3>
          <Kv rows={[
            ["Parts", data.components.length],
            ["CAD meshes", `${Object.keys(data.meshes).length} / ${data.components.length}`],
            ["Wires", data.wires.length],
            ["Joints", data.joints.length],
            ["Error diagnostics", errors],
          ]} />
        </section>
        <section><Empty icon="target" title="Click a part or wire">Its catalog data, mass source and diagnostics appear here.</Empty></section>
      </div>
    );
  }
  if (selection.kind === "wire") {
    const w = data.wires.find((x) => x.id === selection.id);
    const route = data.routes?.wires[selection.id];
    return (
      <div className="inspector" aria-label="Inspector">
        <section>
          <div className="row"><h2>{selection.id}</h2><span className="spacer" style={{ flex: 1 }} /><button className="small ghost" onClick={onClear}>Close</button></div>
          {w ? (
            <Kv rows={[
              ["Harness", w.harness], ["Gauge", `${w.awg} AWG`], ["Colour", <span key="c"><span className="swatch" style={{ background: `#${w.color.toString(16).padStart(6, "0")}` }} /> {w.colorName}</span>],
              ["Signal", w.signal], ["Routed length", fmt(route?.routed_length, "m")], ["Cut length", fmt(route?.cut_length, "m")],
              ["Outer diameter", fmt(w.radius * 2, "m")], ["Path", w.routed ? `${w.path.length} points` : "straight estimate (not routed)"],
            ]} />
          ) : <span className="muted">Unknown wire.</span>}
        </section>
        <section><h3>Diagnostics</h3><DiagnosticList items={data.bySubject[selection.id] ?? []} /></section>
      </div>
    );
  }
  const c = data.components.find((x) => x.id === selection.id);
  const part = data.cadParts[selection.id];
  const entry = c ? catalog.data?.components.find((x) => x.id === c.type) : undefined;
  const pinDiagnostics = Object.entries(data.bySubject).filter(([s]) => s.startsWith(`${selection.id}.`)).flatMap(([, d]) => d);
  const source = data.massSource[selection.id] ?? part?.mass_status ?? "catalog";
  return (
    <div className="inspector" aria-label="Inspector" data-testid="inspector">
      <section>
        <div className="row"><h2>{selection.id}</h2><span className="spacer" style={{ flex: 1 }} /><button className="small ghost" onClick={onClear}>Close</button></div>
        {c && <span className="secondary">{c.name}</span>}
        {c && (
          <Kv rows={[
            ["Type", <code key="t">{c.type}</code>], ["Kind", c.kind], ["Mount", c.mount],
            ["Position", c.xyz.map((v) => (v * 1000).toFixed(1)).join(", ") + " mm"],
            ["Moving", c.moving ? "yes" : "no"],
          ]} />
        )}
      </section>
      <section>
        <h3>Mass</h3>
        <Kv rows={[
          ["Mass", fmt(part?.mass ?? c?.mass, "kg")],
          ["Source", <Chip key="s" tone={source.startsWith("cad") ? "good" : "warning"}>{source}</Chip>],
          ["CAD status", part?.mass_status], ["Material", part?.material],
          ["Density", part?.density ? `${part.density.toFixed(0)} kg/m³` : null], ["Mesh", part?.mesh ? `${part.mesh_triangles ?? "?"} triangles` : "catalog envelope"],
          ["Catalog mass", fmt(entry?.mass ?? null, "kg")],
        ]} />
      </section>
      <section>
        <h3>Catalog</h3>
        {catalog.isPending ? <div className="skeleton" style={{ height: 60 }} /> : entry ? (
          <>
            <Kv rows={[
              ["Name", entry.name], ["Part number", entry.part_number || null],
              ["Envelope", entry.size ? `${entry.shape ?? ""} ${entry.size.map((v) => (v * 1000).toFixed(1)).join(" × ")} mm` : null],
              ["Behavior model", entry.behavior ? <code key="b">{entry.behavior}</code> : null],
              ["Provenance", entry.provenance ? <Chip key="p" tone={entry.provenance === "datasheet" || entry.provenance === "measured" ? "good" : "warning"}>{entry.provenance}</Chip> : null],
            ]} />
            {Object.keys(entry.connectors).length > 0 && (
              <div className="small">
                <strong>Connectors</strong>
                {Object.entries(entry.connectors).map(([id, type]) => (
                  <div key={id} className="row" style={{ justifyContent: "space-between" }}><code>{id}</code><span className="muted">{type}</span></div>
                ))}
              </div>
            )}
            {Object.keys(entry.pins).length > 0 && (
              <div className="small">
                <strong>Pins</strong>
                {Object.entries(entry.pins).slice(0, 16).map(([id, role]) => (
                  <div key={id} className="row" style={{ justifyContent: "space-between" }}><code>{id}</code><span className="muted">{humanize(role)}</span></div>
                ))}
              </div>
            )}
          </>
        ) : <span className="muted small">No catalog entry for {c?.type}.</span>}
      </section>
      <section><h3>Diagnostics</h3><DiagnosticList items={[...(data.bySubject[selection.id] ?? []), ...pinDiagnostics]} /></section>
    </div>
  );
}
