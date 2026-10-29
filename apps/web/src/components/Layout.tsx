import { useState } from "react";
import { NavLink, Outlet, useLocation, useParams } from "react-router-dom";

import { useDiagnostics, useProject } from "../api/hooks.ts";
import { severityCounts } from "../lib/domain.ts";
import { humanize } from "../lib/units.ts";
import { useMockBackend, useTheme } from "../state/prefs.ts";
import { useProjectRuns, type TrackedRun } from "../state/runs.tsx";
import { Icon, type IconName } from "./icons.tsx";
import { Chip, StageChip } from "./ui.tsx";

const PROJECT_NAV: { to: string; label: string; icon: IconName }[] = [
  { to: "overview", label: "Overview", icon: "overview" },
  { to: "3d", label: "3D", icon: "cube" },
  { to: "wiring", label: "Wiring", icon: "wire" },
  { to: "electrical", label: "Electrical", icon: "bolt" },
  { to: "pcb", label: "PCB", icon: "chip" },
  { to: "software", label: "Software", icon: "code" },
  { to: "sim", label: "Simulation", icon: "play" },
  { to: "diagnostics", label: "Diagnostics", icon: "pulse" },
  { to: "agent", label: "Agent", icon: "sparkle" },
  { to: "deploy", label: "Deploy", icon: "rocket" },
  { to: "settings", label: "Settings", icon: "gear" },
];

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
  return (
    <div className="shell">
      <aside className="sidebar">
        <NavLink to="/" className="brand">
          <span className="brand-mark">R3</span>
          <span>R3<small>robot design studio</small></span>
        </NavLink>
        <nav className="nav-section nav" aria-label="Workspace">
          <NavLink to="/" end className={() => (location.pathname === "/" ? "active" : "")}><Icon name="folder" />Projects</NavLink>
          {!pid && <NavLink to="/settings"><Icon name="stethoscope" />Doctor</NavLink>}
        </nav>
        {pid && <ProjectNav pid={pid} />}
        <div className="sidebar-foot">
          <div className="row small">
            {mock === null ? <Chip tone="muted">connecting</Chip> : mock ? <Chip tone="warning" title="Vite dev mock: serving build/ artifacts">mock API</Chip>
              : <Chip tone="good">r3 serve</Chip>}
          </div>
          <button className="small" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">
            <Icon name={theme === "dark" ? "sun" : "moon"} size={14} />{theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
        </div>
      </aside>
      <div className="main">
        {mock && (
          <div className="banner" role="status">
            <Icon name="info" size={14} />
            <span><strong>Mock API.</strong> <code>r3 serve</code> is not reachable, so the dev server answers from the projects' <code>build/</code> artifacts.
              Runs replay existing results; spec edits stay in memory.</span>
          </div>
        )}
        <Outlet />
      </div>
    </div>
  );
}

function RunStatus({ run, onDismiss }: { run: TrackedRun; onDismiss: () => void }) {
  const [open, setOpen] = useState(false);
  const running = run.status === "queued" || run.status === "running";
  return (
    <div style={{ position: "relative" }}>
      <button className="small" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {running ? <Chip tone="running">{run.stage ? `${run.stage}…` : run.status} {Math.round(run.progress * 100)}%</Chip>
          : <Chip tone={run.status === "succeeded" ? "good" : run.status === "cancelled" ? "muted" : "critical"}>run {run.status}</Chip>}
      </button>
      {open && (
        <div className="card" style={{ position: "absolute", right: 0, top: 34, width: 520, zIndex: 20, boxShadow: "var(--shadow)" }}>
          <div className="card-head"><h2>Run {run.runId}</h2><div className="spacer" />{!running && <button className="small ghost" onClick={onDismiss}>Dismiss</button>}</div>
          <div className="card-body stack">
            <div className="row">{run.stagesDone.map((s) => <StageChip key={s.stage} stage={s.stage} status={s.status ?? (s.ok ? "ok" : "failed")} />)}
              {run.stage && <StageChip stage={run.stage} status="running" />}</div>
            <RunLog run={run} />
          </div>
        </div>
      )}
    </div>
  );
}

export function RunLog({ run }: { run: TrackedRun }) {
  return (
    <div className="run-log" aria-label="Run log">
      {run.events.length === 0 && <span className="muted">waiting for events…</span>}
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

export function ProjectLayout() {
  const { pid = "" } = useParams();
  const project = useProject(pid);
  const { runs, active, start, dismiss } = useProjectRuns(pid);
  const location = useLocation();
  const flush = /\/(3d|diagnostics)$/.test(location.pathname);
  const [starting, setStarting] = useState(false);
  const latest = active ?? runs[0] ?? null;
  return (
    <>
      <header className="topbar">
        <div className="title">
          <h1>{project.data?.robot.name ?? pid}</h1>
          <span className="ellipsis" style={{ maxWidth: 520 }} title={project.data?.project}>
            {pid}{project.data?.git?.branch ? ` · ${project.data.git.branch}` : ""} · inputs <code>{project.data?.fingerprint.robot_yaml_sha256 ?? "…"}</code>
          </span>
        </div>
        <div className="row" aria-label="Stage status">
          {project.data?.stages.filter((s) => s.stage !== "report").map((s) => (
            <StageChip key={s.stage} stage={s.stage} status={active?.stage === s.stage ? "running" : s.stale && s.status !== "missing" ? "stale" : s.status} />
          ))}
        </div>
        <div className="spacer" />
        {latest && <RunStatus run={latest} onDismiss={() => dismiss(latest.runId)} />}
        <button className="primary" disabled={!!active || starting}
                onClick={() => { setStarting(true); void start({}).finally(() => setStarting(false)); }}>
          <Icon name="play" size={14} />Run pipeline
        </button>
      </header>
      <div className={`content${flush ? " flush" : ""}`}>
        <Outlet />
      </div>
    </>
  );
}
