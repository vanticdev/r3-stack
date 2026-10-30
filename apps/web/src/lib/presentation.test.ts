// Human-facing presentation helpers: labels, project status, release grouping, environment causes,
// rail tolerances and the orthogonal power-tree drawing.
import { describe, expect, it } from "vitest";

import type { DoctorPayload, Evidence, ProjectDetail, ReleaseItem } from "../api/types.ts";
import { drawPowerTree, powerTree, railFlag, railLimits } from "./domain.ts";
import { environmentCauses } from "./environment.ts";
import { diagnosticFacts, diagnosticHeadline, diagnosticType, opLabel, partLabel, stageLabel } from "./labels.ts";
import { groupRelease, itemLabel, itemResult, itemUnit, releaseSummary } from "./release.ts";
import { countsText, projectStatus } from "./status.ts";

const ev = (over: Partial<Evidence>): Evidence => ({
  stage: "sim", check: "x", passed: true, value: 1, limit: null, artifact: null, inputs: {}, estimated: false, stale: false, detail: "", ...over,
});
const item = (over: Partial<ReleaseItem>): ReleaseItem => ({ id: "requirement.x", kind: "requirement", subject: "x", target: 1, status: "verified", evidence: [], message: "", ...over });

describe("labels", () => {
  it("names parts, harnesses and operating points for people", () => {
    expect(partLabel("pivot_right")).toBe("front-right knuckle");
    expect(partLabel("supply")).toBe("battery lead");
    expect(partLabel("distribution")).toBe("power Y");
    expect(partLabel("actuator_power")).toBe("ESC motor");
    expect(partLabel("pi")).toBe("Pi");
    expect(partLabel("W_BAT_P")).toBe("W_BAT_P");
    expect(partLabel("esc.BEC")).toBe("esc.BEC");
    expect(opLabel("motor_stall@minimum")).toBe("motor stall · minimum charge");
    expect(opLabel("op:servo_stall_compute@full")).toBe("servo stall compute · full charge");
  });

  it("uses the navigation's names for stages", () => {
    expect(stageLabel("sim")).toBe("Simulation");
    expect(stageLabel("model")).toBe("3D model");
    expect(stageLabel("pcb")).toBe("PCB");
  });

  it("turns diagnostics into headlines and measured facts", () => {
    const d = {
      code: "cable_sags_into_moving_part", stage: "wiring", subjects: ["actuator_signal", "W_SRV_SIG"],
      message: "actuator_signal cable enters the pivot_right envelope by 2.3 mm during the slack simulation (t = 1.597 s)",
      data: { min_distance: -0.00234, required: 0.008, component: "pivot_right", time: 1.597 },
    };
    expect(diagnosticType(d.code)).toBe("Cable sags into moving part");
    expect(diagnosticHeadline(d)).toBe("Servo lead cable enters the front-right knuckle envelope by 2.3 mm during the slack simulation (t = 1.597 s)");
    const facts = diagnosticFacts(d);
    expect(facts).toMatchObject({ cable: "servo lead", part: "front-right knuckle", amount: "2.34 mm inside", limit: "needs ≥ 8 mm", time: 1.597 });
    expect(diagnosticHeadline({ message: "esc BEC supplies 2.38 A at servo_stall_compute@full > 2.0 A rating", subjects: ["esc", "op:servo_stall_compute@full"] }))
      .toBe("ESC BEC supplies 2.38 A at servo stall compute · full charge > 2.0 A rating");
    expect(diagnosticType("some_new_code")).toBe("Some new code");
  });
});

describe("project status", () => {
  const project = {
    stages: [
      { stage: "model", status: "ok", ok: true, stale: false, counts: { error: 0 } },
      { stage: "wiring", status: "failed", ok: false, stale: false, counts: { error: 52, warning: 42 } },
      { stage: "pcb", status: "failed", ok: false, stale: false, counts: { error: 5 } },
      { stage: "sim", status: "ok", ok: true, stale: true, counts: {} },
      { stage: "cad", status: "missing", ok: null },
      { stage: "report", status: "ok", ok: true },
    ],
    release: { fabrication_ready: false },
  } as unknown as Pick<ProjectDetail, "stages" | "release">;

  it("summarizes result and freshness separately", () => {
    const s = projectStatus(project);
    expect(s.headline).toBe("Not ready");
    expect(s.parts).toEqual(["2 stages failed", "Simulation results outdated"]);
    expect(s.stages.map((x) => x.stage)).not.toContain("report");
    expect(s.stages.find((x) => x.stage === "sim")).toMatchObject({ outcome: "passed", outdated: true });
    expect(s.stages.find((x) => x.stage === "cad")).toMatchObject({ outcome: "not_run", outdated: false });
    expect(projectStatus(project, "wiring").stages.find((x) => x.stage === "wiring")!.outcome).toBe("running");
  });

  it("spells counts out", () => {
    expect(countsText({ error: 52, warning: 1 })).toBe("52 errors, 1 warning");
    expect(countsText({})).toBe("no issues");
  });
});

describe("release presentation", () => {
  const topSpeed = item({ id: "requirement.top_speed", subject: "top_speed", target: 8, status: "stale",
    evidence: [ev({ check: "full_throttle.min_top_speed_mps", value: 8, stale: true })] });
  const scenarioSpeed = item({ id: "scenario.full_throttle.min_top_speed_mps", kind: "scenario_expectation", subject: "full_throttle", target: 8, status: "stale",
    evidence: [ev({ check: "full_throttle.min_top_speed_mps", value: 8, stale: true })] });
  const brownouts = item({ id: "scenario.full_throttle.brownouts", kind: "scenario_expectation", subject: "full_throttle", target: 0,
    evidence: [ev({ check: "full_throttle.brownouts", value: 0 })] });
  const drop = item({ id: "requirement.max_wire_voltage_drop_fraction", subject: "max_wire_voltage_drop_fraction", target: 0.03, status: "failed",
    evidence: [ev({ stage: "electrical", check: "voltage_drop_fraction", value: 0.0419, passed: false, detail: "2 harness(es) exceed the limit" })] });
  const clearance = item({ id: "requirement.min_wire_clearance_moving", subject: "min_wire_clearance_moving", target: 0.008, status: "failed",
    evidence: [ev({ stage: "wiring", check: "wire_min_clearance_moving", value: 0.0023, passed: false })] });
  const sweep = item({ id: "scenario.steering_sweep.min_wire_clearance_m", kind: "scenario_expectation", subject: "steering_sweep", target: 0.008, status: "not_run" });
  const stage = item({ id: "stage.pcb", kind: "stage", subject: "pcb", target: "no errors", status: "failed", evidence: [ev({ stage: "pcb", check: "errors", value: 5, passed: false })] });

  it("labels items with engineering names and units (scenario keys come from the id)", () => {
    expect(itemLabel(drop)).toBe("Wire voltage drop");
    expect(itemResult(drop)).toEqual({ value: "4.19%", target: "≤ 3.00%" });
    expect(itemUnit(scenarioSpeed)).toBe("m/s");
    expect(itemLabel(scenarioSpeed)).toBe("Top speed");
    expect(itemResult(stage).value).toBe("5 errors");
    expect(itemResult(sweep).value).toBe("not checked yet");
  });

  it("groups scenario evidence under the requirement it backs", () => {
    const g = groupRelease([topSpeed, scenarioSpeed, brownouts, drop, clearance, sweep, stage]);
    expect(g.requirements.map((r) => r.item.id)).toEqual(["requirement.max_wire_voltage_drop_fraction", "requirement.min_wire_clearance_moving", "requirement.top_speed"]);
    expect(g.requirements.find((r) => r.item.subject === "top_speed")!.related.map((r) => r.id)).toEqual([scenarioSpeed.id]);
    expect(g.requirements.find((r) => r.item.subject === "min_wire_clearance_moving")!.related.map((r) => r.id)).toEqual([sweep.id]);
    expect(g.scenarios).toEqual([{ scenario: "full_throttle", items: [brownouts] }]);
    expect(g.stages).toEqual([stage]);
  });

  it("writes one readiness summary", () => {
    expect(releaseSummary({ failed: 5, stale: 4, estimated: 2, not_run: 15, verified: 2 }, false))
      .toEqual({ headline: "Not ready for fabrication", parts: ["5 failed", "4 outdated", "2 estimated", "15 not checked", "2 verified"] });
  });
});

describe("environment", () => {
  const doctor = (stages: DoctorPayload["stages"], tools: DoctorPayload["tools"] = {}): DoctorPayload => ({
    ok: false, environment: { python: "", platform: "", display: false, worker_image: false, ci: false }, tools, stages,
  });
  it("leads with the root cause and groups the stages it affects", () => {
    const stages = ["model", "pcb", "wiring", "sim"].map((stage) => ({ stage, headless: true, level: "degraded" as const, missing: ["r3 serve"], note: "" }));
    const causes = environmentCauses(doctor(stages));
    expect(causes).toHaveLength(1);
    expect(causes[0]!.headline).toBe("Build service unavailable — stages cannot execute");
    expect(causes[0]!.stages).toHaveLength(4);
  });
  it("names a single missing tool and its fix", () => {
    const causes = environmentCauses(doctor(
      [{ stage: "pcb", headless: true, level: "degraded", missing: ["freerouting"], note: "" }, { stage: "model", headless: true, level: "full", missing: [], note: "" }],
      { freerouting: { name: "freerouting", available: false, version: null, detail: "jar not found (set R3_FREEROUTING_JAR)", data: {} } },
    ));
    expect(causes.map((c) => c.headline)).toEqual(["Freerouting is not installed — PCB runs with reduced fidelity"]);
    expect(causes[0]!.fix).toMatch(/R3_FREEROUTING_JAR/);
    expect(causes[0]!.tool?.detail).toMatch(/jar not found/);
  });
});

describe("rails and the power tree", () => {
  it("flags rail voltages in both directions, assuming ±5 % when no tolerance is stated", () => {
    const stated = railLimits({ nominal: 7.4, min: 6.0, max: 8.4 });
    expect(stated).toMatchObject({ lo: 6.0, hi: 8.4, loStated: true, hiStated: true });
    expect(railFlag(5.9, stated)).toBe("low");
    expect(railFlag(8.5, stated)).toBe("high");
    const unstated = railLimits({ nominal: 3.3, min: 3.3, max: 3.3 });
    expect(unstated.loStated || unstated.hiStated).toBe(false);
    expect(railFlag(3.47, unstated)).toBe("high");
    expect(railFlag(3.35, unstated)).toBeNull();
    expect(railFlag(undefined, unstated)).toBeNull();
  });

  it("draws loads under their supply with one bus per supply pin and back-feeds in a side lane", () => {
    const tree = powerTree([
      { source: "battery.POS", nominal: 7.4, min: 6, max: 8.4, sinks: ["esc.VIN", "hat.VBAT"] },
      { source: "esc.BEC", nominal: 6, min: 6, max: 6, sinks: ["servo.VCC"] },
      { source: "hat.5V", nominal: 5.1, min: 5.1, max: 5.1, sinks: ["gps.5V", "pi.5V", "receiver.5V"] },
      { source: "pi.3V3", nominal: 3.3, min: 3.3, max: 3.3, sinks: ["encoder.VCC", "hat.3V3"] },
    ]);
    const d = drawPowerTree(tree);
    const at = Object.fromEntries(d.nodes.map((n) => [n.id, n]));
    expect(at.battery!.y).toBeLessThan(at.esc!.y);
    expect(at.esc!.y).toBe(at.hat!.y);
    expect(at.pi!.y).toBeGreaterThan(at.hat!.y);
    // Subtrees do not interleave: everything the ESC feeds is left of everything the HAT feeds.
    expect(at.servo!.x).toBeLessThan(Math.min(at.gps!.x, at.pi!.x, at.receiver!.x));
    expect(d.buses.map((b) => b.source)).toEqual(["battery.POS", "esc.BEC", "hat.5V", "pi.3V3"]);
    for (const b of d.buses) for (const drop of b.drops) expect(drop.path[0]![0]).toBe(drop.path[1]![0]); // drops are vertical
    expect(d.backEdges.map((e) => `${e.source}>${e.to}`)).toEqual(["pi.3V3>hat"]);
    const lane = d.backEdges[0]!.path;
    expect(Math.max(...lane.map((p) => p[0]))).toBeGreaterThan(Math.max(...d.nodes.map((n) => n.x)));
  });
});
