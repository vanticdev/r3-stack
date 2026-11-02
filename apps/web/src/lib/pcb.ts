import type { FileLink } from "../api/types.ts";

/** Select named outputs explicitly: a copper SVG is never a 3D board render. */
export function pcbRender(files: FileLink[], view: "3d" | "copper", side: "top" | "bottom"): FileLink | undefined {
  const stem = view === "3d" ? `render_${side}` : `copper_${side}`;
  return files.find((file) => new RegExp(`(?:^|/)${stem}\\.(?:png|svg)$`, "i").test(file.path))
    ?? files.find((file) => file.name === stem);
}
