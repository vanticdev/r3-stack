import { useState } from "react";
import { useParams } from "react-router-dom";

import { usePcb } from "../api/hooks.ts";
import type { PcbBoard } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, Empty, Kpi, QueryView, Segmented } from "../components/ui.tsx";
import { DiagnosticList } from "../components/DiagnosticList.tsx";
import { fmt } from "../lib/units.ts";
import { BomTable } from "../components/BomTable.tsx";

function checkTone(errors: number | null | undefined): "good" | "critical" | "muted" {
  return errors === null || errors === undefined ? "muted" : errors === 0 ? "good" : "critical";
}

export function PcbPage() {
  const { pid = "" } = useParams();
  const pcb = usePcb(pid);
  const [boardId, setBoardId] = useState<string | null>(null);
  return (
    <QueryView query={pcb} height={400}>
      {({ boards }) => {
        if (!boards.length) return <Card title="PCB"><Empty title="No boards built">Run the pcb stage (KiCad, headless) to generate board.json, renders and gerbers.</Empty></Card>;
        const board = boards.find((b) => b.board === boardId) ?? boards[0]!;
        return <Board board={board} boards={boards} onBoard={setBoardId} />;
      }}
    </QueryView>
  );
}

function Board({ board, boards, onBoard }: { board: PcbBoard; boards: PcbBoard[]; onBoard: (id: string) => void }) {
  const [side, setSide] = useState<"top" | "bottom" | "both">("top");
  const mismatches = board.pin_map.filter((p) => !p.match);
  const connectors = [...new Set(board.pin_map.map((p) => p.connector))];
  const validation = board.validation as { clearance_violations?: number; unrouted_connections?: number; min_clearance_mm?: number; clearance_below_preferred?: number } | undefined;
  const renders = Object.entries(board.renders);
  const render = board.renders[side] ?? renders[0]?.[1];
  return (
    <div className="page">
      <div className="page-head">
        <div className="stack" style={{ gap: 2 }}>
          <h1>PCB · {board.board}</h1>
          <p>Carrier board for component <code>{board.component}</code>{board.outline ? `, ${fmt(board.outline.width, "m")} × ${fmt(board.outline.height, "m")}` : ""}. Built with KiCad's pcbnew module and kicad-cli, no GUI.</p>
        </div>
        <div className="spacer" />
        {boards.length > 1 && (
          <select value={board.board} onChange={(e) => onBoard(e.target.value)} aria-label="Board">{boards.map((b) => <option key={b.board}>{b.board}</option>)}</select>
        )}
        {board.gerber_url ? <a className="button" href={board.gerber_url} download><Icon name="download" size={14} />Gerbers</a>
          : <button disabled title="The pcb stage has not exported gerbers (kicad-cli needed)"><Icon name="download" size={14} />Gerbers</button>}
      </div>

      <div className="grid cols-4">
        <Kpi label="ERC errors" value={board.erc?.errors ?? "—"} sub={board.erc?.source} tone={checkTone(board.erc?.errors)}
             toneLabel={board.erc?.errors === 0 ? "clean" : board.erc?.errors ? "errors" : "not run"} />
        <Kpi label="DRC errors" value={board.drc?.errors ?? "—"} sub={board.drc?.source} tone={checkTone(board.drc?.errors)}
             toneLabel={board.drc?.errors === 0 ? "clean" : board.drc?.errors ? "errors" : "not run"} />
        <Kpi label="Unrouted connections" value={validation?.unrouted_connections ?? "—"} sub={`min clearance ${validation?.min_clearance_mm ?? "—"} mm`}
             tone={validation?.unrouted_connections === 0 ? "good" : validation ? "critical" : undefined} toneLabel={validation?.unrouted_connections === 0 ? "fully routed" : undefined} />
        <Kpi label="Pin map vs catalog" value={`${board.pin_map.length - mismatches.length}/${board.pin_map.length}`} sub="connector pins matching the catalog net"
             tone={mismatches.length ? "critical" : "good"} toneLabel={mismatches.length ? `${mismatches.length} mismatch` : "matches catalog"} />
      </div>

      <div className="grid cols-2">
        <Card title="Board render" actions={<Segmented label="Side" value={side} onChange={setSide}
              options={(["top", "bottom", "both"] as const).filter((s) => board.renders[s]).map((s) => ({ value: s, label: s }))} />}>
          {render ? <img src={render} alt={`${board.board} ${side}`} style={{ width: "100%", borderRadius: 6, background: "#fff" }} /> : <Empty title="No renders" />}
        </Card>
        <Card title="Connector pin → net" subtitle="Board netlist against the catalog entry of the component" flush>
          <div className="table-wrap" style={{ maxHeight: 560 }}>
            <table data-testid="pin-map">
              <thead><tr><th>Connector</th><th>Pin</th><th>Board net</th><th>Catalog net</th><th>Match</th></tr></thead>
              <tbody>
                {connectors.flatMap((c) => board.pin_map.filter((p) => p.connector === c).map((p, i) => (
                  <tr key={`${c}.${p.pin}`}>
                    <td className="mono">{i === 0 ? c : ""}</td>
                    <td className="mono">{p.pin}</td>
                    <td className="mono">{p.board_net ?? <span className="muted">—</span>}</td>
                    <td className="mono">{p.catalog_net ?? <span className="muted">unassigned</span>}</td>
                    <td>{p.match ? <Chip tone="good">ok</Chip> : <Chip tone="critical">mismatch</Chip>}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Bill of materials" flush><BomTable rows={board.bom} /></Card>
        <Card title="PCB diagnostics"><DiagnosticList items={board.diagnostics ?? []} /></Card>
      </div>
    </div>
  );
}
