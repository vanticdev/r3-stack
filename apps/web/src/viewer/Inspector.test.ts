import { describe, expect, it } from "vitest";
import type { Diagnostic } from "../api/types.ts";
import type { RobotData } from "./useRobotScene.ts";
import { partDiagnostics } from "./Inspector.tsx";

describe("part diagnostic prefix index", () => {
  it("deduplicates own and pin findings, preserves dotted IDs and excludes sibling prefixes", () => {
    const warning = { severity: "warning" } as Diagnostic;
    const error = { severity: "error" } as Diagnostic;
    const data = { bySubject: { motor: [warning], "motor.pin": [warning, error], "motor.pin.contact": [error], motor2: [error] } } as unknown as RobotData;
    expect(partDiagnostics(data, "motor")).toEqual([error, warning]);
    expect(partDiagnostics(data, "motor.pin")).toEqual([error, warning]);
    expect(partDiagnostics(data, "missing")).toEqual([]);
    expect(partDiagnostics(data, "motor")).toBe(partDiagnostics(data, "motor"));
    const fresh = { bySubject: { motor: [warning] } } as unknown as RobotData;
    expect(partDiagnostics(fresh, "motor")).toEqual([warning]);
  });
});
