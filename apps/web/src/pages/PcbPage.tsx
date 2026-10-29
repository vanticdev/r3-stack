import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { usePcb } from "../api/hooks.ts";
import type { Diagnostic, PcbBoard, PcbCheck } from "../api/types.ts";
import { BomTable } from "../components/BomTable.tsx";
import { DiagnosticList } from "../components/DiagnosticList.tsx";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, Empty, Kpi, QueryView, Segmented } from "../components/ui.tsx";
import { pcbPinMap } from "../lib/domain.ts";
import { fmt } from "../lib/units.ts";

function CheckKpi({ label, check }: { label: string; check: PcbCheck | null }) {
  const errors = check?.errors;
  return (
    <Kpi label={label} value={errors ?? "—"} sub={check?.source ?? "not run"}
         tone={errors === null || errors === undefined ? "muted" : errors === 0 ? "good" : "critical"}
         toneLabel={errors === 0 ? "clean" : errors ? "errors" : "not run"} />
  );
}

export function PcbPage() {
  const { pid = "" } = useParams();
  const pcb = usePcb(pid);
  const [boardId, setBoardId] = useState<string | null>(null);
  return (
    <QueryView query={pcb} height={400}>
      {({ boards, result }) => {
        if (!boards.length) return <Card title="PCB"><Empty title="No boards in robot.yaml">Declare boards under <code>pcb.boards</code>.</Empty></Card>;
        const board = boards.find((b) => b.board === boardId) ?? boards[0]!;
        return <Board board={board} boards={boards} onBoard={setBoardId} diagnostics={result?.diagnostics ?? []} />;
      }}
    </QueryView>
  );
}

function Board({ board, boards, onBoard, diagnostics }: { board: PcbBoard; boards: PcbBoard[]; onBoard: (id: string) => void; diagnostics: Diagnostic[] }) {
  const sides = useMemo(() => {
    const find = (re: RegExp) => board.renders.find((r) => re.test(r.name));
    return [["top", find(/top/i)], ["bottom", find(/bottom/i)], ["both", find(/both/i)]]
      .filter((x): x is [string, NonNullable<ReturnType<typeof find>>] => !!x[1]);
  }, [board]);
  const [side, setSide] = useState<string>("top");
  const render = sides.find(([s]) => s === side)?.[1] ?? sides[0]?.[1] ?? board.renders[0];
  const pinMap = useMemo(() => pcbPinMap(board.connectors, board.catalog_connectors), [board]);
  const mismatches = pinMap.filter((p) => !p.match);
  const json = board.board_json;
  const validation = json?.validation as { unrouted_connections?: number; min_clearance_mm?: number } | undefined;

  if (!json) {
    return (
      <div className="page">
        <div className="page-head"><h1>PCB · {board.board}</h1></div>
        <Card><Empty title="Board not built yet">Run the pcb stage (KiCad headless: pcbnew module + kicad-cli) to produce board.json, renders and gerbers.
          {board.missing.length ? <div className="muted">missing: {board.missing.join(", ")}</div> : null}</Empty></Card>
      </div>
    );
  }
  return (
    <div className="page">
      <div className="page-head">
        <div className="stack" style={{ gap: 2 }}>
          <h1>PCB · {board.board}</h1>
          <p>Board for component <code>{board.component}</code>{json.outline ? `, ${fmt(json.outline.width, "m")} × ${fmt(json.outline.height, "m")}` : ""}. Built with KiCad's pcbnew module and kicad-cli, no GUI.</p>
        </div>
        <div className="spacer" />
        {boards.length > 1 && (
          <select value={board.board} onChange={(e) => onBoard(e.target.value)} aria-label="Board">{boards.map((b) => <option key={b.board}>{b.board}</option>)}</select>
        )}
        {board.gerbers_url ? <a className="button" href={board.gerbers_url} download><Icon name="download" size={14} />Gerbers</a>
          : <button disabled title="The pcb stage has not exported a gerber zip (kicad-cli needed)"><Icon name="download" size={14} />Gerbers</button>}
      </div>

      <div className="grid cols-4">
        <CheckKpi label="ERC errors" check={board.erc} />
        <CheckKpi label="DRC errors" check={board.drc} />
        <Kpi label="Unrouted connections" value={validation?.unrouted_connections ?? "—"} sub={`min clearance ${validation?.min_clearance_mm ?? "—"} mm`}
             tone={validation?.unrouted_connections === 0 ? "good" : validation ? "critical" : undefined} toneLabel={validation?.unrouted_connections === 0 ? "fully routed" : undefined} />
        <Kpi label="Pin map vs catalog" value={`${pinMap.length - mismatches.length}/${pinMap.length}`} sub="connector pins matching the catalog"
             tone={mismatches.length ? "critical" : "good"} toneLabel={mismatches.length ? `${mismatches.length} mismatch` : "matches catalog"} />
      </div>

      <div className="grid cols-2">
        <Card title="Board render" actions={sides.length > 1 && <Segmented label="Side" value={side} onChange={setSide} options={sides.map(([s]) => ({ value: s, label: s }))} />}>
          {render ? <img src={render.url} alt={`${board.board} ${render.name}`} style={{ width: "100%", borderRadius: 6, background: "#fff" }} /> : <Empty title="No renders" />}
        </Card>
        <Card title="Connector pin → net" subtitle="Board netlist against the catalog entry of the component" flush>
          <div className="table-wrap" style={{ maxHeight: 560 }}>
            <table data-testid="pin-map">
              <thead><tr><th>Connector</th><th>Pin</th><th>Board net</th><th>Catalog pin</th><th>Match</th></tr></thead>
              <tbody>
                {pinMap.map((p, i) => (
                  <tr key={`${p.connector}.${p.pin}`}>
                    <td className="mono">{i === 0 || pinMap[i - 1]!.connector !== p.connector ? p.connector : ""}</td>
                    <td className="mono">{p.pin}</td>
                    <td className="mono">{p.boardNet ?? <span className="muted">—</span>}</td>
                    <td className="mono">{p.catalogPin ?? <span className="muted">unassigned</span>}</td>
                    <td>{p.match ? <Chip tone="good">ok</Chip> : <Chip tone="critical">mismatch</Chip>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Bill of materials" flush><BomTable rows={board.bom ?? []} /></Card>
        <Card title="PCB diagnostics"><DiagnosticList items={diagnostics} /></Card>
      </div>
    </div>
  );
}
