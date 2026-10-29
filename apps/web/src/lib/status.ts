// One explicit status summary per project ("Not ready · 3 stages failed · Simulation results outdated"),
// with result and freshness kept separate. Pure; used by the header, the Overview and the Projects list.
import type { ProjectDetail, Severity, StageRow } from "../api/types.ts";
import { plural, stageLabel, stagePage } from "./labels.ts";

/** What a stage's last result says, independent of whether it is current. */
export type StageOutcome = "passed" | "failed" | "not_run" | "unavailable" | "running" | "not_configured" | "not_applicable";

export interface StageState {
  stage: string;
  label: string;
  /** project page that shows the evidence */
  page: string;
  outcome: StageOutcome;
  /** the result was computed from older inputs (robot.yaml or a pinned revision changed since) */
  outdated: boolean;
  counts: Partial<Record<Severity, number>>;
  startedAt: number | null;
  durationS: number | null;
}

export function stageState(row: Pick<StageRow, "stage" | "status" | "stale" | "counts" | "started_at" | "duration_s">, running = false): StageState {
  const outcome: StageOutcome = running ? "running" : row.status === "ok" ? "passed" : row.status === "failed" ? "failed"
    : row.status === "unavailable" ? "unavailable" : row.status === "not_configured" ? "not_configured"
    : row.status === "not_applicable" ? "not_applicable" : "not_run";
  return {
    stage: row.stage, label: stageLabel(row.stage), page: stagePage(row.stage), outcome,
    outdated: !!row.stale && row.status !== "missing",
    counts: row.counts ?? {}, startedAt: row.started_at ?? null, durationS: row.duration_s ?? null,
  };
}

/** "5 errors, 15 warnings" (spelled out, zero counts omitted); "no issues" when clean. */
export function countsText(counts: Partial<Record<Severity, number>>): string {
  const parts = [counts.error ? plural(counts.error, "error") : "", counts.warning ? plural(counts.warning, "warning") : ""].filter(Boolean);
  return parts.length ? parts.join(", ") : "no issues";
}

/** "Wiring" / "Wiring and Electrical" / "Wiring, PCB and 2 more". */
export function listLabels(labels: readonly string[], max = 2): string {
  if (labels.length <= 1) return labels[0] ?? "";
  if (labels.length <= max) return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
  return `${labels.slice(0, max).join(", ")} and ${labels.length - max} more`;
}

export interface ProjectStatus {
  ready: boolean;
  /** "Ready for fabrication" / "Not ready" */
  headline: string;
  /** "3 stages failed", "Simulation results outdated" */
  parts: string[];
  tone: "good" | "critical" | "warning";
  stages: StageState[];
  failed: StageState[];
  outdated: StageState[];
}

export function projectStatus(project: Pick<ProjectDetail, "stages" | "release">, runningStage: string | null = null): ProjectStatus {
  const stages = project.stages.filter((s) => s.stage !== "report").map((s) => stageState(s, s.stage === runningStage));
  const failed = stages.filter((s) => s.outcome === "failed");
  const outdated = stages.filter((s) => s.outdated);
  const ready = project.release.fabrication_ready;
  const parts = [
    failed.length ? `${plural(failed.length, "stage")} failed` : "",
    outdated.length ? `${listLabels(outdated.map((s) => s.label))} results outdated` : "",
  ].filter(Boolean);
  if (!parts.length && !ready) {
    const missing = stages.filter((s) => s.outcome === "not_run").length;
    if (missing) parts.push(`${plural(missing, "stage")} not run`);
  }
  return {
    ready, headline: ready ? "Ready for fabrication" : "Not ready", parts,
    tone: ready ? "good" : failed.length ? "critical" : "warning", stages, failed, outdated,
  };
}
