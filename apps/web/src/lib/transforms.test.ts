import { Vector3 } from "three";
import { describe, expect, it } from "vitest";

import type { Pose7, SpecJoint, Vec3 } from "../api/types.ts";
import {
  ackermann, ancestry, bodyDelta, bounds, chainMatrix, clampJoint, componentBody, explodeOffsets, frameAt, interpolatePoints,
  interpolatePose, jointChains, jointMatrix, mountTree, poseMatrix, resamplePolyline, restPoses, rpyQuaternion,
} from "./transforms.ts";

const apply = (m: ReturnType<typeof poseMatrix>, p: Vec3) => new Vector3(...p).applyMatrix4(m).toArray();
const close = (a: number[], b: number[], digits = 9) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, digits));

// Instrument tree: base <- support <- pivot <- sensor, with a separate actuator.
const components = {
  base: { mount: "world", xyz: [0.02, 0, 0.01] as Vec3 },
  support: { mount: "base", xyz: [0.04, 0.01, 0.01] as Vec3 },
  pivot_left: { mount: "support", xyz: [0.04, 0.03, 0.05] as Vec3 },
  sensor: { mount: "pivot_left", xyz: [0.04, 0.08, 0.09] as Vec3 },
  servo: { mount: "base", xyz: [0.06, 0, 0.01] as Vec3 },
};
const steer: SpecJoint = { id: "pivot", type: "revolute", parent: "base", child: "pivot_left", origin: [0.04, 0.03, 0.09], axis: [0, 0, 1], limits: [-0.6, 0.6] };
const spin: SpecJoint = { id: "roll", type: "continuous", parent: "pivot_left", child: "sensor", origin: [0.04, 0.08, 0.09], axis: [0, 1, 0] };

describe("robot.yaml poses", () => {
  it("rpy is R = Rz(yaw)·Ry(pitch)·Rx(roll)", () => {
    // yaw 90°: +x maps to +y.
    close(new Vector3(1, 0, 0).applyQuaternion(rpyQuaternion([0, 0, Math.PI / 2])).toArray(), [0, 1, 0]);
    // roll then yaw: +y -> (roll 90°) +z -> (yaw 90°) stays +z.
    close(new Vector3(0, 1, 0).applyQuaternion(rpyQuaternion([Math.PI / 2, 0, Math.PI / 2])).toArray(), [0, 0, 1]);
    close(apply(poseMatrix([1, 2, 3], [0, 0, Math.PI]), [1, 0, 0]), [0, 2, 3]);
  });
});

describe("playback body poses", () => {
  it("bodyDelta carries nominal-frame geometry to the recorded world pose", () => {
    const nominal: Pose7 = [0.1, 0.1, 0.12, 1, 0, 0, 0];
    const s = Math.SQRT1_2;
    const current: Pose7 = [5.1, 1.1, 0.12, s, 0, 0, s]; // translated + yawed 90° about the sensor centre
    const delta = bodyDelta(nominal, current);
    close(apply(delta, [0.1, 0.1, 0.12]), [5.1, 1.1, 0.12]); // body origin lands on the recorded position
    close(apply(delta, [0.2, 0.1, 0.12]), [5.1, 1.2, 0.12]); // +x of the body now points +y
  });

  it("identity when the body is at its nominal pose", () => {
    const pose: Pose7 = [0.1, -0.2, 0.3, 0.9238795, 0, 0.3826834, 0];
    close(apply(bodyDelta(pose, pose), [0.4, 0.5, 0.6]), [0.4, 0.5, 0.6], 6);
  });

  it("restPoses: server-provided rest poses win; otherwise bodies relative to the root at frame 0", () => {
    const s = Math.SQRT1_2;
    // At t0 the robot already sits at (5, 1) yawed 90°; the wheel is 0.1 m to the robot's left (+y) => world -x.
    const frames = {
      bodies: {
        base: [[5, 1, 0, s, 0, 0, s], [6, 1, 0, s, 0, 0, s]] as Pose7[],
        probe: [[4.9, 1, 0.12, s, 0, 0, s]] as Pose7[],
      },
    };
    const rest = restPoses(frames, "base");
    expect(rest.base).toEqual([0, 0, 0, 1, 0, 0, 0]);
    close(rest.probe!.slice(0, 3), [0, 0.1, 0.12], 9);
    close(rest.probe!.slice(3), [1, 0, 0, 0], 9);
    // Placing the sensor mesh (authored at rest) with bodyDelta lands it on the recorded pose.
    close(apply(bodyDelta(rest.probe!, frames.bodies.probe[0]!), [0, 0.1, 0.12]), [4.9, 1, 0.12]);
    const given = { ...frames, rest: { base: [0, 0, 0.0005, 1, 0, 0, 0] as Pose7 } };
    expect(restPoses(given, "base")).toBe(given.rest);
  });

  it("frameAt brackets and clamps", () => {
    const t = [0, 0.1, 0.2, 0.3];
    expect(frameAt(t, -1)).toEqual({ i0: 0, i1: 0, alpha: 0 });
    expect(frameAt(t, 9)).toEqual({ i0: 3, i1: 3, alpha: 0 });
    const c = frameAt(t, 0.15);
    expect([c.i0, c.i1]).toEqual([1, 2]);
    expect(c.alpha).toBeCloseTo(0.5);
    expect(frameAt([], 1)).toEqual({ i0: 0, i1: 0, alpha: 0 });
  });

  it("interpolatePose lerps position and slerps rotation", () => {
    const a: Pose7 = [0, 0, 0, 1, 0, 0, 0];
    const b: Pose7 = [2, 0, 0, 0, 0, 0, 1]; // 180° yaw
    const mid = interpolatePose(a, b, 0.5);
    close(mid.slice(0, 3), [1, 0, 0]);
    close([mid[3], mid[6]], [Math.SQRT1_2, Math.SQRT1_2], 6); // 90° yaw
    expect(interpolatePose(a, b, 0)).toBe(a);
    expect(interpolatePose(a, b, 1)).toBe(b);
  });

  it("interpolatePoints blends cable polylines", () => {
    expect(interpolatePoints([[0, 0, 0], [1, 1, 1]], [[2, 0, 0], [1, 3, 1]], 0.5)).toEqual([[1, 0, 0], [1, 2, 1]]);
  });
});

describe("mount tree and joints", () => {
  const tree = mountTree(components);

  it("builds parents and ancestry (world mount is a root)", () => {
    expect(tree.base).toBeNull();
    expect(ancestry(tree, "sensor")).toEqual(["base", "support", "pivot_left", "sensor"]);
    expect(ancestry({ a: "b", b: "a" }, "a")).toEqual(["b", "a"]); // cycles are cut
  });

  it("joint chains move the child subtree only", () => {
    const chains = jointChains(tree, [steer, spin]);
    expect(chains.sensor!.map((j) => j.id)).toEqual(["pivot", "roll"]);
    expect(chains.pivot_left!.map((j) => j.id)).toEqual(["pivot"]);
    expect(chains.support).toEqual([]);
    expect(chains.servo).toEqual([]);
  });

  it("revolute joint rotates about its axis through its origin", () => {
    const m = jointMatrix(steer, Math.PI / 2);
    close(apply(m, steer.origin), steer.origin); // the axis point stays fixed
    // A sensor 50 mm along +y swings toward -x under a 90° pivot.
    close(apply(m, [0.04, 0.08, 0.09]), [-0.01, 0.03, 0.09]);
  });

  it("chains compose root-first: sensor rotation happens in the steered frame", () => {
    const chains = jointChains(tree, [steer, spin]);
    const q = { pivot: Math.PI / 2, roll: Math.PI / 2 };
    const m = chainMatrix(chains.sensor!, q);
    // The probe's +x offset rotates down, then the parent pivot rotates its centre.
    const point = apply(m, [0.08, 0.08, 0.09]);
    close(point, [-0.01, 0.03, 0.05]);
  });

  it("prismatic joints translate and limits clamp", () => {
    const slide: SpecJoint = { id: "s", type: "prismatic", parent: "a", child: "b", origin: [0, 0, 0], axis: [0, 0, 2], limits: [0, 0.1] };
    close(apply(jointMatrix(slide, 0.05), [1, 1, 1]), [1, 1, 1.05]);
    expect(clampJoint(slide, 0.5)).toBe(0.1);
    expect(clampJoint(spin, 99)).toBe(99);
  });

  it("componentBody walks up to the nearest simulated body", () => {
    const bodies = new Set(["base", "pivot_left", "sensor"]);
    expect(componentBody(tree, "sensor", bodies, "base")).toBe("sensor");
    expect(componentBody(tree, "support", bodies, "base")).toBe("base");
    expect(componentBody(tree, "servo", new Set(), "base")).toBe("base");
  });
});

describe("Ackermann steering", () => {
  it("inner wheel steers more and satisfies cot δo − cot δi = track / wheelbase", () => {
    const wheelbase = 0.4, track = 0.16;
    const { left, right } = ackermann(0.3, wheelbase, track);
    expect(left).toBeGreaterThan(right); // left turn: left wheel is inner
    expect(1 / Math.tan(right) - 1 / Math.tan(left)).toBeCloseTo(track / wheelbase, 9);
    const mirrored = ackermann(-0.3, wheelbase, track);
    expect(mirrored.left).toBeCloseTo(-right, 12);
    expect(mirrored.right).toBeCloseTo(-left, 12);
    expect(ackermann(0, wheelbase, track)).toEqual({ left: 0, right: 0 });
  });
});

describe("exploded view", () => {
  it("offsets accumulate along the joins from the root", () => {
    const offsets = explodeOffsets(components, 1);
    expect(offsets.base).toEqual([0, 0, 0]);
    close(offsets.support!, [0.02, 0.01, 0]);
    close(offsets.pivot_left!, [0.02, 0.03, 0.04]);
    expect(explodeOffsets(components, 0).sensor).toEqual([0, 0, 0]);
  });

  it("concentric parts still separate along +z", () => {
    const stacked = { pi: { mount: "world", xyz: [0, 0, 0] as Vec3 }, hat: { mount: "pi", xyz: [0, 0, 0] as Vec3 } };
    expect(explodeOffsets(stacked, 1).hat).toEqual([0, 0, 0.02]);
  });
});

describe("geometry helpers", () => {
  it("bounds", () => {
    expect(bounds([])).toBeNull();
    expect(bounds([[0, 1, 2], [-1, 5, 0]])).toEqual({ min: [-1, 1, 0], max: [0, 5, 2] });
  });

  it("resamplePolyline spaces handles by arc length and keeps the ends", () => {
    const out = resamplePolyline([[0, 0, 0], [1, 0, 0], [1, 1, 0]], 5);
    expect(out).toHaveLength(5);
    close(out[0]!, [0, 0, 0]);
    close(out[2]!, [1, 0, 0]);
    close(out[3]!, [1, 0.5, 0]);
    close(out[4]!, [1, 1, 0]);
  });
});
