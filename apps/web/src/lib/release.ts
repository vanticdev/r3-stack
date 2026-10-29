// Display helpers for release-gate items (requirement.<name>, scenario.<id>.<key>, stage.<name>):
// engineering labels with units, result kept separate from freshness, and scenario evidence grouped
// under the requirement it backs.
import type { EvidenceStatus, ReleaseItem } from "../api/types.ts";
import { sentence, stageLabel } from "./labels.ts";
import { fmt } from "./units.ts";

type ItemKey = Pick<ReleaseItem, "kind" | "subject"> & { id?: string };

const REQUIREMENT_UNITS: Record<string, string> = {
  max_mass: "kg", top_speed: "m/s", min_runtime: "s", radio_range: "m", failsafe_stop_distance: "m",
  max_wire_voltage_drop_fraction: "%", min_wire_clearance_moving: "m", max_component_temperature_c: "°C",
};
const SUFFIX_UNITS: [RegExp, string][] = [[/_mps$/, "m/s"], [/_m$/, "m"], [/_v$/, "V"], [/_s$/, "s"], [/_a$/, "A"], [/_c$/, "°C"], [/_fraction$/, "%"]];

const LABELS: Record<string, string> = {
  max_mass: "Mass", top_speed: "Top speed", min_runtime: "Runtime", radio_range: "Radio range",
  failsafe_stop_distance: "Failsafe stopping distance", max_wire_voltage_drop_fraction: "Wire voltage drop",
  min_wire_clearance_moving: "Wire clearance to moving parts", max_component_temperature_c: "Component temperature",
  min_top_speed_mps: "Top speed", brownouts: "Brownouts", laps_completed: "Laps completed",
  max_cross_track_error_m: "Cross-track error", failsafe_events: "Failsafe events", min_battery_voltage_v: "Battery voltage",
  link_lost_beyond_m: "Radio link holds to", stop_within_m_of_link_loss: "Stops after link loss within",
  min_cell_voltage_v: "Cell voltage", low_voltage_cutoff_engaged: "Low-voltage cutoff engages", min_wire_clearance_m: "Wire clearance",
};

/** The checked quantity: the requirement name, or the expectation key of a scenario item. */
export function itemKey(item: ItemKey): string {
  if (item.kind === "scenario_expectation") {
    const fromId = item.id?.split(".").slice(2).join(".");
    return fromId || item.subject.split(".").pop() || item.subject;
  }
  return item.subject;
}

/** Scenario id of a scenario expectation. */
export function itemScenario(item: ItemKey): string {
  return item.id?.split(".")[1] ?? item.subject.split(".")[0] ?? item.subject;
}

/** SI unit of an item's target/measured values. */
export function itemUnit(item: ItemKey): string {
  if (item.kind === "stage") return "";
  const key = itemKey(item);
  return REQUIREMENT_UNITS[key] ?? SUFFIX_UNITS.find(([re]) => re.test(key))?.[1] ?? "";
}

/** "Wire voltage drop", "Top speed", "PCB" (stage items use the navigation names). */
export function itemLabel(item: ItemKey): string {
  if (item.kind === "stage") return stageLabel(item.subject);
  const key = itemKey(item);
  return LABELS[key] ?? sentence(key.replace(/^(min|max)_/, "").replace(/_(mps|m|v|s|a|c|fraction)$/, "").replace(/_/g, " "));
}

export function formatValue(value: unknown, unit: string): string {
  if (value === null || value === undefined) return "not recorded";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value !== "number") return String(value);
  if (unit === "%") return `${(value * 100).toFixed(2)}%`;
  return fmt(value, unit);
}

/** "≤ 2.2 kg" / "≥ 8 m/s" style target, using the requirement's min_/max_ prefix. */
export function formatTarget(item: ItemKey & Pick<ReleaseItem, "target">): string {
  if (item.kind === "stage") return String(item.target ?? "no errors");
  const key = itemKey(item);
  const op = /^max_/.test(key) ? "≤ " : /^min_|_beyond_|top_speed/.test(key) ? "≥ " : /^stop_within_/.test(key) ? "≤ " : "";
  return `${op}${formatValue(item.target, itemUnit(item))}`;
}

/** Result of an item, separate from freshness: "4.19% · limit ≤ 3.00%", "5 errors", "not checked yet". */
export function itemResult(item: ReleaseItem): { value: string; target: string } {
  const unit = itemUnit(item);
  const target = item.kind === "stage" ? "no errors" : formatTarget(item);
  const evidence = item.evidence.filter((e) => e.passed !== null || e.value !== null);
  if (!evidence.length) return { value: "not checked yet", target };
  if (item.kind === "stage") {
    const errors = typeof evidence[0]!.value === "number" ? (evidence[0]!.value as number) : null;
    return { value: errors === null ? String(evidence[0]!.value) : errors === 0 ? "no errors" : `${errors} ${errors === 1 ? "error" : "errors"}`, target };
  }
  return { value: [...new Set(evidence.map((e) => formatValue(e.value, unit)))].join(" / "), target };
}

/** Explanation text worth showing (the stage's detail), without repeating the value. */
export function itemNote(item: ReleaseItem): string {
  const notes = [...new Set([item.message, ...item.evidence.map((e) => e.detail)].filter(Boolean))];
  return notes.join(" · ");
}

export const KIND_LABEL: Record<ReleaseItem["kind"], string> = { requirement: "requirement", scenario_expectation: "scenario", stage: "stage" };

/** Result outcome, independent of freshness. */
export function itemOutcome(item: ReleaseItem): "passed" | "failed" | "not_run" {
  const evidence = item.evidence.filter((e) => e.passed !== null);
  if (!evidence.length) return "not_run";
  return evidence.some((e) => e.passed === false) ? "failed" : "passed";
}

export const isOutdated = (item: ReleaseItem): boolean => item.status === "stale" || item.evidence.some((e) => e.stale);

// ------------------------------------------------------------------ grouping

export interface RequirementGroup {
  item: ReleaseItem;
  /** scenario expectations that check the same quantity */
  related: ReleaseItem[];
}

export interface ReleaseGroups {
  requirements: RequirementGroup[];
  scenarios: { scenario: string; items: ReleaseItem[] }[];
  stages: ReleaseItem[];
}

const normalized = (key: string) => key.replace(/^(min|max)_/, "").replace(/_(mps|m|v|s|a|c|fraction|moving)$/, "");
const evidenceKeys = (item: ReleaseItem) => new Set(item.evidence.map((e) => `${e.stage}|${e.check}`));

const STATUS_RANK: Record<EvidenceStatus, number> = { failed: 0, stale: 1, estimated: 2, not_run: 3, verified: 4 };
const byStatus = (a: ReleaseItem, b: ReleaseItem) => STATUS_RANK[a.status] - STATUS_RANK[b.status];

/** Requirements with the scenario expectations that back them (same evidence, or the same quantity),
 * remaining expectations per scenario, and stage items. Each list is worst-first. */
export function groupRelease(items: readonly ReleaseItem[]): ReleaseGroups {
  const requirements = items.filter((i) => i.kind === "requirement").map((item) => ({ item, related: [] as ReleaseItem[] }));
  const scenarios = new Map<string, ReleaseItem[]>();
  for (const item of items.filter((i) => i.kind === "scenario_expectation")) {
    const keys = evidenceKeys(item);
    const key = normalized(itemKey(item));
    const owner = requirements.find((r) => [...evidenceKeys(r.item)].some((k) => keys.has(k)))
      ?? requirements.find((r) => { const rk = normalized(r.item.subject); return rk.length > 3 && key.length > 3 && (rk.includes(key) || key.includes(rk)); });
    if (owner) owner.related.push(item);
    else (scenarios.get(itemScenario(item)) ?? scenarios.set(itemScenario(item), []).get(itemScenario(item))!).push(item);
  }
  const groupRank = (g: RequirementGroup) => Math.min(STATUS_RANK[g.item.status], ...g.related.map((r) => STATUS_RANK[r.status]));
  return {
    requirements: requirements.map((g) => ({ ...g, related: g.related.sort(byStatus) })).sort((a, b) => groupRank(a) - groupRank(b)),
    scenarios: [...scenarios.entries()].map(([scenario, list]) => ({ scenario, items: list.sort(byStatus) }))
      .sort((a, b) => STATUS_RANK[a.items[0]!.status] - STATUS_RANK[b.items[0]!.status]),
    stages: items.filter((i) => i.kind === "stage").slice().sort(byStatus),
  };
}

/** "Not ready for fabrication: 5 failed · 4 outdated · 2 estimated · 15 not checked". */
export function releaseSummary(counts: Partial<Record<EvidenceStatus, number>>, ready: boolean): { headline: string; parts: string[] } {
  const parts = [
    counts.failed ? `${counts.failed} failed` : "",
    counts.stale ? `${counts.stale} outdated` : "",
    counts.estimated ? `${counts.estimated} estimated` : "",
    counts.not_run ? `${counts.not_run} not checked` : "",
    counts.verified ? `${counts.verified} verified` : "",
  ].filter(Boolean);
  return { headline: ready ? "Ready for fabrication" : "Not ready for fabrication", parts };
}
