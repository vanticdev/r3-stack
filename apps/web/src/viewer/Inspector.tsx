// Inspector for a clicked part or wire: catalog data, mass source, CAD properties and diagnostics.
import { useParams } from "react-router-dom";

import { useCatalog } from "../api/hooks.ts";
import { DiagnosticList } from "../components/DiagnosticList.tsx";
import { Chip, Empty } from "../components/ui.tsx";
import { fmt, humanize, quantity } from "../lib/units.ts";
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
  const entry = c ? catalog.data?.components[c.type] : undefined;
  const provenance = entry?.mechanical?.provenance;
  const pinDiagnostics = Object.entries(data.bySubject).filter(([s]) => s.startsWith(`${selection.id}.`)).flatMap(([, d]) => d);
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
          ["Source", <Chip key="s" tone={(data.massSource[selection.id] ?? part?.mass_status ?? "").startsWith("cad") ? "good" : "warning"}>{data.massSource[selection.id] ?? part?.mass_status ?? "catalog"}</Chip>],
          ["CAD status", part?.mass_status], ["Material", part?.material ?? entry?.mechanical?.material],
          ["Density", part?.density ? `${part.density} kg/m³` : null], ["Mesh", part?.mesh ? `${part.mesh_triangles ?? "?"} triangles` : "catalog envelope"],
          ["Catalog mass", fmt(quantity(entry?.mechanical?.mass), "kg")],
        ]} />
      </section>
      <section>
        <h3>Catalog</h3>
        {catalog.isPending ? <div className="skeleton" style={{ height: 60 }} /> : entry ? (
          <>
            <Kv rows={[
              ["Manufacturer", entry.manufacturer], ["Part number", entry.part_number],
              ["Envelope", entry.mechanical?.size ? `${entry.mechanical.shape} ${entry.mechanical.size.map((v) => (quantity(v) * 1000).toFixed(1)).join(" × ")} mm` : null],
              ["Provenance", provenance?.status ? <Chip key="p" tone={provenance.status === "datasheet" ? "good" : "warning"}>{provenance.status}</Chip> : null],
            ]} />
            {provenance?.source && <span className="small muted">{provenance.source}</span>}
            {provenance?.notes && <span className="small muted">{provenance.notes}</span>}
            {entry.description && <p className="small secondary">{entry.description}</p>}
            {entry.connectors && (
              <div className="small">
                <strong>Connectors</strong>
                {Object.entries(entry.connectors).map(([id, conn]) => (
                  <div key={id} className="row" style={{ justifyContent: "space-between" }}><code>{id}</code><span className="muted">{conn.type} · {Object.keys(conn.pins ?? {}).length} pins</span></div>
                ))}
              </div>
            )}
            {entry.pins && (
              <div className="small">
                <strong>Pins</strong>
                {Object.entries(entry.pins).slice(0, 14).map(([id, pin]) => (
                  <div key={id} className="row" style={{ justifyContent: "space-between" }}><code>{id}</code>
                    <span className="muted">{humanize(String(pin.role ?? ""))}{pin.current_max ? ` · ${String(pin.current_max)} A` : ""}{pin.voltage_max ? ` · ≤${String(pin.voltage_max)} V` : ""}</span></div>
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
