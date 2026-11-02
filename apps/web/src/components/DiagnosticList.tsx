import type { Diagnostic } from "../api/types.ts";
import { diagnosticHeadline } from "../lib/labels.ts";
import { Markdown } from "./Markdown.tsx";
import { Icon } from "./icons.tsx";
import { Details } from "./ui.tsx";

/** Compact diagnostics: a severity mark, the human headline and the suggested fix; codes and ids in details. */
export function DiagnosticList({ items, onFocus, selected, empty = "No diagnostics." }: {
  items: Diagnostic[]; onFocus?: (d: Diagnostic) => void; selected?: Diagnostic | null; empty?: string;
}) {
  if (!items.length) return <span className="muted small">{empty}</span>;
  return (
    <div className="diag-list">
      {items.map((d, i) => (
        <div key={`${d.code}${i}`} className={`diag ${d.severity}${onFocus ? " clickable" : ""}${selected === d ? " selected" : ""}`} onClick={() => onFocus?.(d)}>
          <span className="sev" aria-label={d.severity}><Icon name={d.severity === "error" ? "x" : d.severity === "warning" ? "alert" : "info"} size={12} /></span>
          <div className="stack" style={{ gap: 3, minWidth: 0 }}>
            <Markdown className="msg">{diagnosticHeadline(d)}</Markdown>
            {d.hint && <Markdown className="hint">{`Fix: ${d.hint}`}</Markdown>}
            <Details rows={[["Code", d.code], ["Stage", d.stage], ["Subjects", d.subjects.join(", ")],
              ["Technical evidence", JSON.stringify(d.data, null, 2)]]} />
          </div>
        </div>
      ))}
    </div>
  );
}
