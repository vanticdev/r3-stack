import { createContext, useContext, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useParams } from "react-router-dom";

import { useDiagnostics, useProject } from "../api/hooks.ts";
import type { ProjectDetail } from "../api/types.ts";
import { severityCounts } from "../lib/domain.ts";
import { stageLabel } from "../lib/labels.ts";
import { countsText, projectStatus, type StageState } from "../lib/status.ts";
import { humanize, timeAgo } from "../lib/units.ts";
import { useMockBackend, useTheme } from "../state/prefs.ts";
import { useProjectRuns, type TrackedRun } from "../state/runs.tsx";
import { Icon, type IconName } from "./icons.tsx";
import { Chip, Details, Popover } from "./ui.tsx";

/** Project navigation. Pages added by other features append an entry here and a route in App.tsx. */
export const PROJECT_NAV: { to: string; label: string; icon: IconName }[] = [
  { to: "overview", label: "Overview", icon: "overview" },
  { to: "3d", label: "3D model", icon: "cube" },
  { to: "wiring", label: "Wiring", icon: "wire" },
  { to: "electrical", label: "Electrical", icon: "bolt" },
  { to: "pcb", label: "PCB", icon: "chip" },
  { to: "software", label: "Software", icon: "code" },
  { to: "sim", label: "Simulation", icon: "play" },
  { to: "diagnostics", label: "Diagnostics", icon: "pulse" },
  { to: "agent", label: "Agent", icon: "sparkle" },
  { to: "deploy", label: "Deploy", icon: "rocket" },
];

// ------------------------------------------------------------------ navigation drawer (narrow screens)

const DrawerContext = createContext<{ open: boolean; setOpen: (open: boolean) => void }>({ open: false, setOpen: () => undefined });

export function MenuButton() {
  const { open, setOpen } = useContext(DrawerContext);
  return (
    <button className="ghost menu-button" aria-label="Open navigation" aria-expanded={open} onClick={() => setOpen(!open)}>
      <Icon name="menu" />
    </button>
  );
}

function ProjectNav({ pid }: { pid: string }) {
  const diagnostics = useDiagnostics(pid);
  const counts = diagnostics.data?.counts ?? severityCounts(diagnostics.data?.diagnostics ?? []);
  return (
    <nav className="nav-section nav" aria-label="Project">
      <h3>Project</h3>
      {PROJECT_NAV.map((item) => (
        <NavLink key={item.to} to={`/p/${encodeURIComponent(pid)}/${item.to}`}>
          <Icon name={item.icon} />
          {item.label}
          {item.to === "diagnostics" && !!counts.error && <span className="badge critical" title={`${counts.error} errors`}>{counts.error}</span>}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const { pid } = useParams();
  const [theme, setTheme] = useTheme();
  const mock = useMockBackend();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [location.pathname]);
  return (
    <DrawerContext.Provider value={{ open, setOpen }}>
      <div className={`shell${open ? " nav-open" : ""}`}>
        <aside className="sidebar" aria-label="Navigation">
          <NavLink to="/" className="brand">
            <span className="brand-mark">R3</span>
            <span>R3</span>
          </NavLink>
          <nav className="nav-section nav" aria-label="Workspace">
            <NavLink to="/" end className={() => (location.pathname === "/" ? "active" : "")}><Icon name="folder" />Projects</NavLink>
            <NavLink to="/environment"><Icon name="stethoscope" />Environment</NavLink>
          </nav>
          {pid && <ProjectNav pid={pid} />}
          <div className="sidebar-foot">
            <span className="small muted">{mock === null ? "Connecting…" : mock ? "Mock API (dev server)" : "Connected to r3 serve"}</span>
            <button className="small ghost" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">
              <Icon name={theme === "dark" ? "sun" : "moon"} size={14} />{theme === "dark" ? "Light theme" : "Dark theme"}
            </button>
          </div>
        </aside>
        <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" />
        <div className="main">
          {mock && (
            <div className="banner" role="status">
              <strong>Mock API</strong>
              <span className="ellipsis">r3 serve is not reachable; showing the projects' build/ artifacts. Runs replay; edits stay in memory.</span>
            </div>
          )}
          {!pid && (
            <header className="mobile-bar">
              <MenuButton />
              <span className="brand-mark">R3</span>
            </header>
          )}
          <Outlet />
        </div>
      </div>
    </DrawerContext.Provider>
  );
}

// ------------------------------------------------------------------ run status and log

function RunStatus({ run, onDismiss }: { run: TrackedRun; onDismiss: () => void }) {
  const running = run.status === "queued" || run.status === "running";
  return (
    <Popover label="Pipeline run" align="right" trigger={() => running
      ? <Chip tone="running">{run.stage ? `Running ${stageLabel(run.stage)}` : "Starting"} · {Math.round(run.progress * 100)}%</Chip>
      : <Chip tone={run.status === "succeeded" ? "good" : run.status === "cancelled" ? "muted" : "critical"}>Run {run.status}</Chip>}>
      {(close) => (
        <div className="stack run-popover">
          <div className="row"><strong>Pipeline run</strong><span className="muted small">started {timeAgo(run.startedAt / 1000)}</span><span className="spacer" style={{ flex: 1 }} />
            {!running && <button className="small ghost" onClick={() => { onDismiss(); close(); }}>Dismiss</button>}</div>
          <div className="row small">
            {run.stagesDone.map((s) => <Chip key={s.stage} tone={(s.status ?? (s.ok ? "ok" : "failed")) === "ok" ? "good" : "critical"}>{stageLabel(s.stage)}</Chip>)}
            {run.stage && <Chip tone="running">{stageLabel(run.stage)}</Chip>}
          </div>
          <RunLog run={run} />
        </div>
      )}
    </Popover>
  );
}

export function RunLog({ run }: { run: TrackedRun }) {
  return (
    <div className="run-log" aria-label="Run log">
      {run.events.length === 0 && <span className="muted">Waiting for the first event…</span>}
      {run.events.map((e, i) => (
        <div key={i} className={e.type === "diagnostic" || e.ok === false || e.stream === "stderr" ? "err" : ""}>
          <span className="muted">{e.stage ? `[${e.stage}] ` : ""}</span>
          {e.type === "log" ? (e.line ?? e.message) : e.type === "diagnostic" ? `${e.diagnostic?.code}: ${e.diagnostic?.message}`
            : e.type === "stage_started" ? `started${typeof e.index === "number" && e.total ? ` (${e.index + 1}/${e.total})` : ""}`
            : e.type === "stage_finished" ? `finished: ${e.status ?? (e.ok === false ? "failed" : "ok")}${e.counts?.error ? `, ${e.counts.error} errors` : ""}`
            : e.type === "run_finished" ? `run ${e.status ?? ""}${e.error?.message ? `: ${e.error.message}` : ""}`
            : e.type === "progress" ? (e.message ?? "progress") : humanize(e.type)}
        </div>
      ))}
      {run.error && <div className="err">stream error: {run.error}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ status summary

export function outcomeText(s: StageState): string {
  switch (s.outcome) {
    case "failed": return `Failed · ${countsText(s.counts)}`;
    case "passed": return s.counts.warning ? `Passed · ${countsText(s.counts)}` : "Passed";
    case "running": return "Running…";
    case "unavailable": return "Unavailable here";
    default: return "Not run";
  }
}

/** Stage rows linking to their evidence: outcome, freshness (separately), and a rerun action. */
export function StageList({ pid, stages, onRerun, busy, onNavigate, extra, countsLink }: {
  pid: string; stages: StageState[]; onRerun?: (stage: string) => void; busy?: boolean; onNavigate?: () => void;
  /** link the error/warning counts to the stage's diagnostics */
  countsLink?: boolean;
  /** per-stage annotation, e.g. an environment problem that affects the stage */
  extra?: (s: StageState) => React.ReactNode;
}) {
  return (
    <div className="stage-list" data-testid="stage-list">
      {stages.map((s) => {
        const note = extra?.(s);
        return (
          <div key={s.stage} className={`stage-row ${s.outcome}`}>
            <span className="mark" aria-hidden="true"><Icon name={s.outcome === "passed" ? "check" : s.outcome === "failed" ? "x" : s.outcome === "running" ? "spinner" : "dot"} size={13} /></span>
            <div className="main">
              <div className="top">
                <Link to={`/p/${encodeURIComponent(pid)}/${s.page}`} onClick={onNavigate} className="label">{s.label}</Link>
                {countsLink && (s.counts.error || s.counts.warning)
                  ? <Link className="outcome" to={`/p/${encodeURIComponent(pid)}/diagnostics?stage=${encodeURIComponent(s.stage)}`}>{outcomeText(s)}</Link>
                  : <span className="outcome">{outcomeText(s)}</span>}
              </div>
              {(s.outdated || s.startedAt || note) && (
                <div className="sub">
                  {s.outdated && <span className="tag outdated"><Icon name="clock" size={11} />Outdated · inputs changed</span>}
                  {s.startedAt && <span>ran {timeAgo(s.startedAt)}</span>}
                  {note}
                </div>
              )}
            </div>
            {onRerun && s.stage !== "report" ? (
              <button className="small ghost" disabled={busy} onClick={() => onRerun(s.stage)} aria-label={`Rerun ${s.label}`}>
                {s.outcome === "not_run" ? "Run" : "Rerun"}
              </button>
            ) : <span />}
          </div>
        );
      })}
    </div>
  );
}

function revisionRows(p: ProjectDetail): [string, string | null | undefined][] {
  return [
    ["robot.yaml", p.fingerprint.robot_yaml_sha256],
    ["Git commit", p.git?.commit ?? p.fingerprint.git_commit],
    ["Git branch", p.git?.branch],
    ["CAD revision", p.fingerprint.cad_revision],
    ["Project folder", p.project],
  ];
}

function StatusSummary({ project, runningStage, onRerun, busy }: {
  project: ProjectDetail; runningStage: string | null; onRerun: (stage: string) => void; busy: boolean;
}) {
  const status = projectStatus(project, runningStage);
  return (
    <Popover label="Project status" className="status-summary" trigger={(open) => (
      <span className={`status-pill ${status.tone}`} data-testid="status-summary">
        <span className="dot" />
        <strong>{status.headline}</strong>
        {status.parts.map((p) => <span key={p} className="part">· {p}</span>)}
        <Icon name="chevron" size={12} className={open ? "flip" : ""} />
      </span>
    )}>
      {(close) => (
        <div className="stack status-panel">
          <StageList pid={project.id} stages={status.stages} onRerun={(s) => { onRerun(s); }} busy={busy} onNavigate={close} />
          <Details summary="Revision details" rows={revisionRows(project)} />
        </div>
      )}
    </Popover>
  );
}

// ------------------------------------------------------------------ project layout

export function ProjectLayout() {
  const { pid = "" } = useParams();
  const project = useProject(pid);
  const { runs, active, start, dismiss } = useProjectRuns(pid);
  const location = useLocation();
  const page = location.pathname.split("/").pop() ?? "";
  const flush = page === "3d" || page === "diagnostics";
  const [starting, setStarting] = useState(false);
  const latest = active ?? runs[0] ?? null;
  const p = project.data;
  const run = (stages?: string[]) => { setStarting(true); void start(stages ? { stages } : {}).finally(() => setStarting(false)); };
  return (
    <>
      <header className="topbar">
        <MenuButton />
        <div className="title">
          <h1 className="ellipsis">{p?.robot.name ?? pid}</h1>
          <span className="meta ellipsis">{p?.robot.version ? `v${p.robot.version}` : ""}{p?.git?.branch ? ` · ${p.git.branch}` : ""}</span>
        </div>
        {p && <StatusSummary project={p} runningStage={active?.stage ?? null} onRerun={(s) => run([s])} busy={!!active || starting} />}
        <div className="spacer" />
        {latest && <RunStatus run={latest} onDismiss={() => dismiss(latest.runId)} />}
        {/* The page's own action is primary (Deploy, Send, Build PCB); Run pipeline is primary only on the Overview. */}
        <button className={`run-pipeline${page === "overview" ? " primary" : ""}`} disabled={!!active || starting} onClick={() => run()} aria-label="Run pipeline">
          <Icon name="play" size={14} /><span className="label">Run pipeline</span>
        </button>
      </header>
      <div className={`content${flush ? " flush" : ""}`}>
        <Outlet />
      </div>
    </>
  );
}
