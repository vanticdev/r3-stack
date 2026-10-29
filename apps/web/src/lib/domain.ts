// Pure data transforms for the domain pages, also used by the dev mock layer to build API responses
// from build/ artifacts. No DOM, no three.js.
import type {
  Binding, Diagnostic, Evidence, EvidenceStatus, ModelGraph, Rail, ReleaseItem, Severity, SpecBinding,
} from "../api/types.ts";

// ------------------------------------------------------------------ electrical

export function ratio(value: number, limit: number): number {
  return limit > 0 ? value / limit : 0;
}

export interface PowerTree {
  nodes: { id: string; label: string; kind: string }[];
  edges: { from: string; to: string; source: string; sink: string; nominal: number }[];
}

/** Power tree from the graph's rails: source component -> sink components, one edge per sink pin. */
export function powerTree(rails: readonly Rail[], components: ModelGraph["components"] | null = null): PowerTree {
  const nodes = new Map<string, PowerTree["nodes"][number]>();
  const edges: PowerTree["edges"] = [];
  const node = (pin: string) => {
    const id = pin.split(".")[0]!;
    if (!nodes.has(id)) nodes.set(id, { id, label: components?.[id]?.name ?? id, kind: components?.[id]?.kind ?? "" });
    return id;
  };
  for (const rail of rails) {
    const from = node(rail.source);
    for (const sink of rail.sinks) {
      const to = node(sink);
      if (to !== from) edges.push({ from, to, source: rail.source, sink, nominal: rail.nominal });
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

/** Orthogonal top-down drawing of a power tree: sources above their loads, one bus per supply pin
 * (stem down from the source, horizontal bus, arrowed drops to each load) with a junction dot at every
 * branch. Edges that feed back up the tree (e.g. the Pi's 3V3 into the HAT) run in a lane to the right. */
export interface TreeDrawing {
  width: number;
  height: number;
  nodes: { id: string; x: number; y: number }[];
  buses: { source: string; nominal: number; stem: [number, number][]; bus: [number, number][] | null; junctions: [number, number][];
           drops: { sink: string; to: string; path: [number, number][] }[]; labelAt: [number, number] }[];
  backEdges: { source: string; sink: string; to: string; nominal: number; path: [number, number][]; labelAt: [number, number] }[];
}

export function drawPowerTree(tree: PowerTree, box = { w: 112, h: 38, gx: 20, gy: 64 }): TreeDrawing {
  const { w: W, h: H, gx: GX, gy: GY } = box;
  // Breadth-first from the nodes nothing feeds, so loads sit under their supply and siblings stay together.
  const incoming = new Set(tree.edges.map((e) => e.to));
  const roots = tree.nodes.filter((n) => !incoming.has(n.id)).map((n) => n.id);
  const order: string[] = [];
  const depth = new Map<string, number>();
  const queue = roots.length ? [...roots] : tree.nodes.slice(0, 1).map((n) => n.id);
  for (const r of queue) depth.set(r, 0);
  while (queue.length) {
    const id = queue.shift()!;
    order.push(id);
    for (const e of tree.edges) if (e.from === id && !depth.has(e.to)) { depth.set(e.to, depth.get(id)! + 1); queue.push(e.to); }
  }
  for (const n of tree.nodes) if (!depth.has(n.id)) { depth.set(n.id, 0); order.push(n.id); }
  // Tidy layout on the breadth-first spanning tree: leaves take consecutive columns and each supply is
  // centred over its loads, so sibling subtrees never interleave and buses never overlap.
  const parent = new Map<string, string>();
  for (const id of order) for (const e of tree.edges) if (e.from === id && depth.get(e.to) === depth.get(id)! + 1 && !parent.has(e.to)) parent.set(e.to, id);
  const children = (id: string) => order.filter((c) => parent.get(c) === id);
  const column = new Map<string, number>();
  let next = 0;
  const place = (id: string): number => {
    const kids = children(id);
    const col = kids.length ? (() => { const cols = kids.map(place); return (cols[0]! + cols[cols.length - 1]!) / 2; })() : next++;
    column.set(id, col);
    return col;
  };
  for (const id of order) if (!parent.has(id) && !column.has(id)) place(id);
  const widest = Math.max(1, next);
  const pos = new Map<string, { x: number; y: number }>();
  for (const id of order) pos.set(id, { x: 12 + column.get(id)! * (W + GX), y: 12 + depth.get(id)! * (H + GY) });
  const right = 12 + widest * (W + GX) - GX;
  const buses: TreeDrawing["buses"] = [];
  const backEdges: TreeDrawing["backEdges"] = [];
  const sources = [...new Set(tree.edges.map((e) => e.source))];
  const railsPerNode = new Map<string, number>();
  let lane = 0;
  for (const source of sources) {
    const edges = tree.edges.filter((e) => e.source === source);
    const from = edges[0]!.from;
    const a = pos.get(from)!;
    const forward = edges.filter((e) => depth.get(e.to)! > depth.get(from)!);
    const back = edges.filter((e) => depth.get(e.to)! <= depth.get(from)!);
    if (forward.length) {
      const k = railsPerNode.get(from) ?? 0;
      railsPerNode.set(from, k + 1);
      const cx = a.x + W / 2 + k * 14;
      const busY = a.y + H + GY / 2 - 6 + k * 10;
      const xs = forward.map((e) => pos.get(e.to)!.x + W / 2);
      const lo = Math.min(cx, ...xs), hi = Math.max(cx, ...xs);
      buses.push({
        source, nominal: forward[0]!.nominal,
        stem: [[cx, a.y + H], [cx, busY]],
        bus: hi - lo > 0.5 ? [[lo, busY], [hi, busY]] : null,
        junctions: forward.length > 1 || xs[0] !== cx ? [[cx, busY], ...xs.filter((x) => x !== cx).map((x): [number, number] => [x, busY])] : [],
        drops: forward.map((e) => { const b = pos.get(e.to)!; const x = b.x + W / 2; return { sink: e.sink, to: e.to, path: [[x, busY], [x, b.y]] as [number, number][] }; }),
        labelAt: [cx + 6, a.y + H + 13],
      });
    }
    for (const e of back) {
      const b = pos.get(e.to)!;
      const laneX = right + 18 + lane * 14;
      lane += 1;
      backEdges.push({
        source, sink: e.sink, to: e.to, nominal: e.nominal,
        path: [[a.x + W - 12, a.y + H], [a.x + W - 12, a.y + H + 10], [laneX, a.y + H + 10], [laneX, b.y + H / 2], [b.x + W, b.y + H / 2]],
        labelAt: [laneX + 4, (a.y + b.y + H) / 2],
      });
    }
  }
  const depthMax = Math.max(0, ...depth.values());
  return {
    width: right + 12 + (lane ? 18 + lane * 14 + 48 : 0),
    height: 24 + (depthMax + 1) * (H + GY) - GY,
    nodes: [...pos.entries()].map(([id, p]) => ({ id, ...p })),
    buses, backEdges,
  };
}

export const OPERATING_POINT_ORDER = ["idle", "cruise", "full_throttle", "motor_stall", "servo_stall_compute"];

/** Rows of one battery state, in the canonical idle → stall order. */
export function rowsForState<T extends { operating_point: string; battery_state: string }>(rows: readonly T[], state: string): T[] {
  const rank = (name: string) => { const i = OPERATING_POINT_ORDER.indexOf(name); return i < 0 ? 99 : i; };
  return rows.filter((r) => r.battery_state === state).sort((a, b) => rank(a.operating_point) - rank(b.operating_point));
}

/** Battery states present in the table, ordered full → minimum by their state of charge. */
export function batteryStates(rows: readonly { battery_state: string; soc: number }[]): { state: string; soc: number }[] {
  const seen = new Map<string, number>();
  for (const r of rows) if (!seen.has(r.battery_state)) seen.set(r.battery_state, r.soc);
  return [...seen.entries()].map(([state, soc]) => ({ state, soc })).sort((a, b) => b.soc - a.soc);
}

// ------------------------------------------------------------------ nets and software bindings

/** Net representative that contains a graph node such as "C:pi.GPIO18". */
function netOf(graph: Pick<ModelGraph, "nets">, node: string): string | null {
  for (const [rep, members] of Object.entries(graph.nets)) if (members.includes(node)) return rep;
  return null;
}

/** Name of the net a component pin (e.g. "hat.5V") sits on. */
export function pinNet(graph: Pick<ModelGraph, "nets" | "net_names">, pin: string): string | null {
  const rep = netOf(graph, `C:${pin}`);
  return rep ? graph.net_names[rep] ?? rep : null;
}

/** Binding view in the API's shape (api/projects.py software_view): each computer pin's net, the device
 * pins on it, the pins reached through internal series links, and whether each expected pin is reached. */
export function bindingPinMaps(bindings: Record<string, SpecBinding>, graph: Pick<ModelGraph, "nets" | "net_names" | "pins" | "edges">): Binding[] {
  const logicalByConnectorPin = new Map<string, string>();
  for (const [logical, info] of Object.entries(graph.pins ?? {})) for (const cp of info.connector_pins) logicalByConnectorPin.set(cp, logical);
  const series = (graph.edges ?? []).filter((e) => e.kind === "internal" && e.link_kind === "series_resistor");
  const componentPins = (rep: string) => (graph.nets[rep] ?? []).filter((m) => m.startsWith("C:")).map((m) => m.slice(2));
  const reachable = (start: string) => {
    const nets = new Set<string>();
    const queue = [start];
    while (queue.length) {
      const rep = queue.shift()!;
      if (nets.has(rep)) continue;
      nets.add(rep);
      const members = graph.nets[rep] ?? [];
      for (const e of series) {
        if (members.includes(e.a)) { const n = netOf(graph, e.b); if (n) queue.push(n); }
        if (members.includes(e.b)) { const n = netOf(graph, e.a); if (n) queue.push(n); }
      }
    }
    return nets;
  };
  return Object.entries(bindings).map(([handle, b]) => {
    const notComputer = (p: string) => !p.startsWith(`${b.computer}.`);
    const reachedPins = new Set<string>();
    const pinMap = b.pins.map((pin) => {
      const logical = logicalByConnectorPin.get(pin);
      const net = logical ? netOf(graph, `C:${logical}`) : null;
      if (!logical || !net) return { pin, error: `${pin} is not a mapped connector pin` };
      const reaches = [...reachable(net)].flatMap(componentPins).filter(notComputer).sort();
      reaches.forEach((p) => reachedPins.add(p));
      return { pin, node: `C:${logical}`, net, net_name: graph.net_names[net] ?? net, devices: componentPins(net).filter(notComputer), reaches };
    });
    const expects = (b.expects ?? []).map((pin) => ({ pin, reached: reachedPins.has(logicalByConnectorPin.get(pin) ?? pin) }));
    return { ...b, handle, pin_map: pinMap, expects_reached: expects };
  });
}

// ------------------------------------------------------------------ diagnostics

export interface DiagnosticGroup {
  key: string;
  severity: Severity;
  items: Diagnostic[];
}

/** Worst-first groups of diagnostics by stage, severity or subject. */
export function groupDiagnostics(diagnostics: readonly Diagnostic[], by: "stage" | "severity" | "subject"): DiagnosticGroup[] {
  const groups = new Map<string, Diagnostic[]>();
  for (const d of diagnostics) {
    const keys = by === "stage" ? [d.stage || "general"] : by === "severity" ? [d.severity] : d.subjects.length ? d.subjects : ["(no subject)"];
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

/** Item status from its evidence, in release.py's precedence: failed > stale > not_run > estimated > verified. */
export function itemStatus(evidence: readonly Pick<Evidence, "passed" | "stale" | "estimated">[]): EvidenceStatus {
  if (evidence.some((e) => !e.stale && e.passed === false)) return "failed";
  if (evidence.some((e) => e.stale)) return "stale";
  if (!evidence.length || evidence.every((e) => e.passed === null)) return "not_run";
  if (evidence.some((e) => e.estimated)) return "estimated";
  return "verified";
}

export function evidenceCounts(items: readonly Pick<ReleaseItem, "status">[]): Record<EvidenceStatus, number> {
  const counts: Record<EvidenceStatus, number> = { verified: 0, estimated: 0, failed: 0, not_run: 0, stale: 0 };
  for (const item of items) counts[item.status] += 1;
  return counts;
}

export function fabricationReady(items: readonly Pick<ReleaseItem, "status">[]): boolean {
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

/** Line diff (LCS) of two texts, deletions before insertions. */
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

/** Unified diff text (as in the API's `spec_changed.diff`). */
export function unifiedDiff(before: string, after: string, name = "robot.yaml", context = 3): string {
  const hunks = diffHunks(diffLines(before, after), context);
  if (!hunks.some((l) => l && l.op !== " ")) return "";
  const out = [`--- a/${name}`, `+++ b/${name}`];
  let block: DiffLine[] = [];
  const flush = () => {
    if (!block.length) return;
    const aStart = block.find((l) => l.a !== undefined)?.a ?? 0;
    const bStart = block.find((l) => l.b !== undefined)?.b ?? 0;
    const aLen = block.filter((l) => l.op !== "+").length;
    const bLen = block.filter((l) => l.op !== "-").length;
    out.push(`@@ -${aStart},${aLen} +${bStart},${bLen} @@`, ...block.map((l) => `${l.op}${l.text}`));
    block = [];
  };
  for (const line of hunks) {
    if (line === null) flush();
    else block.push(line);
  }
  flush();
  return `${out.join("\n")}\n`;
}

/** Unified diff text -> numbered lines for display (null = hunk break). */
export function parseUnifiedDiff(text: string): (DiffLine | null)[] {
  const out: (DiffLine | null)[] = [];
  let a = 0;
  let b = 0;
  for (const raw of text.split("\n")) {
    if (raw.startsWith("--- ") || raw.startsWith("+++ ") || raw === "" || raw.startsWith("\\")) continue;
    const hunk = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      if (out.length) out.push(null);
      a = Number(hunk[1]);
      b = Number(hunk[2]);
      continue;
    }
    const text = raw.slice(1);
    if (raw[0] === "+") out.push({ op: "+", text, b: b++ });
    else if (raw[0] === "-") out.push({ op: "-", text, a: a++ });
    else out.push({ op: " ", text, a: a++, b: b++ });
  }
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
  currents: readonly { id: string; continuous_current: number; ampacity?: number }[];
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
      current: current ? Math.abs(current.continuous_current) : null, ampacity: current?.ampacity ?? null,
      clearance: clearance?.distance ?? null, clearanceComponent: clearance?.component ?? null,
      bendRadius: tightest?.radius ?? null, bendRequired: tightest?.required ?? null,
      slackClearance: slack && slack.component ? slack.min_distance : null, slackComponent: slack?.component || null,
      failed: !!detail?.failed,
    };
  });
}

// ------------------------------------------------------------------ pcb

export interface PinMapRow {
  connector: string;
  pin: string;
  boardNet: string | null;
  catalogPin: string | null;
  match: boolean;
}

/** Board connector pin -> net (board.json) against the catalog's pin -> component pin. A pin matches when
 * the board net carries the catalog pin's name; catalog-unassigned pins only need to be unconnected or
 * pass-through (they match). */
export function pcbPinMap(connectors: Record<string, Record<string, string>> | null,
                          catalog: Record<string, Record<string, string>>): PinMapRow[] {
  const names = [...new Set([...Object.keys(connectors ?? {}), ...Object.keys(catalog)])].sort();
  return names.flatMap((connector) => {
    const board = connectors?.[connector] ?? {};
    const expected = catalog[connector] ?? {};
    const pins = [...new Set([...Object.keys(board), ...Object.keys(expected)])]
      .sort((a, b) => Number(a) - Number(b) || a.localeCompare(b));
    return pins.map((pin) => {
      const boardNet = board[pin] ?? null;
      const catalogPin = expected[pin] ?? null;
      return { connector, pin, boardNet, catalogPin, match: catalogPin === null || boardNet === catalogPin || boardNet?.endsWith(`.${catalogPin}`) === true };
    });
  });
}

// ------------------------------------------------------------------ rail tolerances

export interface RailLimits {
  lo: number;
  hi: number;
  /** whether the graph states each side; an unstated side uses ±`assumed` of nominal */
  loStated: boolean;
  hiStated: boolean;
}

/** Tolerance band of a rail. The graph gives min = max = nominal when no tolerance is specified. */
export function railLimits(rail: Pick<Rail, "nominal" | "min" | "max">, assumed = 0.05): RailLimits {
  const loStated = rail.min < rail.nominal;
  const hiStated = rail.max > rail.nominal;
  return {
    lo: loStated ? rail.min : rail.nominal * (1 - assumed),
    hi: hiStated ? rail.max : rail.nominal * (1 + assumed),
    loStated, hiStated,
  };
}

/** "low" / "high" when a calculated rail voltage leaves its band (both directions are checked). */
export function railFlag(voltage: number | null | undefined, limits: RailLimits): "low" | "high" | null {
  if (voltage === null || voltage === undefined || !Number.isFinite(voltage)) return null;
  return voltage < limits.lo ? "low" : voltage > limits.hi ? "high" : null;
}
