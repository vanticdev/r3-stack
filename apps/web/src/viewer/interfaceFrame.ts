import type { AssemblyInterface, Vec3 } from "../api/types.ts";

/** assembly.py frame_matrix/to_json writes nested matrix rows: axes are columns
 * 0/2 (x/z), and translation is column 3. Preserve legacy explicit axes too. */
export function interfaceFrame(value: AssemblyInterface): { origin: Vec3; z: Vec3; x?: Vec3 } | null {
  if ("frame" in value) {
    const frame = value.frame;
    if (frame.length !== 4 || !frame.every((row) => Array.isArray(row) && row.length === 4 && row.every(Number.isFinite))) return null;
    const column = (index: number): Vec3 => [frame[0]![index]!, frame[1]![index]!, frame[2]![index]!];
    return { origin: column(3), z: column(2), x: column(0) };
  }
  return { origin: value.origin, z: value.z, x: value.x };
}
