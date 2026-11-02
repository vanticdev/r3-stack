// Parsing and building the picking accelerator must not block pointer events.
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { CENTER, MeshBVH } from "three-mesh-bvh";

self.onmessage = async ({ data: url }: MessageEvent<string>) => {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Mesh ${response.status}: ${url}`);
    const geometry = new STLLoader().parse(await response.arrayBuffer());
    const tree = new MeshBVH(geometry, { strategy: CENTER });
    const bvh = MeshBVH.serialize(tree, { cloneBuffers: false });
    geometry.computeBoundingSphere();
    const sphere = { center: geometry.boundingSphere!.center.toArray(), radius: geometry.boundingSphere!.radius };
    const position = geometry.getAttribute("position").array;
    const normal = geometry.getAttribute("normal").array;
    const transfers = new Set<ArrayBuffer>([position.buffer as ArrayBuffer, normal.buffer as ArrayBuffer,
      ...bvh.roots, bvh.index!.buffer as ArrayBuffer]);
    self.postMessage({ position, normal, bvh, sphere }, { transfer: [...transfers] });
  } catch (error) {
    self.postMessage({ error: String(error) });
  }
};
