import { Vector3 } from "three";
import { describe, expect, it } from "vitest";

import type { Pose7, SpecJoint, Vec3 } from "../api/types.ts";
import {
  ackermann, ancestry, bodyDelta, bounds, chainMatrix, clampJoint, componentBody, explodeOffsets, frameAt, interpolatePoints,
  interpolatePose, jointChains, jointMatrix, mountTree, poseMatrix, resamplePolyline, rpyQuaternion,
} from "./transforms.ts";

const apply = (m: ReturnType<typeof poseMatrix>, p: Vec3) => new Vector3(...p).applyMatrix4(m).toArray();
const close = (a: number[], b: number[], digits = 9) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, digits));

// RC-car-like tree: chassis <- arm <- knuckle <- wheel; servo <- tie_rod.
const components = {
  chassis: { mount: "world", xyz: [0.13, 0, 0.025] as Vec3 },
  arm_fl: { mount: "chassis", xyz: [0.26, 0.0475, 0.025] as Vec3 },
  knuckle_fl: { mount: "arm_fl", xyz: [0.26, 0.0675, 0.035] as Vec3 },
  wheel_fl: { mount: "knuckle_fl", xyz: [0.26, 0.1, 0.055] as Vec3 },
  servo: { mount: "chassis", xyz: [0.25, 0, 0.028] as Vec3 },
};
const steer: SpecJoint = { id: "steer_fl", type: "revolute", parent: "chassis", child: "knuckle_fl", origin: [0.26, 0.0675, 0.055], axis: [0, 0, 1], limits: [-0.45, 0.45] };
const spin: SpecJoint = { id: "spin_fl", type: "continuous", parent: "knuckle_fl", child: "wheel_fl", origin: [0.26, 0.1, 0.055], axis: [0, 1, 0] };

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
    const nominal: Pose7 = [0.26, 0.1, 0.0555, 1, 0, 0, 0];
    const s = Math.SQRT1_2;
    const current: Pose7 = [5.26, 1.1, 0.0555, s, 0, 0, s]; // translated + yawed 90° about the wheel centre
    const delta = bodyDelta(nominal, current);
    close(apply(delta, [0.26, 0.1, 0.0555]), [5.26, 1.1, 0.0555]); // body origin lands on the recorded position
    close(apply(delta, [0.36, 0.1, 0.0555]), [5.26, 1.2, 0.0555]); // +x of the body now points +y
  });

  it("identity when the body is at its nominal pose", () => {
    const pose: Pose7 = [0.1, -0.2, 0.3, 0.9238795, 0, 0.3826834, 0];
    close(apply(bodyDelta(pose, pose), [0.4, 0.5, 0.6]), [0.4, 0.5, 0.6], 6);
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
    expect(tree.chassis).toBeNull();
    expect(ancestry(tree, "wheel_fl")).toEqual(["chassis", "arm_fl", "knuckle_fl", "wheel_fl"]);
    expect(ancestry({ a: "b", b: "a" }, "a")).toEqual(["b", "a"]); // cycles are cut
  });

  it("joint chains move the child subtree only", () => {
    const chains = jointChains(tree, [steer, spin]);
    expect(chains.wheel_fl!.map((j) => j.id)).toEqual(["steer_fl", "spin_fl"]);
    expect(chains.knuckle_fl!.map((j) => j.id)).toEqual(["steer_fl"]);
    expect(chains.arm_fl).toEqual([]);
    expect(chains.servo).toEqual([]);
  });

  it("revolute joint rotates about its axis through its origin", () => {
    const m = jointMatrix(steer, Math.PI / 2);
    close(apply(m, steer.origin), steer.origin); // the axis point stays fixed
    // The wheel centre is 32.5 mm left of the kingpin; steering 90° left swings it to the rear.
    close(apply(m, [0.26, 0.1, 0.055]), [0.26 - 0.0325, 0.0675, 0.055]);
  });

  it("chains compose root-first: wheel spin happens in the steered frame", () => {
    const chains = jointChains(tree, [steer, spin]);
    const q = { steer_fl: Math.PI / 2, spin_fl: Math.PI / 2 };
    const m = chainMatrix(chains.wheel_fl!, q);
    // A point on the tyre's front (+x of the wheel centre) spins down (−z), then steering rotates the wheel centre.
    const point = apply(m, [0.26 + 0.055, 0.1, 0.055]);
    close(point, [0.26 - 0.0325, 0.0675, 0.055 - 0.055]);
  });

  it("prismatic joints translate and limits clamp", () => {
    const slide: SpecJoint = { id: "s", type: "prismatic", parent: "a", child: "b", origin: [0, 0, 0], axis: [0, 0, 2], limits: [0, 0.1] };
    close(apply(jointMatrix(slide, 0.05), [1, 1, 1]), [1, 1, 1.05]);
    expect(clampJoint(slide, 0.5)).toBe(0.1);
    expect(clampJoint(spin, 99)).toBe(99);
  });

  it("componentBody walks up to the nearest simulated body", () => {
    const bodies = new Set(["chassis", "knuckle_fl", "wheel_fl"]);
    expect(componentBody(tree, "wheel_fl", bodies, "chassis")).toBe("wheel_fl");
    expect(componentBody(tree, "arm_fl", bodies, "chassis")).toBe("chassis");
    expect(componentBody(tree, "servo", new Set(), "chassis")).toBe("chassis");
  });
});

describe("Ackermann steering", () => {
  it("inner wheel steers more and satisfies cot δo − cot δi = track / wheelbase", () => {
    const wheelbase = 0.26, track = 0.135;
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
    expect(offsets.chassis).toEqual([0, 0, 0]);
    close(offsets.arm_fl!, [0.13, 0.0475, 0]);
    close(offsets.knuckle_fl!, [0.13, 0.0675, 0.01]);
    expect(explodeOffsets(components, 0).wheel_fl).toEqual([0, 0, 0]);
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
