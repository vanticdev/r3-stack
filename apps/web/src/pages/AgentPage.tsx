import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api/client.ts";
import { invalidateProject, keys, useDoctor, useSpec } from "../api/hooks.ts";
import { streamEvents, type StreamHandle } from "../api/sse.ts";
import type { AgentEvent, AgentProvider } from "../api/types.ts";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, Empty, ErrorBox, Segmented } from "../components/ui.tsx";
import { diffHunks, diffLines } from "../lib/domain.ts";
import { useRuns } from "../state/runs.tsx";

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
  status: "running" | "completed" | "cancelled" | "failed";
  baseline: string;
}

function json(value: unknown): string {
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function TranscriptItem({ event, runs }: { event: AgentEvent; runs: ReturnType<typeof useRuns>["runs"] }) {
  switch (event.type) {
    case "message":
      return <div className={`msg-bubble ${event.role === "user" ? "user" : ""}`}>{event.text}</div>;
    case "tool_call":
    case "tool_result": {
      const call = event.type === "tool_call";
      return (
        <details className="tool">
          <summary>
            <Icon name={call ? "code" : event.is_error ? "x" : "check"} size={13} />
            <strong className="mono">{event.tool}</strong>
            <span className="muted">{call ? "call" : "result"}</span>
          </summary>
          <pre className="code">{json(call ? event.input : event.output)}</pre>
        </details>
      );
    }
    case "run_started": {
      const run = runs.find((r) => r.runId === event.run_id);
      return (
        <div className="row small">
          <Icon name="play" size={13} />Run <code>{event.run_id}</code>
          {run && <Chip tone={run.status === "succeeded" ? "good" : run.status === "failed" ? "critical" : "running"}>{run.stage ?? run.status}</Chip>}
        </div>
      );
    }
    case "run_finished":
      return <div className="row small muted"><Icon name="check" size={13} />Run <code>{event.run_id}</code> {event.status}</div>;
    case "spec_changed":
      return <div className="row small"><Chip tone="info">robot.yaml changed</Chip><code className="muted">{event.sha256?.slice(0, 12)}</code></div>;
    case "session_started":
      return <div className="small muted">Session started · <code>{String(event.command ?? event.provider ?? "")}</code>{event.cwd ? <> in <code>{String(event.cwd)}</code></> : null}</div>;
    case "error":
      return <ErrorBox error={new Error(event.text ?? String(event.message ?? "agent error"))} />;
    case "session_finished":
      return <div className="small muted">Session {event.status ?? "finished"}</div>;
    default:
      return <div className="small muted mono">{event.type}{event.text ? `: ${event.text}` : ""}</div>;
  }
}

export function AgentPage() {
  const { pid = "" } = useParams();
  const spec = useSpec(pid);
  const doctor = useDoctor();
  const client = useQueryClient();
  const { runs, follow } = useRuns();
  const [provider, setProvider] = useState<AgentProvider>("claude");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<unknown>(null);
  const stream = useRef<StreamHandle | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => () => stream.current?.close(), []);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [session?.events.length]);

  const send = async () => {
    setError(null);
    try {
      const baseline = spec.data?.yaml ?? "";
      const { session_id } = await api.startAgent(pid, { prompt, provider, ...(model ? { model } : {}) });
      setSession({ id: session_id, prompt, provider, events: [{ type: "message", role: "user", text: prompt }], status: "running", baseline });
      setPrompt("");
      stream.current?.close();
      stream.current = streamEvents<AgentEvent>(api.agentEventsUrl(session_id), (event) => {
        setSession((s) => (s && s.id === session_id ? {
          ...s, events: [...s.events, event],
          status: event.type === "session_finished" ? (event.status === "cancelled" ? "cancelled" : event.status === "failed" ? "failed" : "completed") : s.status,
        } : s));
        if (event.type === "run_started" && event.run_id) follow(pid, event.run_id);
        if (event.type === "spec_changed" || (event.type === "tool_result" && /patch|spec/.test(event.tool ?? ""))) {
          void client.invalidateQueries({ queryKey: keys.section(pid, "spec") });
        }
        if (event.type === "session_finished" || event.type === "run_finished") void invalidateProject(client, pid);
      }, (e) => setError(e));
    } catch (e) {
      setError(e);
    }
  };

  const cancel = async () => {
    if (!session) return;
    try { await api.cancelAgent(session.id); } catch (e) { setError(e); }
  };

  const lines = useMemo(() => (session && spec.data ? diffHunks(diffLines(session.baseline, spec.data.yaml)) : []), [session, spec.data]);
  const changes = lines.filter((l) => l && l.op !== " ").length;
  const available = doctor.data?.agents?.[provider];
  const running = session?.status === "running";

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
              actions={session && <Chip tone={running ? "running" : session.status === "completed" ? "good" : "muted"}>{session.status}</Chip>}>
          <div className="stack" style={{ gap: 12 }}>
            <div className="transcript" style={{ maxHeight: 520, overflowY: "auto" }} data-testid="transcript">
              {!session && <Empty icon="sparkle" title="No session yet">Pick a provider and send a prompt. Runs the agent triggers appear here and refresh every page.</Empty>}
              {session?.events.map((e, i) => <TranscriptItem key={i} event={e} runs={runs} />)}
              <div ref={bottom} />
            </div>
            {error ? <ErrorBox error={error} /> : null}
            <div className="row">
              <Segmented label="Provider" value={provider} onChange={setProvider} options={[{ value: "claude", label: "Claude Code" }, { value: "codex", label: "Codex" }]} />
              <input type="text" placeholder="model (optional)" value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 170 }} aria-label="Model" />
              {available && !available.available && <Chip tone="warning" title={available.detail}>{provider} CLI unavailable</Chip>}
              {available?.available && <Chip tone="good">{available.version ?? "available"}</Chip>}
            </div>
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
