import { describe, expect, it } from "vitest";

import type { Diagnostic, ModelGraph, RobotSpec, SpecHarness } from "../api/types.ts";
import { SseParser, toEvent } from "../api/sse.ts";
import { reduceRun, type TrackedRun } from "../state/runs.tsx";
import {
  batteryStates, bindingPinMaps, diffHunks, diffLines, evidenceCounts, fabricationReady, groupDiagnostics, itemStatus, layoutTree, parseCsv,
  parseUnifiedDiff, pcbPinMap, pinNet, powerTree, rowsForState, unifiedDiff, wireRows,
} from "./domain.ts";
import { formatTarget, formatValue, itemUnit } from "./release.ts";
import { initialHandles, routePatch } from "./routeEdit.ts";
import { awgRadius, buildScene, resolveSubjects, wireColor } from "./scene.ts";
import { fmt, quantity, vector } from "./units.ts";

const diag = (over: Partial<Diagnostic>): Diagnostic => ({ code: "x", severity: "info", message: "", subjects: [], data: {}, stage: "model", ...over });

describe("units", () => {
  it("parses robot.yaml quantities to SI", () => {
    expect(quantity("260 mm")).toBeCloseTo(0.26);
    expect(quantity("15 min")).toBe(900);
    expect(quantity("90 deg")).toBeCloseTo(Math.PI / 2);
    expect(quantity("8 m/s")).toBe(8);
    expect(quantity(0.03)).toBe(0.03);
    expect(quantity("fast")).toBeNaN();
    expect(vector(["1 mm", 0, "2 cm"])).toEqual([0.001, 0, 0.02]);
  });

  it("formats with SI prefixes", () => {
    expect(fmt(0.0123, "m")).toBe("12.3 mm");
    expect(fmt(0.466, "A")).toBe("466 mA");
    expect(fmt(7417.9, "s")).toBe("124 min");
    expect(fmt(null, "V")).toBe("—");
    expect(fmt(8.224, "V")).toBe("8.22 V");
  });
});

describe("electrical", () => {
  const table = [
    { id: "cruise@full", operating_point: "cruise", battery_state: "full", soc: 1 },
    { id: "stall@full", operating_point: "motor_stall", battery_state: "full", soc: 1 },
    { id: "idle@minimum", operating_point: "idle", battery_state: "minimum", soc: 0 },
    { id: "idle@full", operating_point: "idle", battery_state: "full", soc: 1 },
    { id: "idle@nominal", operating_point: "idle", battery_state: "nominal", soc: 0.5 },
  ];

  it("orders battery states by SOC and operating points canonically", () => {
    expect(batteryStates(table).map((s) => s.state)).toEqual(["full", "nominal", "minimum"]);
    expect(rowsForState(table, "full").map((r) => r.operating_point)).toEqual(["idle", "cruise", "motor_stall"]);
  });

  it("power tree and layered layout survive supply cycles", () => {
    const tree = powerTree([
      { source: "battery.POS", nominal: 7.4, min: 6, max: 8.4, sinks: ["esc.VIN", "hat.VBAT"] },
      { source: "hat.5V", nominal: 5.1, min: 5.1, max: 5.1, sinks: ["pi.5V"] },
      { source: "pi.3V3", nominal: 3.3, min: 3.3, max: 3.3, sinks: ["hat.3V3", "encoder.VCC"] },
    ]);
    expect(tree.nodes.map((n) => n.id)).toEqual(["battery", "esc", "hat", "pi", "encoder"]);
    expect(tree.edges.map((e) => e.sink)).toEqual(["esc.VIN", "hat.VBAT", "pi.5V", "hat.3V3", "encoder.VCC"]);
    const layout = layoutTree(tree);
    expect([layout.battery!.depth, layout.hat!.depth, layout.pi!.depth, layout.encoder!.depth]).toEqual([0, 1, 2, 3]);
    expect(layout.esc!.row).not.toBe(layout.hat!.row);
  });
});

describe("software bindings", () => {
  const graph = {
    nets: {
      "C:hat.PI_ESC_PWM": ["C:hat.PI_ESC_PWM", "C:pi.GPIO18"],
      "C:esc.SIG": ["C:esc.SIG", "C:hat.ESC_PWM", "H:esc_signal.A.1"],
      "C:hat.5V": ["C:hat.5V", "C:pi.5V"],
    },
    net_names: { "C:hat.PI_ESC_PWM": "hat.PI_ESC_PWM", "C:esc.SIG": "ESC_PWM", "C:hat.5V": "5V" },
    pins: { "pi.GPIO18": { role: "pwm_out", passthrough: false, connector_pins: ["pi.J8.12"] },
            "esc.SIG": { role: "pwm_in", passthrough: false, connector_pins: ["esc.J_SIG.1"] } },
    edges: [{ a: "C:hat.PI_ESC_PWM", b: "C:hat.ESC_PWM", kind: "internal", link_kind: "series_resistor", value: 220 }],
  };

  it("matches the API's pin_map: devices on the net, reaches through the HAT's series resistor", () => {
    const [binding] = bindingPinMaps({ esc_pwm: { interface: "pwm", computer: "pi", pins: ["pi.J8.12"], expects: ["esc.J_SIG.1"] } }, graph);
    expect(binding!.pin_map[0]).toEqual({
      pin: "pi.J8.12", node: "C:pi.GPIO18", net: "C:hat.PI_ESC_PWM", net_name: "hat.PI_ESC_PWM",
      devices: ["hat.PI_ESC_PWM"], reaches: ["esc.SIG", "hat.ESC_PWM", "hat.PI_ESC_PWM"],
    });
    expect(binding!.expects_reached).toEqual([{ pin: "esc.J_SIG.1", reached: true }]);
  });

  it("reports unmapped pins and unreached expectations", () => {
    const [bad] = bindingPinMaps({ bad: { interface: "pwm", computer: "pi", pins: ["pi.J8.99", "pi.J8.12"], expects: ["servo.J1.1"] } }, graph);
    expect(bad!.pin_map[0]!.error).toMatch(/not a mapped connector pin/);
    expect(bad!.expects_reached).toEqual([{ pin: "servo.J1.1", reached: false }]);
    expect(pinNet(graph, "pi.5V")).toBe("5V");
  });
});

describe("pcb", () => {
  it("compares board pin -> net with the catalog pin map", () => {
    const rows = pcbPinMap(
      { J_BATT: { "1": "VBAT", "2": "GND" }, J_GPIO: { "12": "PI_ESC_PWM", "13": "SPARE" } },
      { J_BATT: { "1": "VBAT", "2": "VBAT" }, J_GPIO: { "12": "PI_ESC_PWM" }, J_RX: { "1": "5V" } },
    );
    expect(rows.map((r) => `${r.connector}.${r.pin}:${r.match ? "ok" : "x"}`)).toEqual([
      "J_BATT.1:ok", "J_BATT.2:x", "J_GPIO.12:ok", "J_GPIO.13:ok", "J_RX.1:x",
    ]);
    expect(rows.find((r) => r.connector === "J_RX")).toMatchObject({ boardNet: null, catalogPin: "5V" });
  });
});

describe("wiring", () => {
  it("wire rows pick the tightest bend relative to its requirement", () => {
    const rows = wireRows({
      routes: { wires: { W: { harness: "h", awg: 14, routed_length: 0.2, cut_length: 0.25 } } },
      detail: { wires: { W: { harness: "h", color: "blue", bends: [{ radius: 0.01, required: 0.02 }, { radius: 0.004, required: 0.026 }] } },
                wire_min_clearance: { W: { distance: -0.002, component: "sensor" } } },
      currents: [{ id: "W", continuous_current: -12, ampacity: 25 }],
      slack: { wires: { W: { min_distance: 0.025, component: "" } } },
    });
    expect(rows[0]).toMatchObject({ id: "W", bendRadius: 0.004, bendRequired: 0.026, clearance: -0.002, clearanceComponent: "sensor",
                                    current: 12, ampacity: 25, slackClearance: null });
  });

  it("route patch replaces only the edited harness's hints, in mm", () => {
    const spec = { robot: { name: "r" }, components: {}, electrical: { harnesses: [
      { id: "a", connectors: {}, wires: [], route: { slack: "20 mm", keep_away: ["motor"] } },
      { id: "b", connectors: {}, wires: [], route: { slack: "5 mm" } },
    ] } } as RobotSpec;
    const patch = routePatch(spec, "a", [[0.1, 0.02, 0.0451]], []) as { electrical: { harnesses: SpecHarness[] } };
    const [a, b] = patch.electrical.harnesses;
    expect(a!.route).toEqual({ slack: "20 mm", keep_away: ["motor"], via: [["100.0 mm", "20.0 mm", "45.1 mm"]], tie_points: [] });
    expect(b).toBe(spec.electrical!.harnesses![1]);
    expect(() => routePatch(spec, "zzz", [], [])).toThrow(/not in robot.yaml/);
  });

  it("initial handles prefer authored hints, else sample the routed bundle", () => {
    const spec = { robot: { name: "r" }, components: {}, electrical: { harnesses: [
      { id: "a", connectors: {}, wires: [], route: { via: [["10 mm", 0, 0]] } }, { id: "b", connectors: {}, wires: [] },
    ] } } as RobotSpec;
    expect(initialHandles(spec, "a", undefined, [[1, 1, 1]])).toEqual({ via: [[0.01, 0, 0]], ties: [[1, 1, 1]] });
    const sampled = initialHandles(spec, "b", [[0, 0, 0], [0.5, 0, 0]], [], 4);
    expect(sampled.via).toHaveLength(4);
    sampled.via.forEach((p, i) => expect(p[0]).toBeCloseTo([0.1, 0.2, 0.3, 0.4][i]!, 12));
  });
});

describe("scene", () => {
  const graph = {
    components: { esc: { type: "esc", kind: "esc", name: "ESC", mount: "base", shape: "box", size: [0.04, 0.03, 0.03], axis: "z", xyz: [0, 0, 0], rpy: [0, 0, 0], color: "#111", moving: false, mass: 0.05 } },
    wires: { W1: { harness: "esc_power", awg: 12, color: "red", from: "A.1", to: "B.1", ends: [[0, 0, 0], [0.1, 0, 0]] } },
    nets: {}, net_names: {},
  } as unknown as ModelGraph;

  it("maps diagnostic subjects to parts and wires", () => {
    const scene = buildScene({ graph, routes: null, diagnostics: [diag({ severity: "error", subjects: ["esc.BEC"] })] });
    expect(resolveSubjects(["esc.BEC", "op:servo_stall@full"], scene)).toEqual({ components: new Set(["esc"]), wires: new Set() });
    expect(resolveSubjects(["esc_power"], scene).wires).toEqual(new Set(["W1"]));
    expect(resolveSubjects(["esc_power.B|distribution.P2#1"], scene).wires).toEqual(new Set(["W1"]));
    expect(scene.wires[0]).toMatchObject({ routed: false, color: wireColor("red"), radius: awgRadius(12) });
    // Severity on the scene model is exact-subject (pins resolve to parts through resolveSubjects).
    expect(scene.components[0]).toMatchObject({ id: "esc", severity: null });
  });
});

describe("diagnostics", () => {
  it("groups worst-first", () => {
    const groups = groupDiagnostics([diag({ stage: "sim", severity: "warning" }), diag({ stage: "wiring", severity: "error" }), diag({ stage: "wiring" })], "stage");
    expect(groups.map((g) => [g.key, g.severity, g.items.length])).toEqual([["wiring", "error", 2], ["sim", "warning", 1]]);
    expect(groupDiagnostics([diag({ subjects: ["a", "b"] })], "subject").map((g) => g.key)).toEqual(["a", "b"]);
  });
});

describe("release gate", () => {
  const ev = (passed: boolean | null, over = {}) => ({ passed, stale: false, estimated: false, ...over });

  it("item status follows release.py precedence", () => {
    expect(itemStatus([])).toBe("not_run");
    expect(itemStatus([ev(true)])).toBe("verified");
    expect(itemStatus([ev(true, { estimated: true })])).toBe("estimated");
    expect(itemStatus([ev(true, { stale: true }), ev(true)])).toBe("stale");
    expect(itemStatus([ev(false, { stale: true })])).toBe("stale"); // stale failures do not count as fresh failures
    expect(itemStatus([ev(false), ev(true, { stale: true })])).toBe("failed");
  });

  it("fabrication ready only when everything is verified", () => {
    expect(fabricationReady([{ status: "verified" }])).toBe(true);
    expect(fabricationReady([{ status: "verified" }, { status: "estimated" }])).toBe(false);
    expect(fabricationReady([])).toBe(false);
    expect(evidenceCounts([{ status: "verified" }, { status: "estimated" }])).toMatchObject({ verified: 1, estimated: 1, failed: 0 });
  });

  it("formats targets and measured values with the requirement's unit", () => {
    const mass = { kind: "requirement" as const, subject: "max_mass", target: 2.2 };
    expect(itemUnit(mass)).toBe("kg");
    expect(formatTarget(mass)).toBe("≤ 2.2 kg");
    expect(formatTarget({ kind: "requirement", subject: "min_wire_clearance_moving", target: 0.008 })).toBe("≥ 8 mm");
    expect(formatTarget({ kind: "scenario_expectation", subject: "full_throttle.min_top_speed_mps", target: 8 })).toBe("≥ 8 m/s");
    expect(formatValue(0.0256, itemUnit({ kind: "requirement", subject: "max_wire_voltage_drop_fraction" }))).toBe("2.56%");
    expect(formatTarget({ kind: "stage", subject: "wiring", target: "no errors" })).toBe("no errors");
    expect(formatValue(true, "")).toBe("yes");
  });
});

describe("text", () => {
  it("parses quoted CSV", () => {
    expect(parseCsv('a,b\n1,"x, ""y"""\r\n2,\n')).toEqual([{ a: "1", b: 'x, "y"' }, { a: "2", b: "" }]);
  });

  it("diffs robot.yaml lines, deletions first, and elides unchanged context", () => {
    const before = ["a", "b", "c", "d", "e", "f", "g", "h", "i"].join("\n");
    const after = ["a", "b", "c", "d", "E", "f", "g", "h", "i"].join("\n");
    const lines = diffLines(before, after);
    expect(lines.filter((l) => l.op !== " ").map((l) => `${l.op}${l.text}`)).toEqual(["-e", "+E"]);
    expect(diffHunks(lines, 1).map((l) => (l ? `${l.op}${l.text}` : "…"))).toEqual([" d", "-e", "+E", " f"]);
  });

  it("unified diff round-trips through the parser with line numbers", () => {
    const before = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`).join("\n");
    const after = before.replace("line 5", "line five").replace("line 16", "line 16\nline 16b");
    const text = unifiedDiff(before, after, "robot.yaml", 1);
    expect(text.split("\n").filter((l) => l.startsWith("@@"))).toEqual(["@@ -4,3 +4,3 @@", "@@ -16,2 +16,3 @@"]);
    const parsed = parseUnifiedDiff(text);
    expect(parsed.filter((l) => l && l.op !== " ").map((l) => `${l!.op}${l!.a ?? l!.b}:${l!.text}`))
      .toEqual(["-5:line 5", "+5:line five", "+17:line 16b"]);
    expect(parsed.filter((l) => l === null)).toHaveLength(1);
    expect(unifiedDiff("same", "same")).toBe("");
  });
});

describe("live updates", () => {
  it("parses SSE frames split across chunks", () => {
    const parser = new SseParser();
    expect(parser.push("id: 3\nevent: stage_started\ndata: {\"stage\":")).toEqual([]);
    const out = parser.push("\"wiring\",\"index\":1,\"total\":3}\n\n: keep-alive\n\ndata: {\"type\":\"log\",\"line\":\"hi\"}\r\n\r\n");
    expect(out.map((m) => toEvent<{ type: string }>(m))).toEqual([
      { type: "stage_started", stage: "wiring", index: 1, total: 3 }, { type: "log", line: "hi" },
    ]);
    expect(out[0]!.id).toBe("3");
  });

  it("reduces run events into status and progress", () => {
    let run: TrackedRun = { runId: "r", pid: "p", request: {}, status: "queued", stage: null, stagesDone: [], total: 0, progress: 0, events: [], startedAt: 0 };
    run = reduceRun(run, { type: "run_queued", stages: ["wiring", "electrical", "report"] });
    expect(run.total).toBe(3);
    run = reduceRun(run, { type: "stage_started", stage: "wiring", index: 0, total: 3 });
    expect([run.status, run.stage, run.progress]).toEqual(["running", "wiring", 0]);
    run = reduceRun(run, { type: "stage_finished", stage: "wiring", status: "failed", ok: false });
    expect(run.progress).toBeCloseTo(1 / 3);
    run = reduceRun(run, { type: "run_finished", status: "failed", ok: false });
    expect(run).toMatchObject({ status: "failed", stage: null, progress: 1, stagesDone: [{ stage: "wiring", ok: false, status: "failed" }] });
    expect(run.events).toHaveLength(4);
  });
});

describe("charts", async () => {
  const { tickLabels } = await import("../components/TimeChart.tsx");
  it("tick labels use the tick spacing's precision", () => {
    expect(tickLabels([0.7285, 0.729, 0.7295, 0.73])).toEqual(["0.7285", "0.7290", "0.7295", "0.7300"]);
    expect(tickLabels([-0.00001, 0, 0.00001])).toEqual(["-0.00001", "0", "0.00001"]);
    expect(tickLabels([0, 2, 4])).toEqual(["0", "2", "4"]);
    expect(tickLabels([-0.0000001, 0.5])).toEqual(["-0.0", "0.5"].map((x) => (Number(x) === 0 ? "0" : x)));
  });
});
