// Display helpers for release-gate items (requirement.<name>, scenario.<id>.<key>, stage.<name>).
import type { ReleaseItem } from "../api/types.ts";
import { fmt } from "./units.ts";

const REQUIREMENT_UNITS: Record<string, string> = {
  max_mass: "kg", top_speed: "m/s", min_runtime: "s", radio_range: "m", failsafe_stop_distance: "m",
  max_wire_voltage_drop_fraction: "%", min_wire_clearance_moving: "m", max_component_temperature_c: "°C",
};
const SUFFIX_UNITS: [RegExp, string][] = [[/_mps$/, "m/s"], [/_m$/, "m"], [/_v$/, "V"], [/_s$/, "s"], [/_a$/, "A"], [/_c$/, "°C"], [/_fraction$/, "%"]];

/** SI unit of an item's target/measured values, from its subject (requirement name or expectation key). */
export function itemUnit(item: Pick<ReleaseItem, "kind" | "subject">): string {
  if (item.kind === "stage") return "";
  const key = item.kind === "scenario_expectation" ? item.subject.split(".").pop() ?? "" : item.subject;
  return REQUIREMENT_UNITS[key] ?? SUFFIX_UNITS.find(([re]) => re.test(key))?.[1] ?? "";
}

export function formatValue(value: unknown, unit: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value !== "number") return String(value);
  if (unit === "%") return `${(value * 100).toFixed(2)}%`;
  return fmt(value, unit);
}

/** "≤ 2.2 kg" / "≥ 8 m/s" style target, using the requirement's min_/max_ prefix. */
export function formatTarget(item: Pick<ReleaseItem, "kind" | "subject" | "target">): string {
  if (item.kind === "stage") return String(item.target ?? "no errors");
  const key = item.kind === "scenario_expectation" ? item.subject.split(".").pop() ?? "" : item.subject;
  const op = /^max_/.test(key) ? "≤ " : /^min_|_beyond_|top_speed/.test(key) ? "≥ " : /^stop_within_/.test(key) ? "≤ " : "";
  return `${op}${formatValue(item.target, itemUnit(item))}`;
}

export const KIND_LABEL: Record<ReleaseItem["kind"], string> = { requirement: "requirement", scenario_expectation: "scenario", stage: "stage" };
