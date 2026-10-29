import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { agentProviders, api } from "../api/client.ts";
import { invalidateProject, keys, useDoctor, useSpec } from "../api/hooks.ts";
import { streamEvents, type StreamHandle } from "../api/sse.ts";
import type { AgentEvent, AgentProvider } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, Empty, ErrorBox, Segmented } from "../components/ui.tsx";
import { diffHunks, diffLines, parseUnifiedDiff, type DiffLine } from "../lib/domain.ts";

const EXAMPLES = [
  "Make the battery lead shorter and re-run wiring and electrical.",
  "Fix the BEC overload at servo stall without changing the ESC.",
  "Route the motor leads away from the rear wheels so clearance is at least 8 mm.",
  "Design a robot that follows a line at 2 m/s using the same Pi + HAT.",
];

interface Session {
  id: string;
  prompt: string;
  provider: AgentProvider;
  events: AgentEvent[];
  status: string;
  baseline: string;
}

type TranscriptEntry =
  | { kind: "event"; event: AgentEvent }
  | { kind: "tool"; call: AgentEvent; result: AgentEvent | null };

/** Pairs each tool_call with its tool_result (by id) so the transcript reads call → result. */
export function transcriptEntries(events: readonly AgentEvent[]): TranscriptEntry[] {
  const out: TranscriptEntry[] = [];
  const calls = new Map<string, { kind: "tool"; call: AgentEvent; result: AgentEvent | null }>();
  for (const event of events) {
    if (event.type === "tool_call") {
      const entry = { kind: "tool" as const, call: event, result: null };
      if (event.id) calls.set(event.id, entry);
      out.push(entry);
    } else if (event.type === "tool_result" && event.id && calls.has(event.id)) {
      calls.get(event.id)!.result = event;
    } else if (!["usage", "raw"].includes(event.type)) {
      out.push({ kind: "event", event });
    }
  }
  return out;
}

function text(value: unknown): string {
  if (typeof value === "string") {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function ToolEntry({ call, result }: { call: AgentEvent; result: AgentEvent | null }) {
  return (
    <details className="tool">
      <summary>
        <Icon name={!result ? "spinner" : result.ok === false ? "x" : "check"} size={13} />
        <strong className="mono">{call.name}</strong>
        <span className="muted">{call.server ? call.server : "built-in"}</span>
        {result && result.ok === false && <Chip tone="critical">error</Chip>}
      </summary>
      <pre className="code">{text(call.input)}</pre>
      {result && <pre className="code" style={{ borderTop: "1px solid var(--line)" }}>{text(result.content)}</pre>}
    </details>
  );
}

function EventEntry({ event }: { event: AgentEvent }) {
  switch (event.type) {
    case "message":
      if (event.role === "thinking") return <div className="small muted" style={{ whiteSpace: "pre-wrap" }}>💭 {event.text}</div>;
      if (event.role === "error") return <ErrorBox error={new Error(event.text ?? "agent error")} />;
      if (event.role === "output" || event.role === "system") return <div className="small muted mono" style={{ whiteSpace: "pre-wrap" }}>{event.text}</div>;
      return <div className={`msg-bubble ${event.role === "user" ? "user" : ""}`}>{event.text}</div>;
    case "run_started":
      return <div className="row small"><Icon name="play" size={13} />Run via <code>{event.tool}</code>{event.stages?.length ? <span className="muted">{event.stages.join(", ")}</span> : null}<Chip tone="running">running</Chip></div>;
    case "run_finished":
      return (
        <div className="row small"><Icon name={event.ok ? "check" : "x"} size={13} />Run via <code>{event.tool}</code> finished
          <Chip tone={event.ok ? "good" : "critical"}>{event.ok ? "ok" : "failed"}</Chip>
          {event.counts?.error ? <span className="muted">{event.counts.error} errors</span> : null}</div>
      );
    case "spec_changed":
      return <div className="row small"><Chip tone="info">robot.yaml changed</Chip><code className="muted">{event.previous_sha256?.slice(0, 10)} → {event.sha256?.slice(0, 10)}</code></div>;
    case "session_started":
      return <div className="small muted">Session started · <code>{Array.isArray(event.command) ? event.command.slice(0, 4).join(" ") : String(event.command ?? event.provider ?? "")}</code>
        {event.cli_version ? <> · {String(event.cli_version)}</> : null}</div>;
    case "stderr":
      return <div className="small mono" style={{ color: "var(--critical)" }}>{event.line}</div>;
    case "session_finished":
      return <div className="small muted">Session {event.status}{event.error ? `: ${event.error}` : ""}{typeof event.returncode === "number" ? ` (exit ${event.returncode})` : ""}</div>;
    default:
      return <div className="small muted mono">{event.type}{event.text ? `: ${event.text}` : ""}</div>;
  }
}

export function AgentPage() {
  const { pid = "" } = useParams();
  const spec = useSpec(pid);
  const doctor = useDoctor();
  const client = useQueryClient();
  const [provider, setProvider] = useState<AgentProvider>("claude");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<unknown>(null);
  const stream = useRef<StreamHandle | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => () => stream.current?.close(), []);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [session?.events.length]);

  const send = async () => {
    setError(null);
    try {
      const baseline = spec.data?.yaml ?? "";
      const { session_id } = await api.startAgent(pid, { prompt, provider, ...(model ? { model } : {}) });
      setSession({ id: session_id, prompt, provider, events: [{ type: "message", role: "user", text: prompt }], status: "starting", baseline });
      setPrompt("");
      stream.current?.close();
      stream.current = streamEvents<AgentEvent>(api.agentEventsUrl(session_id), (event) => {
        setSession((s) => (s && s.id === session_id ? {
          ...s, events: [...s.events, event],
          status: event.type === "session_finished" ? event.status ?? "completed" : event.type === "session_started" ? "running" : s.status,
        } : s));
        if (event.type === "spec_changed") void client.invalidateQueries({ queryKey: keys.section(pid, "spec") });
        if (event.type === "spec_changed" || event.type === "run_finished" || event.type === "session_finished") void invalidateProject(client, pid);
      }, (e) => setError(e));
    } catch (e) {
      setError(e);
    }
  };

  const cancel = async () => {
    if (!session) return;
    try { await api.cancelAgent(session.id); } catch (e) { setError(e); }
  };

  const entries = useMemo(() => transcriptEntries(session?.events ?? []), [session?.events]);
  const lines = useMemo<(DiffLine | null)[]>(() => {
    if (!session) return [];
    // The API sends the cumulative unified diff since the session started; fall back to a local diff.
    const latest = [...session.events].reverse().find((e) => typeof e.cumulative_diff === "string");
    if (latest) return parseUnifiedDiff(latest.cumulative_diff as string);
    return spec.data ? diffHunks(diffLines(session.baseline, spec.data.yaml)) : [];
  }, [session, spec.data]);
  const changes = lines.filter((l) => l && l.op !== " ").length;
  const info = agentProviders(doctor.data).find((p) => p.provider === provider);
  const running = session?.status === "running" || session?.status === "starting";
  const usage = [...(session?.events ?? [])].reverse().find((e) => e.type === "usage");

  return (
    <div className="page">
      <div className="page-head">
        <div className="stack" style={{ gap: 2 }}>
          <h1>Agent</h1>
          <p>Claude Code or Codex, run headlessly through their own CLIs in the project directory, editing only through the R3 MCP tools.</p>
        </div>
      </div>
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <Card title="Session" subtitle={session ? `${session.provider} · ${session.id}` : "Describe the change or the robot"}
              actions={session && <div className="row">
                {usage && <span className="small muted">{usage.input_tokens ?? 0} in / {usage.output_tokens ?? 0} out tokens{usage.cost_usd ? ` · $${usage.cost_usd.toFixed(3)}` : ""}</span>}
                <Chip tone={running ? "running" : session.status === "completed" ? "good" : session.status === "failed" ? "critical" : "muted"}>{session.status}</Chip>
              </div>}>
          <div className="stack" style={{ gap: 12 }}>
            <div className="transcript" style={{ maxHeight: 520, overflowY: "auto" }} data-testid="transcript">
              {!session && <Empty icon="sparkle" title="No session yet">Pick a provider and send a prompt. Runs the agent starts refresh every page when they finish.</Empty>}
              {entries.map((entry, i) => entry.kind === "tool"
                ? <ToolEntry key={i} call={entry.call} result={entry.result} /> : <EventEntry key={i} event={entry.event} />)}
              <div ref={bottom} />
            </div>
            {error ? <ErrorBox error={error} /> : null}
            <div className="row">
              <Segmented label="Provider" value={provider} onChange={setProvider} options={[{ value: "claude", label: "Claude Code" }, { value: "codex", label: "Codex" }]} />
              <input type="text" placeholder="model (optional)" value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 170 }} aria-label="Model" />
              {info && !info.available && <Chip tone="warning" title={info.detail}>{provider} CLI unavailable</Chip>}
              {info?.available && <Chip tone="good">{info.version ?? "available"}</Chip>}
            </div>
            {info && !info.available && info.detail && <span className="small muted">{info.detail}</span>}
            <textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder='e.g. "make the battery lead shorter"' aria-label="Prompt"
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && prompt.trim() && !running) void send(); }} />
            <div className="row">
              {EXAMPLES.map((x) => <button key={x} className="small ghost" onClick={() => setPrompt(x)} style={{ whiteSpace: "normal", textAlign: "left" }}>{x}</button>)}
            </div>
            <div className="row">
              <button className="primary" disabled={!prompt.trim() || running} onClick={() => void send()}><Icon name="sparkle" size={14} />Send</button>
              {running && <button className="danger" onClick={() => void cancel()}><Icon name="stop" size={12} />Cancel</button>}
              <span className="small muted">⌘/Ctrl + Enter</span>
            </div>
          </div>
        </Card>
        <Card title="robot.yaml diff" subtitle={session ? `${changes} changed line(s) since the session started` : "Live diff of the spec while the agent works"} flush>
          {!session ? <Empty title="Nothing to compare yet" /> : changes === 0 ? <Empty title="No changes to robot.yaml yet" /> : (
            <div className="diff" data-testid="spec-diff">
              {lines.map((l, i) => l === null ? <div key={i} className="gap">⋯</div> : (
                <div key={i} className={l.op === "+" ? "add" : l.op === "-" ? "del" : ""}>
                  <span className="ln">{l.op === "+" ? "" : l.a}</span><span className="ln">{l.op === "-" ? "" : l.b}</span>{l.op} {l.text}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
