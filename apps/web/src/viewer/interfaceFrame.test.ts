import { describe, expect, it } from "vitest";

import { interfaceFrame } from "./interfaceFrame.ts";

describe("assembly interface frames", () => {
  it("extracts translation and rotated x/z columns from the compiler's matrix rows", () => {
    expect(interfaceFrame({ kind: "shaft", frame: [
      [0, 0, 1, 0.12],
      [1, 0, 0, -0.03],
      [0, 1, 0, 0.08],
      [0, 0, 0, 1],
    ] })).toEqual({ origin: [0.12, -0.03, 0.08], x: [0, 1, 0], z: [1, 0, 0] });
  });

  it("preserves legacy origin/z/x, including an omitted optional x", () => {
    const legacy = { origin: [1, 2, 3] as [number, number, number], z: [0, 1, 0] as [number, number, number], x: [0, 0, 1] as [number, number, number] };
    expect(interfaceFrame(legacy)).toEqual(legacy);
    expect(interfaceFrame({ origin: legacy.origin, z: legacy.z })).toEqual({ origin: legacy.origin, z: legacy.z, x: undefined });
  });

  it("skips incomplete or non-finite matrices instead of crashing the overlay", () => {
    expect(interfaceFrame({ frame: [[1, 0, 0, 0]] })).toBeNull();
    expect(interfaceFrame({ frame: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, NaN], [0, 0, 0, 1]] })).toBeNull();
  });
});
