// Human labels for backend identifiers: stage names (matching the navigation), part and harness ids,
// operating points and diagnostic codes. Pure functions, shared by every page and unit-tested.
import type { Diagnostic } from "../api/types.ts";
import { fmt } from "./units.ts";

// ------------------------------------------------------------------ stages

/** Each pipeline stage, named as the page that shows its results (the same words as the navigation). */
export const STAGE_META: Record<string, { label: string; page: string }> = {
  assembly: { label: "Assembly", page: "3d" },
  model: { label: "3D model", page: "3d" },
  cad: { label: "CAD", page: "3d" },
  pcb: { label: "PCB", page: "pcb" },
  wiring: { label: "Wiring", page: "wiring" },
  electrical: { label: "Electrical", page: "electrical" },
  software: { label: "Software", page: "software" },
  sim: { label: "Simulation", page: "sim" },
  report: { label: "Report", page: "overview" },
};

export function stageLabel(stage: string): string {
  return STAGE_META[stage]?.label ?? sentence(stage.replace(/_/g, " "));
}

export function stagePage(stage: string): string {
  return STAGE_META[stage]?.page ?? "overview";
}

// ------------------------------------------------------------------ parts, harnesses, operating points

const ACRONYMS = new Set(["pi", "esc", "bec", "gps", "hat", "imu", "rx", "tx", "pwm", "uart", "i2c", "spi", "led", "pcb", "lipo", "crsf", "vcc", "gnd", "sig"]);
const CORNERS: Record<string, string> = { fl: "front-left", fr: "front-right", rl: "rear-left", rr: "rear-right" };

/** "pivot_right" → "front-right knuckle", "supply" → "battery lead", "distribution" → "power Y", "pi" → "Pi".
 * Wire ids ("W_BAT_P") and pin ids ("esc.BEC") are already names and stay as they are. */
export function partLabel(id: string): string {
  if (!id || /^[A-Z0-9_]+$/.test(id) || id.includes(".") || id.includes(":")) return id;
  const words = id.split(/[_\s]+/).filter(Boolean);
  const last = words[words.length - 1]?.toLowerCase() ?? "";
  const corner = words.length > 1 ? CORNERS[last] : undefined;
  const body = (corner ? words.slice(0, -1) : words).map((w) =>
    ACRONYMS.has(w.toLowerCase()) ? (w.toLowerCase() === "pi" ? "Pi" : w.toUpperCase()) : w.length === 1 ? w.toUpperCase() : w.toLowerCase());
  return [corner, ...body].filter(Boolean).join(" ");
}

export const BATTERY_STATE_LABEL: Record<string, string> = { full: "full charge", nominal: "nominal charge", minimum: "minimum charge" };

/** "motor_stall@minimum" → "motor stall · minimum charge"; "cruise" → "cruise". */
export function opLabel(id: string): string {
  const [point = "", state] = id.replace(/^op:/, "").split("@");
  const name = point.replace(/_/g, " ");
  return state ? `${name} · ${BATTERY_STATE_LABEL[state] ?? `${state} charge`}` : name;
}

export function sentence(text: string): string {
  return text ? text[0]!.toUpperCase() + text.slice(1) : text;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

// ------------------------------------------------------------------ diagnostics

/** Failure type of a diagnostic code, used to subgroup lists ("Cable interference · 24"). */
const DIAGNOSTIC_TYPE: Record<string, string> = {
  wire_intersects_component: "Cable interference",
  wire_clearance_violation: "Clearance to moving parts",
  wire_dynamic_clearance_violation: "Cable motion clearance",
  cable_sags_into_moving_part: "Cable sags into moving part",
  wire_bend_radius_violation: "Bend radius too tight",
  excess_slack_loop: "Excess slack loop",
  harness_mass: "Harness mass",
  wire_burst_above_ampacity: "Burst current above wire ampacity",
  connector_burst_above_continuous: "Burst current above contact rating",
  supply_undervoltage: "Supply undervoltage",
  voltage_drop_exceeded: "Harness voltage drop",
  wire_unprotected_by_fuse: "Wire not protected by a fuse",
  bec_overload: "BEC overload",
  brownout_risk: "Brownout",
  pin_overcurrent: "Pin overcurrent",
  runtime_estimate: "Runtime estimate",
  board_catalog_mismatch: "Board pin map differs from catalog",
  pcb_trace_over_ipc2152: "Trace current above IPC-2152 rating",
  pcb_connector_position_mismatch: "Connector position differs from catalog",
  servo_rail_dropout: "Servo rail dropout",
  pcb_tvs_clamp_exceeds_abs_max: "TVS clamp above regulator rating",
  interference_checked_by_cad: "Interference checked in CAD",
};

export function diagnosticType(code: string): string {
  return DIAGNOSTIC_TYPE[code] ?? sentence(code.replace(/_/g, " "));
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** One-line headline with ids replaced by part names and operating points spelled out. */
export function diagnosticHeadline(d: Pick<Diagnostic, "message" | "subjects"> & { data?: Record<string, unknown> }): string {
  let text = d.message.replace(/\b([a-z][a-z0-9_]*)@(full|nominal|minimum)\b/g, (_m, point: string, state: string) => opLabel(`${point}@${state}`));
  const component = typeof d.data?.component === "string" ? [d.data.component] : [];
  const ids = [...new Set([...d.subjects, ...component])].filter((s) => /^[a-z][a-z0-9_]*$/.test(s)).sort((a, b) => b.length - a.length);
  for (const id of ids) text = text.replace(new RegExp(`(^|[^\\w.])${id}(?![\\w.])`, "g"), (_m, pre: string) => `${pre}${partLabel(id)}`);
  return sentence(text);
}

/** The measured facts of a geometric/electrical diagnostic, for compact rows: what, where, how much, when. */
export interface DiagnosticFacts {
  /** harness or wire the finding is about */
  cable: string | null;
  /** the other part involved */
  part: string | null;
  /** the measured quantity, formatted ("2.2 mm deep", "7.8 mm", "2.38 A") */
  amount: string | null;
  /** the limit it is checked against ("needs ≥ 8 mm") */
  limit: string | null;
  /** simulation time, s */
  time: number | null;
  /** world position (m) when the stage reports one */
  position: [number, number, number] | null;
}

export function diagnosticFacts(d: Pick<Diagnostic, "code" | "subjects" | "data" | "stage">): DiagnosticFacts {
  const data = d.data ?? {};
  const component = typeof data.component === "string" ? data.component : null;
  const cable = d.subjects.find((s) => /^[a-z][a-z0-9_]*$/.test(s) && s !== component) ?? null;
  const pos = Array.isArray(data.position) && data.position.length === 3 && data.position.every((v) => typeof v === "number")
    ? (data.position as [number, number, number]) : null;
  const required = num(data.required) ?? num(data.min_radius) ?? num(data.limit);
  let amount: string | null = null;
  const penetration = num(data.penetration);
  const distance = num(data.min_distance);
  const radius = num(data.radius);
  if (penetration !== null) amount = `${fmt(penetration, "m")} deep`;
  else if (distance !== null) amount = distance < 0 ? `${fmt(-distance, "m")} inside` : fmt(distance, "m");
  else if (radius !== null) amount = `${fmt(radius, "m")} radius`;
  else if (num(data.current) !== null) amount = fmt(num(data.current), "A");
  else if (num(data.voltage) !== null) amount = fmt(num(data.voltage), "V");
  else if (num(data.fraction) !== null) amount = `${(num(data.fraction)! * 100).toFixed(2)} %`;
  const unit = num(data.current) !== null ? "A" : num(data.voltage) !== null && required !== null && required > 0.5 ? "V" : "m";
  const limit = required === null ? null
    : num(data.fraction) !== null ? `limit ${(required * 100).toFixed(1)} %`
    : d.code === "supply_undervoltage" || distance !== null || radius !== null ? `needs ≥ ${fmt(required, unit)}` : `rating ${fmt(required, unit)}`;
  return {
    cable: cable ? partLabel(cable) : null,
    part: component ? partLabel(component) : null,
    amount, limit,
    time: num(data.time),
    position: pos,
  };
}
