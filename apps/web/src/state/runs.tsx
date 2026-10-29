// Live updates: every run started from the UI (or by an agent session) is followed over SSE;
// project queries are invalidated when a stage or the run finishes.
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api } from "../api/client.ts";
import { invalidateProject } from "../api/hooks.ts";
import { streamEvents, type StreamHandle } from "../api/sse.ts";
import type { RunEvent, RunRequest, RunStatusValue as RunStatus } from "../api/types.ts";

export interface TrackedRun {
  runId: string;
  pid: string;
  request: RunRequest;
  status: RunStatus;
  stage: string | null;
  stagesDone: { stage: string; ok: boolean; status?: string }[];
  /** Stages in this run (from run_queued / run_started), for progress. */
  total: number;
  progress: number;
  events: RunEvent[];
  error?: string;
  startedAt: number;
}

interface RunsContextValue {
  runs: TrackedRun[];
  start: (pid: string, request: RunRequest) => Promise<string>;
  follow: (pid: string, runId: string, request?: RunRequest) => void;
  dismiss: (runId: string) => void;
}

const RunsContext = createContext<RunsContextValue | null>(null);

/** Applies one SSE event to a tracked run (pure; exported for tests). */
export function reduceRun(run: TrackedRun, event: RunEvent): TrackedRun {
  const next: TrackedRun = { ...run, events: [...run.events, event].slice(-500) };
  const stages = Array.isArray(event.stages) ? (event.stages as string[]) : null;
  switch (event.type) {
    case "run_queued":
      return { ...next, total: stages?.length ?? run.total };
    case "run_started":
      return { ...next, status: "running", total: stages?.length ?? run.total };
    case "stage_started": {
      const total = typeof event.total === "number" ? event.total : run.total;
      const index = typeof event.index === "number" ? event.index : run.stagesDone.length;
      return { ...next, status: "running", stage: event.stage ?? null, total, progress: total ? index / total : run.progress };
    }
    case "stage_finished": {
      const done = [...run.stagesDone, { stage: event.stage ?? "?", ok: event.ok !== false, status: event.status }];
      return { ...next, stagesDone: done, progress: run.total ? done.length / run.total : run.progress };
    }
    case "run_finished":
      return { ...next, status: (event.status as RunStatus) ?? (event.ok === false ? "failed" : "succeeded"), stage: null, progress: 1 };
    default:
      return next;
  }
}

export function RunsProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [runs, setRuns] = useState<TrackedRun[]>([]);
  const streams = useRef(new Map<string, StreamHandle>());

  const update = useCallback((runId: string, fn: (run: TrackedRun) => TrackedRun) => {
    setRuns((list) => list.map((r) => (r.runId === runId ? fn(r) : r)));
  }, []);

  const follow = useCallback((pid: string, runId: string, request: RunRequest = {}) => {
    if (streams.current.has(runId)) return;
    setRuns((list) => list.some((r) => r.runId === runId) ? list : [
      { runId, pid, request, status: "queued" as RunStatus, stage: null, stagesDone: [], total: 0, progress: 0, events: [], startedAt: Date.now() },
      ...list,
    ].slice(0, 12));
    const handle = streamEvents<RunEvent>(api.runEventsUrl(runId), (event) => {
      update(runId, (run) => reduceRun(run, event));
      if (event.type === "stage_finished" || event.type === "run_finished") void invalidateProject(client, pid);
    }, (error) => update(runId, (run) => ({ ...run, error: error.message })));
    streams.current.set(runId, handle);
    void handle.done.then(async () => {
      streams.current.delete(runId);
      // The stream may close without run_finished (proxy restarts); settle from the status endpoint.
      try {
        const info = await api.run(runId);
        update(runId, (run) => (run.status === "queued" || run.status === "running" ? { ...run, status: info.status } : run));
      } catch { /* status endpoint unavailable; keep the streamed state */ }
      void invalidateProject(client, pid);
    });
  }, [client, update]);

  const start = useCallback(async (pid: string, request: RunRequest) => {
    const { run_id } = await api.startRun(pid, request);
    follow(pid, run_id, request);
    return run_id;
  }, [follow]);

  const dismiss = useCallback((runId: string) => {
    streams.current.get(runId)?.close();
    streams.current.delete(runId);
    setRuns((list) => list.filter((r) => r.runId !== runId));
  }, []);

  useEffect(() => () => { for (const s of streams.current.values()) s.close(); }, []);

  const value = useMemo(() => ({ runs, start, follow, dismiss }), [runs, start, follow, dismiss]);
  return <RunsContext.Provider value={value}>{children}</RunsContext.Provider>;
}

export function useRuns(): RunsContextValue {
  const value = useContext(RunsContext);
  if (!value) throw new Error("useRuns outside RunsProvider");
  return value;
}

export function useProjectRuns(pid: string) {
  const { runs, start, follow, dismiss } = useRuns();
  const mine = runs.filter((r) => r.pid === pid);
  const active = mine.find((r) => r.status === "queued" || r.status === "running") ?? null;
  return { runs: mine, active, start: (request: RunRequest) => start(pid, request), follow: (id: string) => follow(pid, id), dismiss };
}
