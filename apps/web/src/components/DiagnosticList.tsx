import type { Diagnostic } from "../api/types.ts";
import { SeverityChip } from "./ui.tsx";

export function DiagnosticList({ items, onFocus, selected }: { items: Diagnostic[]; onFocus?: (d: Diagnostic) => void; selected?: Diagnostic | null }) {
  if (!items.length) return <span className="muted small">No diagnostics.</span>;
  return (
    <div className="stack">
      {items.map((d, i) => (
        <div key={`${d.code}${i}`} className={`diag${onFocus ? " clickable" : ""}${selected === d ? " selected" : ""}`} onClick={() => onFocus?.(d)}>
          <div className="row"><SeverityChip severity={d.severity} /><code>{d.code}</code><span className="muted small">{d.stage}</span></div>
          <span className="msg">{d.message}</span>
          {d.hint && <span className="hint">{d.hint}</span>}
        </div>
      ))}
    </div>
  );
}
