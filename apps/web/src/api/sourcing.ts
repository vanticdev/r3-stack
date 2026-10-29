// Sourcing API (`r3 source` over HTTP): slots, stored searches, SSE search jobs, choose/unpin.
// Self-contained so the Parts page merges without touching the shared client/types modules.
import { useQuery, type QueryClient } from "@tanstack/react-query";

import { ApiError, apiUrl } from "./client.ts";

export type ValueStatus = "datasheet" | "vendor" | "estimated";
export type Tier = "eligible" | "unverified" | "rejected";

export interface SourcedValue { value: unknown; status: ValueStatus; source: string; note: string }
export interface Offer {
  vendor: string; url: string; sku: string; price: number | null; currency: string; price_breaks: [number, number][];
  stock: number | null; lead_time_days: number | null; status: ValueStatus; source: string;
}
export interface ModelFile { url: string; format: string; description: string; source: string }
export interface Candidate {
  id: string; provider: string; kind: string; name: string; manufacturer: string; mpn: string; description: string;
  params: Record<string, SourcedValue>; offers: Offer[]; urls: Record<string, string>; models: ModelFile[];
  retrieved_at: number; notes: string[];
}
export interface ParamMatch {
  param: string; required: string; status: "pass" | "fail" | "unknown"; detail: string; value: unknown; provenance: string; source: string;
}
export interface TwinCheck { check: string; code: string; severity: "error" | "warning" | "info"; message: string; subjects: string[]; data: Record<string, unknown> }
export interface RankedCandidate {
  rank: number; tier: Tier; score: number; breakdown: Record<string, number>; reasons: string[];
  unit_price: number | null; currency: string; stock: number | null;
  twin: { checked: boolean; errors: number; warnings: number; checks: TwinCheck[] };
  matches: ParamMatch[]; candidate: Candidate;
}
export interface ProviderReport { provider: string; status: "ok" | "skipped" | "failed"; candidates: number; message: string; code: string; duration_s: number }
export interface Pin {
  catalog_id: string; catalog_file: string; digest: string; provider: string; candidate_id: string; name: string; manufacturer: string;
  mpn: string; vendor: string; url: string; price: { amount: number; currency: string; quantity: number } | null; retrieved_at: number;
  requirement_sha256: string; components: string[]; cad: "vendor_step" | "proxy";
}
export interface Slot {
  slot: string; components: string[]; kind: string; requires: Record<string, unknown>; prefer: Record<string, unknown>; quantity: number;
  budget: { amount: number; currency: string } | null; providers: string[]; authored_type: string; resolved_type: string;
  pinned: Pin | null; pin_stale: boolean; requirement_sha256: string;
  last_search: { searched_at: number; candidates: number; eligible: number; requirement_sha256: string; reports: ProviderReport[] } | null;
}
export interface ProviderInfo { id: string; title: string; category: string; kinds: string[]; env: string[]; available: boolean; reason: string }
export interface SlotsResponse { ok: boolean; project: string; slots: Slot[]; providers: ProviderInfo[] }
export interface SearchResult {
  slot: string; components: string[]; kind: string; requires: Record<string, unknown>; quantity: number; requirement_sha256: string;
  searched_at: number; template: string | null; reports: ProviderReport[]; counts: Record<Tier, number>; ranked: RankedCandidate[];
  current: { type: string; satisfied: boolean; matches: ParamMatch[] } | null; stale: boolean; pinned: Pin | null;
}
export interface SearchJobCreated { job_id: string; slot: string; status: string; events_url: string; status_url: string }
export interface ChooseResult { ok: boolean; slot: string; pinned: Pin; catalog_file: string; tier: Tier; diagnostics: { code: string; severity: string; message: string }[] }
export interface SourcingEvent {
  type: string; provider?: string; status?: string; candidates?: number; message?: string; name?: string; rank?: number; tier?: Tier;
  score?: number; counts?: Record<Tier, number>; error?: string;
}

async function call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method, signal,
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, { code: "network_error", message: `cannot reach the R3 API (${(error as Error).message})` });
  }
  const payload = await response.json().catch(() => ({ code: `http_${response.status}`, message: response.statusText }));
  if (!response.ok) throw new ApiError(response.status, { code: payload.code ?? `http_${response.status}`, message: payload.message ?? "", data: payload.data ?? {} });
  return payload as T;
}

const base = (pid: string) => `/api/projects/${encodeURIComponent(pid)}/sourcing`;

export const sourcingApi = {
  slots: (pid: string, signal?: AbortSignal) => call<SlotsResponse>("GET", base(pid), undefined, signal),
  search: (pid: string, slot: string, signal?: AbortSignal) => call<SearchResult>("GET", `${base(pid)}/${encodeURIComponent(slot)}`, undefined, signal),
  startSearch: (pid: string, slot: string, providers?: string[]) =>
    call<SearchJobCreated>("POST", `${base(pid)}/${encodeURIComponent(slot)}/search`, { providers: providers?.length ? providers : null }),
  choose: (pid: string, slot: string, candidate: string | number) =>
    call<ChooseResult>("POST", `${base(pid)}/${encodeURIComponent(slot)}/choose`, { candidate }),
  unpin: (pid: string, slot: string) => call<{ ok: boolean }>("POST", `${base(pid)}/${encodeURIComponent(slot)}/unpin`),
};

export const sourcingKeys = {
  slots: (pid: string) => ["project", pid, "sourcing"] as const,
  search: (pid: string, slot: string) => ["project", pid, "sourcing", slot] as const,
};

export const useSlots = (pid: string) => useQuery({ queryKey: sourcingKeys.slots(pid), queryFn: ({ signal }) => sourcingApi.slots(pid, signal) });

export const useStoredSearch = (pid: string, slot: string | null) =>
  useQuery({
    queryKey: sourcingKeys.search(pid, slot ?? ""), enabled: !!slot, retry: false,
    queryFn: async ({ signal }) => {
      try {
        return await sourcingApi.search(pid, slot!, signal);
      } catch (error) {
        if (error instanceof ApiError && error.code === "sourcing_not_searched") return null;
        throw error;
      }
    },
  });

/** After choose/unpin the pinned part changes every stage's input: refetch the whole project. */
export const invalidateSourcing = (client: QueryClient, pid: string) => client.invalidateQueries({ queryKey: ["project", pid] });
