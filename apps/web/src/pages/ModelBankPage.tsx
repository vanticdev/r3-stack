import { Suspense, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { Bounds, OrbitControls, useGLTF } from "@react-three/drei";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiUrl } from "../api/client.ts";
import { Card, ErrorBox } from "../components/ui.tsx";

export type BankModel = {
  id: string; name: string; mpn: string; manufacturer: string; category: string; available: boolean;
  proxy_reason: string; validation: { status: string }; sources: { url: string; license: string; requires_login: boolean; direct: boolean }[];
};

async function modelsRequest<T>(path: string, body?: unknown): Promise<T> {
  const result = await fetch(apiUrl(`/api/models${path}`), {
    method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await result.json() as T & { message?: string; detail?: { message?: string } };
  if (!result.ok) throw new Error(data.message || data.detail?.message || `Model bank request failed (${result.status})`);
  return data;
}


function Geometry({ url }: { url: string }) {
  const { scene } = useGLTF(url);
  return <primitive object={scene.clone()} />;
}

export function ModelBankPage() {
  const [query, setQuery] = useState(new URLSearchParams(window.location.hash.split("?")[1]).get("q") ?? "");
  const [category, setCategory] = useState("");
  const [selected, setSelected] = useState<string | null>(new URLSearchParams(window.location.hash.split("?")[1]).get("id"));
  const [preview, setPreview] = useState<{ id: string; url: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();
  const results = useQuery({ queryKey: ["model-bank", query, category], queryFn: () => modelsRequest<{ models: BankModel[] }>(`?q=${encodeURIComponent(query)}${category ? `&category=${category}` : ""}&limit=100`) });
  const detail = useQuery({ queryKey: ["model-bank-detail", selected], queryFn: () => modelsRequest<{ model: BankModel }>(`/${selected}`), enabled: Boolean(selected) });
  const model = detail.data?.model ?? results.data?.models.find((m) => m.id === selected);
  const fetchModel = async (id: string) => {
    setBusy(true); setError(null);
    try {
      const fetched = await modelsRequest<{ glb_url: string }>(`/${id}/fetch`, {});
      setPreview({ id, url: apiUrl(fetched.glb_url) });
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const add = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(null);
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    try {
      const result = await modelsRequest<{ model: BankModel }>("", values);
      setSelected(result.model.id); form.reset();
      await client.invalidateQueries({ queryKey: ["model-bank"] });
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return <div className="content"><div className="page model-bank">
    <div className="page-head"><div><h1>Model bank</h1><details><summary>About the model bank</summary><p>Find real purchased-part CAD. Search by part number, manufacturer or category; keep sources and checks with each model.</p></details></div></div>
    <div className="row"><input aria-label="Search models" placeholder="Search part number or name" value={query} onChange={(e) => setQuery(e.target.value)} />
      <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{["computer", "camera", "motor", "gearbox", "esc", "servo", "battery", "gnss", "imu", "receiver", "connector", "wheel", "fastener", "regulator", "sensor", "cable", "bearing", "other"].map((c) => <option key={c}>{c}</option>)}</select></div>
    {(error || results.error || detail.error) && <ErrorBox error={error || results.error || detail.error} />}
    <div className="model-bank-columns">
      <Card title="Models"><div className="stack">{results.isLoading && <p>Searching…</p>}{results.data?.models.length === 0 && <p>No matching model. Add a public source below after checking the manufacturer and public libraries.</p>}{results.data?.models.slice(0, 2).map((m) => <button key={m.id} className="ghost model-bank-row" onClick={() => setSelected(m.id)}><strong>{m.name}</strong><div className="small muted">{m.manufacturer} {m.mpn} · {m.available ? m.validation.status : "proxy / source needed"}</div></button>)}{(results.data?.models.length ?? 0) > 2 && <details><summary>All models ({results.data?.models.length})</summary>{results.data?.models.slice(2).map(m => <button key={m.id} className="ghost model-bank-row" onClick={() => setSelected(m.id)}><strong>{m.name}</strong><span>{m.manufacturer} {m.mpn} · {m.available ? m.validation.status : "proxy / source needed"}</span></button>)}</details>}</div></Card>
      <div className="stack">{model && <Card title={model.name}><p>{model.mpn} · {model.category} · Geometry: {model.validation.status}</p><p>{model.proxy_reason}</p>{model.sources.map((s) => <p key={s.url}><a href={s.url} target="_blank" rel="noreferrer">Source{s.requires_login ? " (human download required)" : ""}</a><br /><span className="small muted">{s.license}</span></p>)}
        <button disabled={busy || !model.sources.some((s) => s.direct && !s.requires_login)} onClick={() => void fetchModel(model.id)}>{busy ? "Fetching…" : "Fetch and preview"}</button>
        {preview?.id === model.id && <div style={{ height: 320 }}><Canvas camera={{ position: [0.15, 0.15, 0.15] }}><ambientLight intensity={1.8} /><directionalLight position={[1, 2, 3]} intensity={2} /><Suspense fallback={null}><Bounds fit clip observe margin={1.4}><Geometry url={preview.url} /></Bounds></Suspense><OrbitControls makeDefault /></Canvas></div>}
      </Card>}
      <Card collapsed title="Add a model"><form onSubmit={(e) => void add(e)} className="stack"><label className="field"><span>Name (required)</span><input name="name" aria-label="Name" placeholder="Name" required /></label><label className="field"><span>Manufacturer (optional)</span><input name="manufacturer" aria-label="Manufacturer" placeholder="Manufacturer" /></label><label className="field"><span>Part number (optional)</span><input name="mpn" aria-label="Part number" placeholder="Part number" /></label><label className="field"><span>Model category (required)</span><input name="category" aria-label="Model category" placeholder="Category (e.g. motor)" required /></label><label className="field"><span>STEP URL (optional)</span><input name="url" aria-label="STEP URL" placeholder="Direct STEP URL (or use Onshape below)" /></label><label className="field"><span>Onshape public version URL (optional)</span><input name="onshape" aria-label="Onshape public version URL" placeholder="Public Onshape version URL (/v/)" /></label><label className="field"><span>License (required)</span><input name="license" aria-label="License" placeholder="License / terms" required /></label><label className="field"><span>Format</span><select name="format" aria-label="Format"><option value="step">STEP</option><option value="stp.zip">STEP ZIP</option><option value="glb">GLB</option></select></label><label className="field"><span>ZIP member (optional)</span><input name="member" aria-label="ZIP member" placeholder="STEP path within ZIP (if applicable)" /></label><button disabled={busy} type="submit">Download, check and add</button></form></Card></div>
    </div>
  </div></div>;
}
