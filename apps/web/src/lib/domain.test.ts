import { describe, expect, it } from "vitest";

import type { Diagnostic, ModelGraph, OperatingPointsJson, RobotSpec, SpecHarness } from "../api/types.ts";
import { SseParser, toEvent } from "../api/sse.ts";
import { reduceRun, type TrackedRun } from "../state/runs.tsx";
import {
  bindingChains, connectorRatings, diffHunks, diffLines, evidenceCounts, fabricationReady, groupDiagnostics, layoutTree, parseCsv,
  pinNet, pointsForState, powerTree, wireRatings, wireRows,
} from "./domain.ts";
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
  const op: OperatingPointsJson = {
    operating_points: {
      "cruise@full": { operating_point: "cruise", battery_state: "full", soc: 1, kind: "continuous", components: {}, nets: { VBAT: 8.3 }, wires: {},
                       contacts: { "a|b#1": { current: -4, rating: 30, burst_rating: 60 } } },
      "stall@full": { operating_point: "motor_stall", battery_state: "full", soc: 1, kind: "burst", components: {}, nets: {}, wires: {},
                      contacts: { "a|b#1": { current: 55, rating: 30, burst_rating: 60 } } },
      "idle@full": { operating_point: "idle", battery_state: "full", soc: 1, kind: "continuous", components: {}, nets: {}, wires: {},
                     contacts: { "a|b#1": { current: 1, rating: 30 } } },
      "idle@minimum": { operating_point: "idle", battery_state: "minimum", soc: 0, kind: "continuous", components: {}, nets: {}, wires: {}, contacts: {} },
    },
    worst_case: { wires: {
      W1: { continuous_current: -4.5, continuous_point: "cruise@full", ampacity: 35, awg: 12, harness: "h", burst_current: 59, burst_point: "stall@full" },
      W2: { continuous_current: 11, continuous_point: "cruise@full", ampacity: 12, awg: 18, harness: "h" },
    } },
  };

  it("wire ratings use |continuous| current against ampacity, worst ratio first", () => {
    const rows = wireRatings(op);
    expect(rows.map((r) => r.id)).toEqual(["W2", "W1"]);
    expect(rows[1]).toMatchObject({ current: 4.5, rating: 35, burst_current: 59, burst_point: "stall@full" });
    expect(wireRatings(null)).toEqual([]);
  });

  it("connector ratings separate continuous peaks from bursts", () => {
    const [row] = connectorRatings(op);
    expect(row).toMatchObject({ id: "a|b#1", current: 4, point: "cruise@full", burst_current: 55, burst_rating: 60, rating: 30 });
  });

  it("operating points of a battery state come in canonical order", () => {
    expect(pointsForState(op, "full").map(([, p]) => p.operating_point)).toEqual(["idle", "cruise", "motor_stall"]);
  });

  it("power tree and layered layout survive supply cycles", () => {
    const graph = {
      components: {},
      rails: [
        { source: "battery.POS", nominal: 7.4, min: 6, max: 8.4, sinks: ["esc.VIN", "hat.VBAT"] },
        { source: "hat.5V", nominal: 5.1, min: 5.1, max: 5.1, sinks: ["pi.5V"] },
        { source: "pi.3V3", nominal: 3.3, min: 3.3, max: 3.3, sinks: ["hat.3V3", "encoder.VCC"] },
      ],
    } as unknown as ModelGraph;
    const tree = powerTree(graph);
    expect(tree.nodes.map((n) => n.id)).toEqual(["battery", "esc", "hat", "pi", "encoder"]);
    expect(tree.edges).toHaveLength(5);
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

  it("follows the pin through the HAT's series resistor to the device", () => {
    const [chain] = bindingChains({ esc_pwm: { interface: "pwm", computer: "pi", pins: ["pi.J8.12"], expects: ["esc.J_SIG.1"] } }, graph);
    expect(chain!.pins[0]).toMatchObject({ pin: "pi.J8.12", logical: "pi.GPIO18", net: "ESC_PWM" });
    expect(chain!.pins[0]!.devices).toEqual(expect.arrayContaining(["esc.SIG", "hat.ESC_PWM", "hat.PI_ESC_PWM"]));
    expect(chain!.expects).toEqual(["esc.SIG"]);
    expect(chain!.ok).toBe(true);
  });

  it("reports a binding whose expected device is not on the net", () => {
    const [chain] = bindingChains({ bad: { interface: "pwm", computer: "pi", pins: ["pi.J8.12"], expects: ["servo.J1.1"] } }, graph);
    expect(chain!.ok).toBe(false);
    expect(pinNet(graph, "pi.5V")).toBe("5V");
  });
});

describe("wiring", () => {
  it("wire rows pick the tightest bend relative to its requirement", () => {
    const rows = wireRows({
      routes: { wires: { W: { harness: "h", awg: 14, routed_length: 0.2, cut_length: 0.25 } } },
      detail: { wires: { W: { harness: "h", color: "blue", bends: [{ radius: 0.01, required: 0.02 }, { radius: 0.004, required: 0.026 }] } },
                wire_min_clearance: { W: { distance: -0.002, component: "wheel_rr" } } },
      currents: [{ id: "W", kind: "wire", current: 12, rating: 25, point: "p" }],
      slack: { wires: { W: { min_distance: 0.025, component: "" } } },
    });
    expect(rows[0]).toMatchObject({ id: "W", bendRadius: 0.004, bendRequired: 0.026, clearance: -0.002, clearanceComponent: "wheel_rr",
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
    sampled.via.forEach((p, i) => expect(p[0]).toBeCloseTo([0.1, 0.2, 0.3, 0.4][i]!, 12));
    expect(sampled.via).toHaveLength(4);
  });
});

describe("scene", () => {
  const graph = {
    components: { esc: { type: "esc", kind: "esc", name: "ESC", mount: "chassis", shape: "box", size: [0.04, 0.03, 0.03], axis: "z", xyz: [0, 0, 0], rpy: [0, 0, 0], color: "#111", moving: false, mass: 0.05 } },
    wires: { W1: { harness: "esc_power", awg: 12, color: "red", from: "A.1", to: "B.1", ends: [[0, 0, 0], [0.1, 0, 0]] } },
    nets: {}, net_names: {},
  } as unknown as ModelGraph;

  it("maps diagnostic subjects to parts and wires", () => {
    const scene = buildScene({ graph, routes: null, diagnostics: [diag({ severity: "error", subjects: ["esc.BEC"] })] });
    expect(resolveSubjects(["esc.BEC", "op:servo_stall@full"], scene)).toEqual({ components: new Set(["esc"]), wires: new Set() });
    expect(resolveSubjects(["esc_power"], scene).wires).toEqual(new Set(["W1"]));
    expect(resolveSubjects(["esc_power.B|power_y.P2#1"], scene).wires).toEqual(new Set(["W1"]));
    expect(scene.wires[0]).toMatchObject({ routed: false, color: wireColor("red"), radius: awgRadius(12) });
  });
});

describe("diagnostics and release gate", () => {
  it("groups worst-first", () => {
    const groups = groupDiagnostics([diag({ stage: "sim", severity: "warning" }), diag({ stage: "wiring", severity: "error" }), diag({ stage: "wiring" })], "stage");
    expect(groups.map((g) => [g.key, g.severity, g.items.length])).toEqual([["wiring", "error", 2], ["sim", "warning", 1]]);
    expect(groupDiagnostics([diag({ subjects: ["a", "b"] })], "subject").map((g) => g.key)).toEqual(["a", "b"]);
  });

  it("fabrication ready only when everything is verified", () => {
    const item = (status: "verified" | "estimated") => ({ id: status, kind: "requirement" as const, label: "", requirement: 1, status, evidence: null });
    expect(fabricationReady([item("verified")])).toBe(true);
    expect(fabricationReady([item("verified"), item("estimated")])).toBe(false);
    expect(fabricationReady([])).toBe(false);
    expect(evidenceCounts([item("verified"), item("estimated")])).toMatchObject({ verified: 1, estimated: 1, failed: 0 });
  });
});

describe("text", () => {
  it("parses quoted CSV", () => {
    expect(parseCsv('a,b\n1,"x, ""y"""\r\n2,\n')).toEqual([{ a: "1", b: 'x, "y"' }, { a: "2", b: "" }]);
  });

  it("diffs robot.yaml lines and elides unchanged context", () => {
    const before = ["a", "b", "c", "d", "e", "f", "g", "h", "i"].join("\n");
    const after = ["a", "b", "c", "d", "E", "f", "g", "h", "i"].join("\n");
    const lines = diffLines(before, after);
    expect(lines.filter((l) => l.op !== " ").map((l) => `${l.op}${l.text}`)).toEqual(["-e", "+E"]);
    const hunks = diffHunks(lines, 1);
    expect(hunks.map((l) => (l ? `${l.op}${l.text}` : "…"))).toEqual([" d", "-e", "+E", " f"]);
  });
});

describe("live updates", () => {
  it("parses SSE frames split across chunks", () => {
    const parser = new SseParser();
    expect(parser.push("event: stage_started\ndata: {\"stage\":")).toEqual([]);
    const out = parser.push("\"wiring\"}\n\n: keep-alive\n\ndata: {\"type\":\"log\",\"message\":\"hi\"}\r\n\r\n");
    expect(out.map((m) => toEvent<{ type: string; stage?: string; message?: string }>(m))).toEqual([
      { type: "stage_started", stage: "wiring" }, { type: "log", message: "hi" },
    ]);
  });

  it("reduces run events into status and progress", () => {
    let run: TrackedRun = { runId: "r", pid: "p", request: {}, status: "queued", stage: null, stagesDone: [], progress: 0, events: [], startedAt: 0 };
    run = reduceRun(run, { type: "stage_started", stage: "wiring" });
    expect([run.status, run.stage]).toEqual(["running", "wiring"]);
    run = reduceRun(run, { type: "stage_finished", stage: "wiring", ok: false });
    run = reduceRun(run, { type: "run_finished", status: "failed" });
    expect(run).toMatchObject({ status: "failed", stage: null, progress: 1, stagesDone: [{ stage: "wiring", ok: false }] });
    expect(run.events).toHaveLength(3);
  });
});
