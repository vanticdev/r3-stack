import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { usePcb, useProject } from "../api/hooks.ts";
import type { Diagnostic, PcbBoard, PcbCheck, StageRow } from "../api/types.ts";
import { BomTable } from "../components/BomTable.tsx";
import { DiagnosticList } from "../components/DiagnosticList.tsx";
import { Icon } from "../components/icons.tsx";
import { Card, CheckRow, Details, Empty, QueryView, Segmented, StaleNotice, type CheckOutcome } from "../components/ui.tsx";
import { pcbPinMap } from "../lib/domain.ts";
import { diagnosticHeadline, plural } from "../lib/labels.ts";
import { countsText } from "../lib/status.ts";
import { fmt, timeAgo } from "../lib/units.ts";
import { useProjectRuns } from "../state/runs.tsx";

export function PcbPage() {
  const { pid = "" } = useParams();
  const pcb = usePcb(pid);
  const [boardId, setBoardId] = useState<string | null>(null);
  return (
    <QueryView query={pcb} height={400}>
      {({ boards, result }) => {
        if (!boards.length) return <Card title="PCB"><Empty title="No boards in robot.yaml">Declare boards under <code>pcb.boards</code>.</Empty></Card>;
        const board = boards.find((b) => b.board === boardId) ?? boards[0]!;
        return <Board pid={pid} board={board} boards={boards} onBoard={setBoardId} diagnostics={result?.diagnostics ?? []} />;
      }}
    </QueryView>
  );
}

function checkOutcome(check: PcbCheck | null): CheckOutcome {
  return check?.errors === null || check?.errors === undefined ? "not_run" : check.errors === 0 ? "passed" : "failed";
}

function stageStatusText(stage: StageRow | undefined): string {
  if (!stage || stage.status === "missing") return "Not built yet";
  const when = stage.started_at ? ` ${timeAgo(stage.started_at)}` : "";
  return stage.status === "ok" ? `Built${when} · ${countsText(stage.counts ?? {})}` : `Last build failed${when} · ${countsText(stage.counts ?? {})}`;
}

function Board({ pid, board, boards, onBoard, diagnostics }: { pid: string; board: PcbBoard; boards: PcbBoard[]; onBoard: (id: string) => void; diagnostics: Diagnostic[] }) {
  const project = useProject(pid);
  const { start, active } = useProjectRuns(pid);
  const stage = project.data?.stages.find((s) => s.stage === "pcb");
  const sides = useMemo(() => {
    const find = (re: RegExp) => board.renders.find((r) => re.test(r.name));
    return [["top", find(/render.*top|top/i)], ["bottom", find(/render.*bottom|bottom/i)]]
      .filter((x): x is [string, NonNullable<ReturnType<typeof find>>] => !!x[1]);
  }, [board]);
  const [side, setSide] = useState<string>("top");
  const render = sides.find(([s]) => s === side)?.[1] ?? sides[0]?.[1] ?? board.renders[0];
  const pinMap = useMemo(() => pcbPinMap(board.connectors, board.catalog_connectors), [board]);
  const mismatches = pinMap.filter((p) => !p.match);
  const json = board.board_json;
  const drc = board.drc as (PcbCheck & { unconnected?: number }) | null;
  const unrouted = (json?.validation as { unrouted_connections?: number } | undefined)?.unrouted_connections ?? drc?.unconnected ?? null;
  const own = diagnostics.filter((d) => d.subjects.includes(board.board) || d.subjects.includes(board.component));
  const blockers = own.filter((d) => d.severity === "error");
  const buildLog = board.files.find((f) => /\.log$/.test(f.path));
  const outdated = !!stage?.stale && stage.status !== "missing";
  const build = () => void start({ stages: ["pcb"] });
  const busy = !!active;
  const failed = stage?.status === "failed";

  return (
    <div className="page">
      <div className="readiness">
        <div className="summary-line">
          <span className="lead">{board.board}</span>
          {boards.length > 1 && (
            <select value={board.board} onChange={(e) => onBoard(e.target.value)} aria-label="Board">{boards.map((b) => <option key={b.board}>{b.board}</option>)}</select>
          )}
          <span className={failed ? "" : "secondary"}>{failed ? <strong>{stageStatusText(stage)}</strong> : stageStatusText(stage)}</span>
          {outdated && <span className="tag outdated"><Icon name="clock" size={11} />Outdated · inputs changed</span>}
          <span className="spacer" style={{ flex: 1 }} />
          {buildLog && <a className="button" href={buildLog.url} target="_blank" rel="noreferrer">View build log</a>}
          {board.gerbers_url && <a className="button" href={board.gerbers_url} download><Icon name="download" size={14} />Fabrication files</a>}
          <button className="primary" onClick={build} disabled={busy}>{json ? "Rebuild PCB" : "Build PCB"}</button>
        </div>
        {blockers.length > 0 && (
          <div className="failing">
            {blockers.slice(0, 4).map((d, i) => (
              <div key={i} className="failing-row"><Icon name="x" size={13} /><span>{diagnosticHeadline(d)}</span></div>
            ))}
            {blockers.length > 4 && <span className="muted small">and {blockers.length - 4} more below</span>}
          </div>
        )}
      </div>
      {outdated && <StaleNotice what="PCB results" since={stage?.started_at} onRerun={build} rerunLabel="Rebuild PCB" busy={busy} />}

      {!json && (
        <Card>
          <div className="stack">
            <strong>Board data unavailable for {board.board}</strong>
            <span className="secondary">{stage && stage.status !== "missing" ? `${stageStatusText(stage)}; it did not produce board.json.` : "The PCB stage has not run for this board."} The sections below show whatever it did produce.</span>
            <Details summary="Toolchain and missing files" rows={[
              ["Missing", board.missing.join(", ") || "board.json"],
              ["Toolchain", "KiCad pcbnew module (board), kicad-cli (ERC, DRC, exports), Freerouting CLI (autorouting); all headless"],
            ]} />
          </div>
        </Card>
      )}

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <Card title="Checks" subtitle={json?.outline ? `Board ${fmt(json.outline.width, "m")} × ${fmt(json.outline.height, "m")}` : undefined}>
          <div className="check-list" data-testid="pcb-checks">
            <CheckRow outcome={checkOutcome(board.erc)} title="Schematic rules (ERC)"
                      result={board.erc?.errors === null || board.erc?.errors === undefined ? "Not run" : board.erc.errors === 0 ? `Passed${board.erc.warnings ? ` · ${plural(board.erc.warnings, "warning")}` : ""}` : `${plural(board.erc.errors, "error")}`} />
            <CheckRow outcome={checkOutcome(board.drc)} title="Board design rules (DRC)"
                      result={board.drc?.errors === null || board.drc?.errors === undefined ? "Not run" : board.drc.errors === 0 ? `Passed${board.drc.warnings ? ` · ${plural(board.drc.warnings, "warning")}` : ""}` : `${plural(board.drc.errors, "error")}`} />
            <CheckRow outcome={unrouted === null ? "not_run" : unrouted === 0 ? "passed" : "failed"} title="Routing"
                      result={unrouted === null ? "Not run" : unrouted === 0 ? "All connections routed" : `${plural(unrouted, "connection")} unrouted`} />
            <CheckRow outcome={!pinMap.length ? "not_run" : mismatches.length ? "failed" : "passed"} title="Connector pins match the catalog"
                      result={!pinMap.length ? "No pins available" : mismatches.length ? `${plural(mismatches.length, "pin")} differ of ${pinMap.length}` : `All ${pinMap.length} pins match`}>
              {mismatches.slice(0, 6).map((p) => (
                <span key={`${p.connector}.${p.pin}`}>{p.connector} pin {p.pin}: board net <strong>{p.boardNet ?? "unconnected"}</strong>, catalog expects <strong>{p.catalogPin ?? "unassigned"}</strong></span>
              ))}
            </CheckRow>
            {outdated && <span className="small muted">These checks ran on earlier inputs.</span>}
          </div>
        </Card>
        <Card title="Board render" actions={sides.length > 1 && <Segmented label="Side" value={side} onChange={setSide} options={sides.map(([s]) => ({ value: s, label: s === "top" ? "Top" : "Bottom" }))} />}>
          {render ? <img src={render.url} alt={`${board.board} ${render.name}`} className="board-render" /> : <span className="muted">No render yet (the PCB stage writes render_top.png and render_bottom.png).</span>}
        </Card>
      </div>

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <Card title="Diagnostics" subtitle={own.length ? `${countsText({ error: blockers.length, warning: own.filter((d) => d.severity === "warning").length })}` : undefined}>
          <DiagnosticList items={own.slice().sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : b.severity === "error" ? 1 : a.severity === "warning" ? -1 : 1))}
                          empty={stage?.status === "missing" ? "Not built yet." : "No diagnostics for this board."} />
        </Card>
        <Card title="Connector pin → net" subtitle="Board netlist against the catalog entry; mismatches first" flush>
          {pinMap.length ? (
            <div className="table-wrap" style={{ maxHeight: 460 }}>
              <table data-testid="pin-map">
                <thead><tr><th>Pin</th><th>Board net</th><th>Catalog pin</th><th>Match</th></tr></thead>
                <tbody>
                  {[...mismatches, ...pinMap.filter((p) => p.match)].map((p) => (
                    <tr key={`${p.connector}.${p.pin}`} className={p.match ? "" : "mismatch"}>
                      <td className="mono">{p.connector}.{p.pin}</td>
                      <td className="mono">{p.boardNet ?? <span className="muted">—</span>}</td>
                      <td className="mono">{p.catalogPin ?? <span className="muted">unassigned</span>}</td>
                      <td>{p.match ? <span className="quiet-check" aria-label="matches"><Icon name="check" size={12} /></span> : <strong className="clearance critical">differs</strong>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div style={{ padding: 14 }} className="muted">No pins available: the board netlist or the catalog connector map is missing.</div>}
        </Card>
      </div>

      <Card title="Bill of materials" flush>{board.bom?.length ? <BomTable rows={board.bom} /> : <div style={{ padding: 14 }} className="muted">No BOM yet.</div>}</Card>
      {board.files.length > 0 && (
        <Details summary={`Files (${board.files.length})`}>
          <div className="file-list">
            {board.files.map((f) => <a key={f.path} href={f.url} target="_blank" rel="noreferrer" className="mono small">{f.path.split("/").slice(2).join("/")}</a>)}
          </div>
        </Details>
      )}
    </div>
  );
}
