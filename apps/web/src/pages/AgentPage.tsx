import { useQueryClient } from "@tanstack/react-query";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { agentProviders, api } from "../api/client.ts";
import { invalidateProject, keys, useAgentSessions, useDiagnostics, useDoctor, useSpec } from "../api/hooks.ts";
import { streamEvents, type StreamHandle } from "../api/sse.ts";
import type { AgentEvent, AgentProvider, AgentStatus, SpecDocument } from "../api/types.ts";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { Details, ErrorBox, Segmented, Tabs } from "../components/ui.tsx";
import { AGENT_NAMES } from "../lib/environment.ts";
import { diagnosticHeadline, plural } from "../lib/labels.ts";
import { parseUnifiedDiff, specDiffFiles, unifiedDiff, type DiffLine } from "../lib/domain.ts";
import { timeAgo } from "../lib/units.ts";

interface Session {
  id: string;
  prompt: string;
  provider: string;
  model: string | null;
  events: AgentEvent[];
  status: string;
  baseline: Record<string, string> | null;
}

function descriptionFiles(document: SpecDocument): Record<string, string> {
  return document.files ? Object.fromEntries(Object.entries(document.files).map(([name, source]) => [name, source.yaml]))
    : { "robot.yaml": document.yaml };
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

/** What a session achieved: description lines changed, checks run, errors left. */
export function sessionOutcome(events: readonly AgentEvent[]): { changedLines: number; runs: number; failedRuns: number; errorsRemaining: number | null; diff: string | null } {
  const diff = [...events].reverse().find((e) => typeof e.cumulative_diff === "string")?.cumulative_diff as string | undefined;
  const changedLines = diff ? parseUnifiedDiff(diff).filter((l) => l && l.op !== " ").length : 0;
  const runs = events.filter((e) => e.type === "run_finished");
  const lastRun = runs[runs.length - 1];
  return { changedLines, runs: runs.length, failedRuns: runs.filter((r) => r.ok === false).length, errorsRemaining: lastRun?.counts?.error ?? null, diff: diff ?? null };
}

function text(value: unknown): string {
  if (typeof value === "string") {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

const ToolEntry = memo(function ToolEntry({ call, result }: { call: AgentEvent; result: AgentEvent | null }) {
  return (
    <details className="tool">
      <summary>
        <Icon name={!result ? "spinner" : result.ok === false ? "x" : "check"} size={13} />
        <span>{(call.name ?? "tool").replace(/^mcp__r3__/, "").replace(/_/g, " ")}</span>
        {result && result.ok === false && <strong style={{ color: "var(--critical)" }}>failed</strong>}
      </summary>
      <pre className="code">{text(call.input)}</pre>
      {result && <Markdown className="tool-result">{result.content ?? ""}</Markdown>}
    </details>
  );
});

const EventEntry = memo(function EventEntry({ event }: { event: AgentEvent }) {
  switch (event.type) {
    case "message":
      if (event.role === "thinking") return <Markdown className="small muted">{event.text ?? ""}</Markdown>;
      if (event.role === "error") return <ErrorBox error={new Error(event.text ?? "agent error")} />;
      if (event.role === "output" || event.role === "system") return <Markdown className="small muted">{event.text ?? ""}</Markdown>;
      return <Markdown className={`msg-bubble ${event.role === "user" ? "user" : ""}`}>{event.text ?? ""}</Markdown>;
    case "tool_result":
      return <Markdown className="tool-result">{event.content ?? ""}</Markdown>;
    case "run_started":
      return <div className="row small muted"><Icon name="play" size={12} />Running checks{event.stages?.length ? `: ${event.stages.join(", ")}` : ""}…</div>;
    case "run_finished":
      return (
        <div className="row small"><Icon name={event.ok ? "check" : "x"} size={12} />Checks {event.ok ? "passed" : "failed"}
          {event.counts?.error ? <span className="muted">· {plural(event.counts.error, "error")} remaining</span> : null}</div>
      );
    case "spec_changed":
      return <div className="row small"><Icon name="code" size={12} />Edited robot description</div>;
    case "stderr":
      return <div className="small mono" style={{ color: "var(--critical)" }}>{event.line}</div>;
    case "session_started":
    case "session_finished":
      return null;
    default:
      return null;
  }
});

function Outcome({ session }: { session: Session }) {
  const o = sessionOutcome(session.events);
  const finished = session.events.find((e) => e.type === "session_finished");
  const started = session.events.find((e) => e.type === "session_started");
  const usage = [...session.events].reverse().find((e) => e.type === "usage");
  if (!finished) return null;
  const failed = finished.status === "failed" || !!finished.error;
  return (
    <div className={`agent-result${failed ? " failed" : ""}`} data-testid="agent-outcome">
      <div className="summary-line">
        <strong>{failed ? "The agent stopped with an error" : finished.status === "cancelled" ? "Cancelled" : "Done"}</strong>
        <span>{o.changedLines ? `changed ${plural(o.changedLines, "line")} of the description` : "description unchanged"}</span>
        <span>{o.runs ? `${plural(o.runs, "check run")}${o.failedRuns ? `, ${o.failedRuns} failed` : ", all passed"}` : "no checks run"}</span>
        {o.errorsRemaining !== null && <span>{o.errorsRemaining ? `${plural(o.errorsRemaining, "error")} remaining` : "no errors remaining"}</span>}
      </div>
      {finished.error && <Markdown className="small">{finished.error}</Markdown>}
      <Details rows={[
        ["Session", session.id], ["Agent", AGENT_NAMES[session.provider] ?? session.provider], ["Model", (started?.model as string | undefined) ?? null],
        ["Command", Array.isArray(started?.command) ? started.command.join(" ") : (started?.command as string | undefined) ?? null],
        ["Exit code", typeof finished.returncode === "number" ? String(finished.returncode) : null],
        ["Tokens", usage ? `${usage.input_tokens ?? 0} in / ${usage.output_tokens ?? 0} out${usage.cost_usd ? ` · $${usage.cost_usd.toFixed(3)}` : ""}` : null],
      ]} />
    </div>
  );
}

function DiffView({ lines }: { lines: (DiffLine | null)[] }) {
  return (
    <div className="diff" data-testid="spec-diff">
      {lines.map((l, i) => l === null ? <div key={i} className="gap">⋯</div> : (
        <div key={i} className={l.op === "+" ? "add" : l.op === "-" ? "del" : ""}>
          <span className="ln">{l.op === "+" ? "" : l.a}</span><span className="ln">{l.op === "-" ? "" : l.b}</span>{l.op} {l.text}
        </div>
      ))}
    </div>
  );
}

export function AgentPage() {
  const { pid = "" } = useParams();
  const spec = useSpec(pid);
  const doctor = useDoctor();
  const diagnostics = useDiagnostics(pid);
  const history = useAgentSessions(pid);
  const client = useQueryClient();
  const [provider, setProvider] = useState<AgentProvider>("claude");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [thread, setThread] = useState<Session[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [tab, setTab] = useState<"chat" | "changes">("chat");
  const [split, setSplit] = useState(58);
  const streams = useRef(new Map<string, StreamHandle>());
  const bottom = useRef<HTMLDivElement>(null);
  const layout = useRef<HTMLDivElement>(null);

  useEffect(() => () => { for (const s of streams.current.values()) s.close(); }, []);
  const eventCount = thread.reduce((n, s) => n + s.events.length, 0);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [eventCount]);

  const follow = useCallback((session_id: string) => {
    streams.current.get(session_id)?.close();
    streams.current.set(session_id, streamEvents<AgentEvent>(api.agentEventsUrl(session_id), (event) => {
      setThread((list) => list.map((s) => (s.id === session_id ? {
        ...s, events: [...s.events, event],
        status: event.type === "session_finished" ? event.status ?? "completed" : event.type === "session_started" ? "running" : s.status,
      } : s)));
      if (event.type === "spec_changed") void client.invalidateQueries({ queryKey: keys.section(pid, "spec") });
      if (event.type === "spec_changed" || event.type === "run_finished" || event.type === "session_finished") void invalidateProject(client, pid);
    }, (e) => setError(e)));
  }, [client, pid]);

  const current = thread[thread.length - 1] ?? null;
  const running = current?.status === "running" || current?.status === "starting";

  const send = async (mode: "new" | "reply") => {
    setError(null);
    const visible = prompt;
    const previous = mode === "reply" ? current : null;
    try {
      const baseline = spec.data ? descriptionFiles(spec.data) : null;
      const request = previous
        ? { prompt: visible, provider: previous.provider as AgentProvider, ...(previous.model ? { model: previous.model } : {}), resume_session_id: previous.id }
        : { prompt: visible, provider, ...(model ? { model } : {}) };
      const { session_id } = await api.startAgent(pid, request);
      const session: Session = { id: session_id, prompt: visible, provider: request.provider, model: request.model ?? null, events: [{ type: "message", role: "user", text: visible }], status: "starting", baseline };
      setThread((list) => (mode === "reply" ? [...list, session] : [session]));
      setPrompt("");
      follow(session_id);
      void client.invalidateQueries({ queryKey: keys.section(pid, "agent") });
    } catch (e) {
      setError(e);
    }
  };

  const open = (s: AgentStatus) => {
    for (const h of streams.current.values()) h.close();
    streams.current.clear();
    setThread([{ id: s.session_id, prompt: s.prompt, provider: s.provider, model: s.model, events: [{ type: "message", role: "user", text: s.prompt }], status: s.status, baseline: null }]);
    follow(s.session_id);
  };

  const cancel = async () => {
    if (!current) return;
    try { await api.cancelAgent(current.id); } catch (e) { setError(e); }
  };

  const files = useMemo(() => {
    if (!thread.length) return [];
    // A restored history item has no browser-side baseline. Show the current
    // execution's authoritative diff rather than hiding its changes or inventing
    // a comparison against an unavailable earlier description.
    if (thread.length === 1 || !thread[0]!.baseline) {
      const diff = sessionOutcome(thread[thread.length - 1]!.events).diff;
      if (diff) return specDiffFiles(diff);
    }
    const base = thread[0]!.baseline;
    if (!base || !spec.data) return [];
    const current = descriptionFiles(spec.data);
    return specDiffFiles([...new Set([...Object.keys(base), ...Object.keys(current)])].sort()
      .map((name) => unifiedDiff(base[name] ?? "", current[name] ?? "", name)).join(""));
  }, [thread, spec.data]);
  const changes = files.flatMap((f) => f.lines).filter((l) => l && l.op !== " ").length;
  const info = agentProviders(doctor.data).find((p) => p.provider === provider);
  // Suggest the most specific errors: codes that occur once or twice (a brownout) before mass findings (34 bend radii).
  const suggestions = useMemo(() => {
    const byCode = new Map<string, NonNullable<typeof diagnostics.data>["diagnostics"]>();
    for (const d of diagnostics.data?.diagnostics ?? []) if (d.severity === "error") (byCode.get(d.code) ?? byCode.set(d.code, []).get(d.code)!).push(d);
    return [...byCode.values()].sort((a, b) => a.length - b.length).slice(0, 2).map((list) => list[0]!);
  }, [diagnostics.data]);
  const past = (history.data ?? []).slice().sort((a, b) => b.created_at - a.created_at);

  const dragSplit = (e: React.PointerEvent) => {
    const box = layout.current?.getBoundingClientRect();
    if (!box) return;
    const move = (ev: PointerEvent) => setSplit(Math.min(80, Math.max(30, ((ev.clientX - box.left) / box.width) * 100)));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    e.preventDefault();
  };

  const composer = (
    <div className={`composer${thread.length ? " docked" : ""}`}>
      <textarea rows={thread.length ? 2 : 4} value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Prompt"
                placeholder={thread.length ? "Reply with a follow-up, or describe a new task" : "e.g. Shorten the battery lead and keep 8 mm clearance from the rear wheels"}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && prompt.trim() && !running) void send(thread.length ? "reply" : "new"); }} />
      <div className="row">
        <Segmented label="Agent" value={provider} onChange={setProvider} options={[{ value: "claude", label: "Claude Code" }, { value: "codex", label: "Codex" }]} />
        {info && !info.available && <span className="small muted" title={info.detail}>{AGENT_NAMES[provider]} is not available here · <Link to="/environment">set up</Link></span>}
        <span className="spacer" style={{ flex: 1 }} />
        {running && <button className="danger" onClick={() => void cancel()}><Icon name="stop" size={12} />Stop</button>}
        {thread.length > 0 && <button disabled={!prompt.trim() || running} onClick={() => void send("new")}>New task</button>}
        <button className="primary" disabled={!prompt.trim() || running} onClick={() => void send(thread.length ? "reply" : "new")}>
          <Icon name="sparkle" size={14} />{thread.length ? "Reply" : "Send"}
        </button>
      </div>
      <Details summary="Options">
        <label className="row small"><span className="muted">Model</span>
          <input type="text" placeholder="the agent's default" value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 200 }} aria-label="Model" /></label>
      </Details>
    </div>
  );

  return (
    <div className={`agent-page${past.length || thread.length ? "" : " no-history"}`}>
      {(past.length > 0 || thread.length > 0) && <aside className="agent-history" aria-label="Agent history">
        <button className="small" onClick={() => { setThread([]); setPrompt(""); }} disabled={running}><Icon name="plus" size={12} />New task</button>
        <div className="list">
          {past.map((s) => (
            <button key={s.session_id} className={`list-row${thread[0]?.id === s.session_id ? " selected" : ""}`} onClick={() => open(s)}>
              <span className="stack" style={{ gap: 0, minWidth: 0 }}>
                <span className="ellipsis">{s.prompt.split("\n").pop()}</span>
                <span className="muted small">{s.status} · {timeAgo(s.created_at)}</span>
              </span>
            </button>
          ))}
          {!past.length && <span className="muted small" style={{ padding: "6px 2px" }}>No earlier tasks.</span>}
        </div>
      </aside>}

      {!thread.length ? (
        <div className="agent-start">
          <h1>What should change?</h1>
          <p className="secondary">Describe a robot change. The agent edits the robot description and runs the checks.</p>
          {composer}
          {error ? <ErrorBox error={error} /> : null}
          {suggestions.length > 0 && (
            <div className="suggestions">
              <span className="muted small">Suggestions from current errors</span>
              {suggestions.map((d, i) => (
                <button key={i} className="ghost small suggestion" onClick={() => setPrompt(`Fix this ${d.stage} error: ${diagnosticHeadline(d)}${d.hint ? ` (${d.hint})` : ""}`)}>
                  <Markdown inline>{diagnosticHeadline(d)}</Markdown>
                </button>
              ))}
              <Link className="small" to={`/p/${encodeURIComponent(pid)}/diagnostics`}>All diagnostics</Link>
            </div>
          )}
        </div>
      ) : (
        <div className="agent-session">
          {changes > 0 && <Tabs label="Agent view" value={tab} onChange={setTab} options={[{ value: "chat", label: "Conversation" }, { value: "changes", label: `${thread.length > 1 && !thread[0]!.baseline ? "Latest changes" : "Changes"} · ${changes}` }]} />}
          <div className={`agent-split show-${tab}${changes > 0 ? " with-changes" : ""}`} ref={layout} style={{ "--split": `${split}%` } as React.CSSProperties}>
            <div className="agent-chat">
              <div className="transcript" data-testid="transcript">
                {thread.map((s) => (
                  <div key={s.id} className="stack" style={{ gap: 10 }}>
                    {transcriptEntries(s.events).map((entry, i) => entry.kind === "tool"
                      ? <ToolEntry key={i} call={entry.call} result={entry.result} /> : <EventEntry key={i} event={entry.event} />)}
                    {s.status === "running" || s.status === "starting" ? <div className="row small muted"><Icon name="spinner" size={12} />Working…</div> : <Outcome session={s} />}
                  </div>
                ))}
                <div ref={bottom} />
              </div>
              {error ? <ErrorBox error={error} /> : null}
              {composer}
            </div>
            {changes > 0 && (
              <>
                <div className="splitter" role="separator" aria-orientation="vertical" aria-label="Resize" onPointerDown={dragSplit} />
                <div className="agent-changes">
                  <div className="changes-head"><strong>Robot description</strong><span className="muted small">{plural(changes, "changed line")}</span></div>
                  {files.map((f) => <div key={f.file}>
                    <div className="changes-head"><strong>{f.file}</strong></div>
                    <DiffView lines={f.lines} />
                  </div>)}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
