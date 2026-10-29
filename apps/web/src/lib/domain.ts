// Pure data transforms for the domain pages (also used by the dev mock layer to build API
// responses from build/ artifacts). No DOM, no three.js.
import type {
  BindingChain, CurrentRating, Diagnostic, EvidenceStatus, ModelGraph, OperatingPointsJson, PowerTree, RequirementEvidence,
  Severity, SpecBinding,
} from "../api/types.ts";

// ------------------------------------------------------------------ electrical

/** Worst continuous (and burst) current of every wire against its ampacity. */
export function wireRatings(op: OperatingPointsJson | null): CurrentRating[] {
  const worst = op?.worst_case?.wires ?? {};
  return Object.entries(worst)
    .map(([id, w]) => ({
      id, kind: "wire" as const, harness: w.harness,
      current: Math.abs(w.continuous_current), rating: w.ampacity, point: w.continuous_point,
      ...(w.burst_current !== undefined ? { burst_current: Math.abs(w.burst_current) } : {}),
      // Ampacity is a continuous rating; short bursts are judged on temperature (burst_ratio is reported separately).
      ...(w.burst_point ? { burst_point: w.burst_point } : {}),
    }))
    .sort((a, b) => b.current / b.rating - a.current / a.rating);
}

/** Peak |current| of every connector contact over the continuous and burst operating points. */
export function connectorRatings(op: OperatingPointsJson | null): CurrentRating[] {
  const out = new Map<string, CurrentRating>();
  for (const [pointId, point] of Object.entries(op?.operating_points ?? {})) {
    const burst = point.kind === "burst";
    for (const [id, contact] of Object.entries(point.contacts ?? {})) {
      const current = Math.abs(contact.current);
      const row = out.get(id) ?? { id, kind: "connector" as const, current: 0, rating: contact.rating, point: "" };
      if (!burst && current >= row.current) Object.assign(row, { current, point: pointId });
      if (burst && current >= (row.burst_current ?? 0)) {
        Object.assign(row, { burst_current: current, burst_point: pointId, burst_rating: contact.burst_rating ?? contact.rating });
      }
      out.set(id, row);
    }
  }
  return [...out.values()].sort((a, b) => b.current / b.rating - a.current / a.rating);
}

export function ratio(value: number, limit: number): number {
  return limit > 0 ? value / limit : 0;
}

/** Power tree from the graph's rails: source component -> sink components, labelled by rail. */
export function powerTree(graph: Pick<ModelGraph, "rails" | "components"> | null): PowerTree {
  const nodes = new Map<string, PowerTree["nodes"][number]>();
  const edges: PowerTree["edges"] = [];
  const node = (pin: string) => {
    const id = pin.split(".")[0]!;
    if (!nodes.has(id)) nodes.set(id, { id, label: graph?.components?.[id]?.name ?? id, kind: graph?.components?.[id]?.kind ?? "" });
    return id;
  };
  for (const rail of graph?.rails ?? []) {
    const from = node(rail.source);
    for (const sink of rail.sinks) {
      const to = node(sink);
      if (to !== from) edges.push({ from, to, rail: `${rail.source} → ${sink}`, nominal: rail.nominal });
    }
  }
  return { nodes: [...nodes.values()], edges };
}

/** Layered layout of a (possibly cyclic) power tree: BFS depth from the nodes with no incoming edge. */
export function layoutTree(tree: PowerTree): Record<string, { depth: number; row: number }> {
  const incoming = new Set(tree.edges.map((e) => e.to));
  const roots = tree.nodes.filter((n) => !incoming.has(n.id)).map((n) => n.id);
  const depth = new Map<string, number>();
  const queue = roots.length ? [...roots] : tree.nodes.slice(0, 1).map((n) => n.id);
  for (const r of queue) depth.set(r, 0);
  while (queue.length) {
    const id = queue.shift()!;
    for (const e of tree.edges) {
      if (e.from === id && !depth.has(e.to)) {
        depth.set(e.to, depth.get(id)! + 1);
        queue.push(e.to);
      }
    }
  }
  for (const n of tree.nodes) if (!depth.has(n.id)) depth.set(n.id, 0);
  const rows = new Map<number, number>();
  const out: Record<string, { depth: number; row: number }> = {};
  for (const n of tree.nodes) {
    const d = depth.get(n.id)!;
    const row = rows.get(d) ?? 0;
    rows.set(d, row + 1);
    out[n.id] = { depth: d, row };
  }
  return out;
}

export const OPERATING_POINT_ORDER = ["idle", "cruise", "full_throttle", "motor_stall", "servo_stall_compute"];

// ------------------------------------------------------------------ software bindings

/** Net representative that contains a graph node such as "C:pi.GPIO18". */
function netOf(graph: Pick<ModelGraph, "nets">, node: string): string | null {
  for (const [rep, members] of Object.entries(graph.nets)) if (members.includes(node)) return rep;
  return null;
}

/** Software handle -> computer pin -> net -> device pins, following series resistors on the way. */
export function bindingChains(bindings: Record<string, SpecBinding>,
                              graph: Pick<ModelGraph, "nets" | "net_names" | "pins"> & { edges?: { a: string; b: string; kind: string; link_kind?: string; ref?: string; value?: number }[] }): BindingChain[] {
  const logicalByConnectorPin = new Map<string, string>();
  for (const [logical, info] of Object.entries(graph.pins ?? {})) for (const cp of info.connector_pins) logicalByConnectorPin.set(cp, logical);
  const series = (graph.edges ?? []).filter((e) => e.kind === "internal" && e.link_kind === "series_resistor");
  return Object.entries(bindings).map(([handle, b]) => {
    const pins = b.pins.map((pin) => {
      const logical = logicalByConnectorPin.get(pin) ?? null;
      const start = logical ? netOf(graph, `C:${logical}`) : null;
      // Walk the net plus nets joined through series resistors (signal paths through the HAT).
      const nets = new Set<string>();
      const queue = start ? [start] : [];
      while (queue.length) {
        const rep = queue.shift()!;
        if (nets.has(rep)) continue;
        nets.add(rep);
        for (const e of series) {
          const members = graph.nets[rep] ?? [];
          if (members.includes(e.a)) { const n = netOf(graph, e.b); if (n) queue.push(n); }
          if (members.includes(e.b)) { const n = netOf(graph, e.a); if (n) queue.push(n); }
        }
      }
      const devices = [...nets].flatMap((rep) => (graph.nets[rep] ?? []).filter((m) => m.startsWith("C:")).map((m) => m.slice(2)))
        .filter((m) => m !== logical && !m.startsWith(`${b.computer}.`));
      const named = start ? graph.net_names[start] ?? start : null;
      const last = [...nets].pop();
      return { pin, logical, net: last ? graph.net_names[last] ?? last : named, devices: [...new Set(devices)] };
    });
    const expects = (b.expects ?? []).map((e) => logicalByConnectorPin.get(e) ?? e);
    const reached = new Set(pins.flatMap((p) => p.devices));
    return {
      handle, interface: b.interface, computer: b.computer, device: b.device, channel: (b.channel as number | undefined) ?? null,
      pins, expects, ok: expects.every((e) => reached.has(e)),
    };
  });
}

// ------------------------------------------------------------------ diagnostics

export interface DiagnosticGroup {
  key: string;
  severity: Severity;
  items: Diagnostic[];
}

/** stage -> worst-first groups of diagnostics (by code), for the diagnostics page. */
export function groupDiagnostics(diagnostics: readonly Diagnostic[], by: "stage" | "severity" | "subject"): DiagnosticGroup[] {
  const groups = new Map<string, Diagnostic[]>();
  for (const d of diagnostics) {
    const keys = by === "stage" ? [d.stage] : by === "severity" ? [d.severity] : d.subjects.length ? d.subjects : ["(no subject)"];
    for (const key of keys) (groups.get(key) ?? groups.set(key, []).get(key)!).push(d);
  }
  const rank: Record<string, number> = { error: 0, warning: 1, info: 2 };
  const worst = (items: Diagnostic[]): Severity =>
    items.some((d) => d.severity === "error") ? "error" : items.some((d) => d.severity === "warning") ? "warning" : "info";
  return [...groups.entries()]
    .map(([key, items]) => ({ key, severity: worst(items), items: items.slice().sort((a, b) => rank[a.severity]! - rank[b.severity]!) }))
    .sort((a, b) => rank[a.severity]! - rank[b.severity]! || b.items.length - a.items.length || a.key.localeCompare(b.key));
}

export function severityCounts(diagnostics: readonly Pick<Diagnostic, "severity">[]): Record<Severity, number> {
  const counts: Record<Severity, number> = { error: 0, warning: 0, info: 0 };
  for (const d of diagnostics) counts[d.severity] = (counts[d.severity] ?? 0) + 1;
  return counts;
}

// ------------------------------------------------------------------ release gate

export const EVIDENCE_ORDER: EvidenceStatus[] = ["failed", "stale", "estimated", "not_run", "verified"];

export function evidenceCounts(items: readonly RequirementEvidence[]): Record<EvidenceStatus, number> {
  const counts: Record<EvidenceStatus, number> = { verified: 0, estimated: 0, failed: 0, not_run: 0, stale: 0 };
  for (const item of items) counts[item.status] += 1;
  return counts;
}

export function fabricationReady(items: readonly RequirementEvidence[]): boolean {
  return items.length > 0 && items.every((i) => i.status === "verified");
}

// ------------------------------------------------------------------ text helpers

/** Minimal RFC 4180 CSV reader (quoted fields, embedded commas/newlines). Rows keyed by header. */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((v) => v !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.some((v) => v !== "")) rows.push(row);
  const [header, ...body] = rows;
  if (!header) return [];
  return body.map((values) => Object.fromEntries(header.map((h, i) => [h, values[i] ?? ""])));
}

export type DiffLine = { op: " " | "+" | "-"; text: string; a?: number; b?: number };

/** Line diff (LCS) of two texts, for the live robot.yaml diff. */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  // Trim the common prefix/suffix so the O(n·m) table only covers the changed middle.
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const lcs: number[][] = Array.from({ length: midA.length + 1 }, () => new Array<number>(midB.length + 1).fill(0));
  for (let i = midA.length - 1; i >= 0; i--) {
    for (let j = midB.length - 1; j >= 0; j--) {
      lcs[i]![j] = midA[i] === midB[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }
  const out: DiffLine[] = a.slice(0, start).map((text, i) => ({ op: " ", text, a: i + 1, b: i + 1 }));
  let i = 0;
  let j = 0;
  while (i < midA.length || j < midB.length) {
    if (i < midA.length && j < midB.length && midA[i] === midB[j]) {
      out.push({ op: " ", text: midA[i]!, a: start + i + 1, b: start + j + 1 }); i++; j++;
    } else if (j < midB.length && (i >= midA.length || lcs[i]![j + 1]! > lcs[i + 1]![j]!)) {
      out.push({ op: "+", text: midB[j]!, b: start + j + 1 }); j++;
    } else {
      out.push({ op: "-", text: midA[i]!, a: start + i + 1 }); i++;
    }
  }
  for (let k = 0; k < a.length - endA; k++) out.push({ op: " ", text: a[endA + k]!, a: endA + k + 1, b: endB + k + 1 });
  return out;
}

/** Diff lines with only `context` unchanged lines around each change (null marks an elision). */
export function diffHunks(lines: readonly DiffLine[], context = 3): (DiffLine | null)[] {
  const keep = new Array<boolean>(lines.length).fill(false);
  lines.forEach((line, index) => {
    if (line.op === " ") return;
    for (let k = Math.max(0, index - context); k <= Math.min(lines.length - 1, index + context); k++) keep[k] = true;
  });
  const out: (DiffLine | null)[] = [];
  lines.forEach((line, index) => {
    if (keep[index]) out.push(line);
    else if (out[out.length - 1] !== null) out.push(null);
  });
  if (out[0] === null) out.shift();
  if (out[out.length - 1] === null) out.pop();
  return out;
}

// ------------------------------------------------------------------ wiring

export interface WireRow {
  id: string;
  harness: string;
  awg: number;
  color: string;
  signal: string;
  routedLength: number | null;
  cutLength: number | null;
  current: number | null;
  ampacity: number | null;
  clearance: number | null;
  clearanceComponent: string | null;
  bendRadius: number | null;
  bendRequired: number | null;
  slackClearance: number | null;
  slackComponent: string | null;
  failed: boolean;
}

/** One row per wire: lengths, worst current vs ampacity, static clearance to moving parts, the tightest
 * bend relative to its required radius, and the dynamic (slack sim) clearance. */
export function wireRows(input: {
  routes: { wires: Record<string, { harness: string; awg: number; routed_length: number; cut_length: number }> } | null;
  detail: { wires: Record<string, { harness: string; color: string; signal?: string; failed?: boolean;
                                    bends?: { radius: number; required: number }[] }>;
            wire_min_clearance?: Record<string, { distance: number; component: string }> } | null;
  currents: readonly CurrentRating[];
  slack: { wires: Record<string, { min_distance: number; component: string }> } | null;
}): WireRow[] {
  const ids = new Set([...Object.keys(input.routes?.wires ?? {}), ...Object.keys(input.detail?.wires ?? {})]);
  const currents = new Map(input.currents.map((c) => [c.id, c]));
  return [...ids].map((id) => {
    const route = input.routes?.wires[id];
    const detail = input.detail?.wires[id];
    const clearance = input.detail?.wire_min_clearance?.[id];
    const current = currents.get(id);
    const tightest = (detail?.bends ?? []).reduce<{ radius: number; required: number } | null>(
      (worst, b) => (!worst || b.radius / b.required < worst.radius / worst.required ? b : worst), null);
    const slack = input.slack?.wires[id];
    return {
      id, harness: route?.harness ?? detail?.harness ?? "", awg: route?.awg ?? 0, color: detail?.color ?? "", signal: detail?.signal ?? "",
      routedLength: route?.routed_length ?? null, cutLength: route?.cut_length ?? null,
      current: current?.current ?? null, ampacity: current?.rating ?? null,
      clearance: clearance?.distance ?? null, clearanceComponent: clearance?.component ?? null,
      bendRadius: tightest?.radius ?? null, bendRequired: tightest?.required ?? null,
      slackClearance: slack && slack.component ? slack.min_distance : null, slackComponent: slack?.component || null,
      failed: !!detail?.failed,
    };
  });
}

/** Name of the net a component pin (e.g. "hat.5V") sits on. */
export function pinNet(graph: Pick<ModelGraph, "nets" | "net_names">, pin: string): string | null {
  const rep = netOf(graph, `C:${pin}`);
  return rep ? graph.net_names[rep] ?? rep : null;
}

/** Operating points of one battery state, in the canonical idle → stall order. */
export function pointsForState(op: OperatingPointsJson | null, state: string): [string, OperatingPointsJson["operating_points"][string]][] {
  const entries = Object.entries(op?.operating_points ?? {}).filter(([, p]) => p.battery_state === state);
  const rank = (name: string) => { const i = OPERATING_POINT_ORDER.indexOf(name); return i < 0 ? 99 : i; };
  return entries.sort((a, b) => rank(a[1].operating_point) - rank(b[1].operating_point));
}
