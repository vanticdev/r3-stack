// Pure geometry transforms shared by the 3D view, playback and tests.
// Robot frame: +x forward, +y left, +z up, metres. robot.yaml `rpy` is R = Rz(yaw)·Ry(pitch)·Rx(roll).
import { Euler, Matrix4, Quaternion, Vector3 } from "three";

import type { GraphComponent, Pose7, SpecJoint, Vec3 } from "../api/types.ts";

export function rpyQuaternion(rpy: Vec3): Quaternion {
  return new Quaternion().setFromEuler(new Euler(rpy[0], rpy[1], rpy[2], "ZYX"));
}

export function poseMatrix(xyz: Vec3, rpy: Vec3 = [0, 0, 0]): Matrix4 {
  return new Matrix4().compose(new Vector3(...xyz), rpyQuaternion(rpy), new Vector3(1, 1, 1));
}

/** MuJoCo pose (x, y, z, qw, qx, qy, qz) -> matrix. */
export function pose7Matrix(pose: Pose7): Matrix4 {
  const [x, y, z, w, qx, qy, qz] = pose;
  return new Matrix4().compose(new Vector3(x, y, z), new Quaternion(qx, qy, qz, w).normalize(), new Vector3(1, 1, 1));
}

/** Rigid motion that carries a body from its nominal pose to `current`: current · nominal⁻¹.
 * Applied to geometry authored in the nominal robot frame (the CAD STLs), it places it in the world. */
export function bodyDelta(nominal: Pose7, current: Pose7): Matrix4 {
  return pose7Matrix(current).multiply(pose7Matrix(nominal).invert());
}

export interface FrameCursor {
  i0: number;
  i1: number;
  alpha: number;
}

/** Bracketing samples of `t` in ascending `times` (clamped at both ends). */
export function frameAt(times: readonly number[], t: number): FrameCursor {
  const n = times.length;
  if (n === 0) return { i0: 0, i1: 0, alpha: 0 };
  if (t <= times[0]!) return { i0: 0, i1: 0, alpha: 0 };
  if (t >= times[n - 1]!) return { i0: n - 1, i1: n - 1, alpha: 0 };
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid]! <= t) lo = mid;
    else hi = mid;
  }
  const span = times[hi]! - times[lo]!;
  return { i0: lo, i1: hi, alpha: span > 0 ? (t - times[lo]!) / span : 0 };
}

export function interpolatePose(a: Pose7, b: Pose7, alpha: number): Pose7 {
  if (alpha <= 0) return a;
  if (alpha >= 1) return b;
  const qa = new Quaternion(a[4], a[5], a[6], a[3]);
  const qb = new Quaternion(b[4], b[5], b[6], b[3]);
  const q = qa.slerp(qb, alpha);
  const lerp = (i: number) => a[i]! + (b[i]! - a[i]!) * alpha;
  return [lerp(0), lerp(1), lerp(2), q.w, q.x, q.y, q.z];
}

export function interpolateValue(values: readonly number[], cursor: FrameCursor): number {
  const a = values[cursor.i0] ?? NaN;
  const b = values[cursor.i1] ?? a;
  return a + (b - a) * cursor.alpha;
}

export function interpolatePoints(a: readonly Vec3[], b: readonly Vec3[], alpha: number): Vec3[] {
  if (alpha <= 0 || a.length !== b.length) return a as Vec3[];
  return a.map((p, i) => {
    const q = b[i]!;
    return [p[0] + (q[0] - p[0]) * alpha, p[1] + (q[1] - p[1]) * alpha, p[2] + (q[2] - p[2]) * alpha];
  });
}

// ------------------------------------------------------------------ mount tree and joints

export type MountTree = Record<string, string | null>;

/** Parent of every component along `mount` (null for the root / world-mounted ones). */
export function mountTree(components: Record<string, Pick<GraphComponent, "mount">>): MountTree {
  const tree: MountTree = {};
  for (const [id, c] of Object.entries(components)) tree[id] = c.mount && c.mount in components && c.mount !== id ? c.mount : null;
  return tree;
}

/** Ancestors from the root down to (and including) `id`. Cycles are cut. */
export function ancestry(tree: MountTree, id: string): string[] {
  const chain: string[] = [];
  const seen = new Set<string>();
  let current: string | null | undefined = id;
  while (current && !seen.has(current)) {
    seen.add(current);
    chain.unshift(current);
    current = tree[current];
  }
  return chain;
}

/** Joints that move each component, ordered root -> leaf. A joint moves its child and the child's
 * mount subtree; the joint's origin and axis are given in the robot frame at q = 0. */
export function jointChains(tree: MountTree, joints: readonly SpecJoint[]): Record<string, SpecJoint[]> {
  const byChild = new Map<string, SpecJoint>();
  for (const joint of joints) if (joint.child in tree && joint.type !== "fixed") byChild.set(joint.child, joint);
  const chains: Record<string, SpecJoint[]> = {};
  for (const id of Object.keys(tree)) {
    chains[id] = ancestry(tree, id).flatMap((ancestor) => {
      const joint = byChild.get(ancestor);
      return joint ? [joint] : [];
    });
  }
  return chains;
}

/** T(o) · R(axis, q) · T(-o) for revolute joints; translation along the axis for prismatic ones. */
export function jointMatrix(joint: SpecJoint, q: number): Matrix4 {
  const axis = new Vector3(...joint.axis).normalize();
  if (joint.type === "prismatic") return new Matrix4().makeTranslation(axis.multiplyScalar(q));
  const origin = new Vector3(...joint.origin);
  return new Matrix4()
    .makeTranslation(origin)
    .multiply(new Matrix4().makeRotationAxis(axis, q))
    .multiply(new Matrix4().makeTranslation(origin.clone().negate()));
}

/** Composite joint motion of one component: J_root(q) · … · J_leaf(q). */
export function chainMatrix(chain: readonly SpecJoint[], positions: Readonly<Record<string, number>>): Matrix4 {
  const out = new Matrix4();
  for (const joint of chain) out.multiply(jointMatrix(joint, positions[joint.id] ?? 0));
  return out;
}

export function clampJoint(joint: SpecJoint, q: number): number {
  if (!joint.limits || joint.type === "continuous") return q;
  return Math.min(Math.max(q, joint.limits[0]), joint.limits[1]);
}

/** Ackermann wheel angles for a commanded centre steer angle δ (positive = left turn):
 * cot δ_outer − cot δ_inner = track / wheelbase, with the centre (bicycle) angle between them. */
export function ackermann(delta: number, wheelbase: number, track: number): { left: number; right: number } {
  if (Math.abs(delta) < 1e-9 || wheelbase <= 0) return { left: delta, right: delta };
  const radius = wheelbase / Math.tan(Math.abs(delta)); // turn radius at the rear-axle centre
  const inner = Math.atan(wheelbase / (radius - track / 2));
  const outer = Math.atan(wheelbase / (radius + track / 2));
  const sign = Math.sign(delta);
  return delta > 0 ? { left: sign * inner, right: sign * outer } : { left: sign * outer, right: sign * inner };
}

// ------------------------------------------------------------------ bodies and exploded view

/** MuJoCo body that carries a component: the component itself or its nearest mount ancestor that is a body. */
export function componentBody(tree: MountTree, id: string, bodies: ReadonlySet<string>, fallback: string | null): string | null {
  const chain = ancestry(tree, id);
  for (let i = chain.length - 1; i >= 0; i--) if (bodies.has(chain[i]!)) return chain[i]!;
  return fallback;
}

export function componentCenter(c: Pick<GraphComponent, "xyz" | "aabb_min" | "aabb_max">): Vec3 {
  if (c.aabb_min && c.aabb_max) {
    return [(c.aabb_min[0] + c.aabb_max[0]) / 2, (c.aabb_min[1] + c.aabb_max[1]) / 2, (c.aabb_min[2] + c.aabb_max[2]) / 2];
  }
  return c.xyz;
}

/** Exploded-view offsets: each part moves away from its mount parent along the join direction
 * (parent centre -> child centre), accumulated down the tree, scaled by `factor`. */
export function explodeOffsets(components: Record<string, Pick<GraphComponent, "mount" | "xyz" | "aabb_min" | "aabb_max">>,
                               factor: number): Record<string, Vec3> {
  const tree = mountTree(components);
  const offsets: Record<string, Vec3> = {};
  const resolve = (id: string, depth = 0): Vec3 => {
    const cached = offsets[id];
    if (cached) return cached;
    const parent = tree[id];
    if (!parent || depth > 64) return (offsets[id] = [0, 0, 0]);
    const base = resolve(parent, depth + 1);
    const a = componentCenter(components[parent]!);
    const b = componentCenter(components[id]!);
    let d: Vec3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    // Parts stacked concentrically (e.g. a HAT on a Pi) still separate along +z.
    if (Math.hypot(...d) < 1e-4) d = [0, 0, 0.02];
    return (offsets[id] = [base[0] + d[0] * factor, base[1] + d[1] * factor, base[2] + d[2] * factor]);
  };
  for (const id of Object.keys(components)) resolve(id);
  return offsets;
}

/** Axis-aligned bounds of a set of points (null when empty). */
export function bounds(points: readonly Vec3[]): { min: Vec3; max: Vec3 } | null {
  if (!points.length) return null;
  const min: Vec3 = [Infinity, Infinity, Infinity];
  const max: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const p of points) {
    for (let i = 0; i < 3; i++) {
      min[i] = Math.min(min[i]!, p[i]!);
      max[i] = Math.max(max[i]!, p[i]!);
    }
  }
  return { min, max };
}

/** Evenly spaced handles along a polyline (by arc length), always including both ends. */
export function resamplePolyline(points: readonly Vec3[], count: number): Vec3[] {
  if (points.length < 2 || count < 2) return points.slice() as Vec3[];
  const cumulative = [0];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    cumulative.push(cumulative[i - 1]! + Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]));
  }
  const total = cumulative[cumulative.length - 1]!;
  const out: Vec3[] = [];
  for (let k = 0; k < count; k++) {
    const s = (total * k) / (count - 1);
    const cursor = frameAt(cumulative, s);
    out.push(interpolatePoints([points[cursor.i0]!], [points[cursor.i1]!], cursor.alpha)[0]!);
  }
  return out;
}
