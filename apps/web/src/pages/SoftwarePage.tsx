import { useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { useModel, useProject, useScenarios, useSoftware } from "../api/hooks.ts";
import type { Binding, BindingPin, FirmwareLogLine, ProjectDetail, SoftwareResponse } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Details, QueryView, Segmented } from "../components/ui.tsx";
import { partLabel, plural } from "../lib/labels.ts";
import { timeAgo } from "../lib/units.ts";

const LEVELS = ["all", "error", "warning", "info", "debug"] as const;
type Level = (typeof LEVELS)[number];

/** "pi.J8.12" + node "C:pi.GPIO18" → "header pin 12 · GPIO18". */
function piPinLabel(pin: BindingPin): string {
  const header = pin.pin.split(".").pop();
  const gpio = pin.node?.split(".").pop();
  return `header pin ${header}${gpio && gpio !== header ? ` · ${gpio}` : ""}`;
}

interface Route {
  /** expected destination connector pin, e.g. esc.J_SIG.1 */
  destination: string;
  /** logical pin it reached, e.g. esc.SIG */
  logical: string | null;
  reached: boolean;
  pin: BindingPin | undefined;
  /** intermediate nodes (board nets, series parts) */
  via: string[];
}

/** One route per computer pin, destination first. Expected pins pair with computer pins in order. */
export function bindingRoutes(b: Binding): Route[] {
  const expected = b.expects_reached.length ? b.expects_reached : b.pin_map.map((p) => ({ pin: (p.reaches ?? p.devices ?? [])[0] ?? p.pin, reached: !p.error }));
  return expected.map((e, i) => {
    const pin = b.pin_map.length === expected.length ? b.pin_map[i] : b.pin_map.find((p) => (p.reaches ?? []).some((r) => r.split(".")[0] === e.pin.split(".")[0]));
    const component = e.pin.split(".")[0]!;
    const reached = pin?.reaches ?? pin?.devices ?? [];
    const logical = reached.find((r) => r.split(".")[0] === component) ?? null;
    const via = reached.filter((r) => r !== logical && r.split(".")[0] !== component);
    return { destination: e.pin, logical, reached: e.reached, pin, via };
  });
}

function interfaceText(b: Binding): string {
  const parts = [b.interface.toUpperCase()];
  if (b.device) parts.push(`${b.device}${b.channel !== null && b.channel !== undefined ? ` channel ${b.channel}` : ""}`);
  if (typeof b.frequency === "number") parts.push(`${b.frequency} Hz`);
  else if (typeof b.frequency === "string") parts.push(b.frequency);
  if (typeof b.baud === "number") parts.push(`${b.baud.toLocaleString()} baud`);
  return parts.join(" · ");
}

function NetlistMatch({ project, bindings }: { project: ProjectDetail | undefined; bindings: Binding[] }) {
  const model = project?.stages.find((s) => s.stage === "model");
  const ok = bindings.filter((b) => b.expects_reached.every((e) => e.reached)).length;
  const sha = project?.results.model?.inputs?.robot_yaml_sha256;
  return (
    <div className="summary-line">
      <span className="lead">{ok === bindings.length ? `Netlist match: all ${bindings.length} bindings reach their devices` : `Netlist match: ${bindings.length - ok} of ${bindings.length} bindings do not reach their devices`}</span>
      <span className="secondary">checked by the 3D model stage{model?.started_at ? ` ${timeAgo(model.started_at)}` : ""}</span>
      {model?.stale ? <span className="tag outdated"><Icon name="clock" size={11} />Outdated · inputs changed</span> : model?.status === "ok" ? <span className="tag">Current</span> : null}
      {sha && <Details summary="Revision" rows={[["robot.yaml", sha]]} />}
    </div>
  );
}

export function SoftwarePage() {
  const { pid = "" } = useParams();
  const software = useSoftware(pid);
  const project = useProject(pid);
  useModel(pid);
  return (
    <QueryView query={software} height={400}>
      {(s) => (
        <div className="page">
          <div className="readiness"><NetlistMatch project={project.data} bindings={s.bindings} /></div>
          <Card title="Where each software binding goes" subtitle="Device pin first, then the Pi header pin that drives it" flush>
            <div className="list binding-list" data-testid="binding-map">
              {s.bindings.map((b) => <BindingRow key={b.handle} b={b} />)}
            </div>
          </Card>
          <div className="grid side" style={{ alignItems: "start" }}>
            <FirmwareLogs pid={pid} logs={s.firmware_logs} />
            <Modules modules={s.modules} project={project.data} deployLog={s.deploy_log_url ?? null} />
          </div>
        </div>
      )}
    </QueryView>
  );
}

function BindingRow({ b }: { b: Binding }) {
  const routes = bindingRoutes(b);
  return (
    <div className="binding-row">
      <div className="binding-head">
        <strong>{b.handle}</strong>
        <span className="secondary small">{interfaceText(b)}</span>
      </div>
      {routes.map((r, i) => (
        <div key={i} className={`route${r.reached ? "" : " broken"}`}>
          <span className="mark" aria-label={r.reached ? "reached" : "not reached"}><Icon name={r.reached ? "check" : "x"} size={12} /></span>
          <span className="dest"><span className="mono">{r.destination}</span>{r.logical && <span className="muted"> · {r.logical.split(".").pop()}</span>}</span>
          <span className="from muted">← Pi {r.pin ? piPinLabel(r.pin) : "?"}</span>
          {r.pin && (
            <Details summary="Path">
              <span className="mono small path">
                {[r.pin.pin, `net ${r.pin.net_name ?? r.pin.net ?? "?"}`, ...r.via.filter((v) => !(r.pin?.net_name && v.endsWith(r.pin.net_name))), r.logical ?? r.destination].join(" → ")}
              </span>
              {r.pin.error && <span className="small" style={{ color: "var(--critical)" }}>{r.pin.error}</span>}
            </Details>
          )}
        </div>
      ))}
    </div>
  );
}

function Modules({ modules, project, deployLog }: { modules: SoftwareResponse["modules"]; project: ProjectDetail | undefined; deployLog: string | null }) {
  const repo = project?.links.github ?? null;
  const branch = project?.git?.branch ?? "main";
  return (
    <Card title="Firmware module">
      {modules.length ? (
        <div className="stack" style={{ gap: 14 }}>
          {modules.map((m) => {
            const files = (m.files ?? []).filter((f) => !f.includes("/_vendor/"));
            return (
              <div key={m.name} className="stack" style={{ gap: 6 }}>
                <strong>{m.name}</strong>
                <dl className="kv">
                  <dt>Runs on</dt><dd>{partLabel(m.runs_on ?? "—")}</dd>
                  <dt>Language</dt><dd>{m.language ?? "—"}</dd>
                  <dt>Entry point</dt><dd className="mono">{m.entrypoint ?? "—"}</dd>
                  <dt>Folder</dt><dd className="mono">{m.path ?? "—"}{m.exists === false && <strong style={{ color: "var(--critical)" }}> · missing</strong>}</dd>
                </dl>
                {files.length > 0 && (
                  <Details summary={`${plural(files.length, "source file")}`}>
                    <div className="stack small" style={{ gap: 2 }}>
                      {files.map((f) => repo
                        ? <a key={f} href={`${repo.replace(/\.git$/, "")}/blob/${branch}/${f}`} target="_blank" rel="noreferrer" className="mono">{f.split("/").pop()}</a>
                        : <span key={f} className="mono" title={f}>{f.split("/").pop()}</span>)}
                      {!repo && <span className="muted">Link a GitHub repository to open the sources from here.</span>}
                    </div>
                  </Details>
                )}
              </div>
            );
          })}
          {deployLog && <a href={deployLog} target="_blank" rel="noreferrer">Last fleet deploy log</a>}
        </div>
      ) : <span className="muted">No software modules in robot.yaml.</span>}
    </Card>
  );
}

function FirmwareLogs({ pid, logs }: { pid: string; logs: SoftwareResponse["firmware_logs"] }) {
  const scenarios = useScenarios(pid);
  const [scenario, setScenario] = useState<string | null>(null);
  const [level, setLevel] = useState<Level>("all");
  const [text, setText] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const chosen = logs.find((l) => l.scenario === scenario) ?? logs[0];
  const all = chosen?.firmware_logs ?? [];
  const lines = useMemo(() => all.map((l, i) => ({ l, i })).filter(({ l }) => (level === "all" || l.level === level) &&
    (!text || `${l.message} ${JSON.stringify(l.fields ?? {})}`.toLowerCase().includes(text.toLowerCase()))), [all, level, text]);
  const errors = all.filter((l) => l.level === "error").length;
  const warnings = all.filter((l) => l.level === "warning").length;
  const last = scenarios.data?.scenarios.find((x) => x.id === chosen?.scenario)?.last;
  const jump = (lvl: string) => {
    const index = all.findIndex((l) => l.level === lvl);
    if (index < 0) return;
    setLevel("all"); setText(""); setOpen(index);
    requestAnimationFrame(() => box.current?.querySelector(`[data-line="${index}"]`)?.scrollIntoView({ block: "center" }));
  };
  return (
    <Card title="Simulation firmware log" flush
          subtitle={chosen ? <>The real controller against the virtual hardware, scenario {partLabel(chosen.scenario)}{last?.stale ? " · previous run, inputs changed" : ""}</> : "No simulation run yet"}>
      <div className="log-toolbar">
        {logs.length > 1 && <select value={chosen?.scenario} onChange={(e) => setScenario(e.target.value)} aria-label="Scenario">{logs.map((l) => <option key={l.scenario} value={l.scenario}>{partLabel(l.scenario)}</option>)}</select>}
        <Segmented label="Level" value={level} onChange={setLevel} options={LEVELS.map((l) => ({ value: l, label: l === "all" ? "All" : l[0]!.toUpperCase() + l.slice(1) }))} />
        <input type="search" placeholder="Filter messages" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter log" />
        <span className="spacer" style={{ flex: 1 }} />
        <button className="small ghost" disabled={!errors} onClick={() => jump("error")}>{plural(errors, "error")}</button>
        <button className="small ghost" disabled={!warnings} onClick={() => jump("warning")}>{plural(warnings, "warning")}</button>
      </div>
      <div className="fw-log" ref={box} data-testid="firmware-log" role="table" aria-label="Firmware log">
        {lines.map(({ l, i }) => <LogLine key={i} index={i} line={l} open={open === i} onToggle={() => setOpen(open === i ? null : i)} />)}
        {!lines.length && <span className="muted" style={{ padding: 10 }}>No log lines.</span>}
      </div>
      {chosen?.software_log_url && (
        <div style={{ padding: "8px 14px", borderTop: "1px solid var(--line)" }} className="small">
          <a href={chosen.software_log_url} target="_blank" rel="noreferrer">Full process output (software.log)</a>
        </div>
      )}
    </Card>
  );
}

function LogLine({ line, index, open, onToggle }: { line: FirmwareLogLine; index: number; open: boolean; onToggle: () => void }) {
  const fields = Object.entries(line.fields ?? {});
  return (
    <div className={`fw-line ${line.level}${open ? " open" : ""}`} data-line={index} role="row" onClick={fields.length ? onToggle : undefined}>
      <span className="t" role="cell">{line.t.toFixed(3)} s</span>
      <span className="lvl" role="cell">{line.level}</span>
      <span className="msg" role="cell">{line.message.replace(/_/g, " ")}{fields.length > 0 && !open && <span className="muted"> · {fields.length} {fields.length === 1 ? "field" : "fields"}</span>}</span>
      {open && (
        <dl className="fields">
          {fields.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>)}
        </dl>
      )}
    </div>
  );
}
