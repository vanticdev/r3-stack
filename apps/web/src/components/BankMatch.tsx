import { useQuery } from "@tanstack/react-query";
import { apiUrl } from "../api/client.ts";

type Match = { id: string; name: string; available: boolean; validation: { status: string } };
async function find(query: string): Promise<{ models: Match[] }> {
  const response = await fetch(apiUrl(`/api/models?q=${encodeURIComponent(query)}&limit=3`));
  if (!response.ok) throw new Error("Model bank lookup failed");
  return response.json() as Promise<{ models: Match[] }>;
}
export function BankMatch({ mpn }: { mpn: string }) {
  const result = useQuery({ queryKey: ["bank-match", mpn], queryFn: () => find(mpn), enabled: Boolean(mpn) });
  return <span className="small">{result.data?.models.map((m) => <a key={m.id} href={`#/models?id=${encodeURIComponent(m.id)}`}>Model bank: {m.name} ({m.available ? m.validation.status : "proxy"})</a>)}
    {(!mpn || result.data?.models.length === 0) && <a href={`#/models?q=${encodeURIComponent(mpn)}`}>No bank model matched · search or add</a>}
    {result.error && <span className="muted">Model bank unavailable</span>}
  </span>;
}
