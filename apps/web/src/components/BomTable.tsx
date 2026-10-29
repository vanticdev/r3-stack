import { Empty } from "./ui.tsx";

export function BomTable({ rows }: { rows: Record<string, string | number>[] }) {
  if (!rows.length) return <Empty title="No BOM">The stage writes a CSV bill of materials.</Empty>;
  const columns = Object.keys(rows[0]!);
  return (
    <div className="table-wrap" style={{ maxHeight: 520 }}>
      <table>
        <thead><tr>{columns.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{columns.map((c) => <td key={c} className={typeof r[c] === "number" || /^[\d.]+$/.test(String(r[c] ?? "")) ? "num" : ""}>{r[c]}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
