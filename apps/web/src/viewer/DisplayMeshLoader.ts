import { BufferAttribute, BufferGeometry, Loader, Sphere, Vector3 } from "three";
import { MeshBVH } from "three-mesh-bvh";

type Job = { url: string; resolve: (geometry: BufferGeometry) => void; reject: (error: Error) => void };
const queue: Job[] = [];
let active = 0;
const idle: Worker[] = [];
// Bound concurrent decoding and GPU uploads; never launch one worker per part.
function drain() {
  while (active < 2 && queue.length) {
    const job = queue.shift()!;
    active++;
    const worker = idle.pop() ?? new Worker(new URL("./displayMesh.worker.ts", import.meta.url), { type: "module" });
    const finish = () => {
      worker.onmessage = null;
      worker.onerror = null;
      idle.push(worker); active--; drain();
      setTimeout(() => { const i = idle.indexOf(worker); if (i >= 0) { idle.splice(i, 1); worker.terminate(); } }, 1000);
    };
    worker.onerror = (event) => { job.reject(new Error(event.message)); worker.terminate(); active--; drain(); };
    worker.onmessage = ({ data }) => {
      if (data.error) job.reject(new Error(data.error));
      else {
        const geometry = new BufferGeometry();
        geometry.setAttribute("position", new BufferAttribute(data.position, 3));
        geometry.setAttribute("normal", new BufferAttribute(data.normal, 3));
        geometry.boundsTree = MeshBVH.deserialize(data.bvh, geometry);
        geometry.boundingSphere = new Sphere(new Vector3().fromArray(data.sphere.center), data.sphere.radius);
        job.resolve(geometry);
      }
      finish();
    };
    worker.postMessage(job.url);
  }
}

/** Full native triangles, with worker parsing and accelerated picking; display only. */
export class DisplayMeshLoader extends Loader<BufferGeometry> {
  override load(url: string, onLoad: (geometry: BufferGeometry) => void, _progress?: (event: ProgressEvent) => void,
    onError?: (error: unknown) => void) {
    queue.push({ url: new URL(url, window.location.href).href, resolve: onLoad, reject: (error) => onError?.(error) });
    drain();
  }
}
