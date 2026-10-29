import { BankMatch } from "../components/BankMatch.tsx";
// Parts: components declared by requirements (`source:` in robot.yaml), the real parts found online,
// their parameters with provenance links, price/stock, the digital-twin check results, and a choose button.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { apiUrl } from "../api/client.ts";
import { streamEvents, type StreamHandle } from "../api/sse.ts";
import {
  invalidateSourcing, sourcingApi, useSlots, useStoredSearch, type Candidate, type ParamMatch, type RankedCandidate, type Slot,
  type SourcingEvent, type Tier, type ValueStatus,
} from "../api/sourcing.ts";
import { Markdown } from "../components/Markdown.tsx";
import { Icon } from "../components/icons.tsx";
import { Card, Chip, ConfirmDialog, Empty, ErrorBox, QueryView, Segmented, type Tone } from "../components/ui.tsx";
import { timeAgo } from "../lib/units.ts";

const TIER_TONE: Record<Tier, Tone> = { eligible: "good", unverified: "warning", rejected: "critical" };
const STATUS_TONE: Record<ValueStatus, Tone> = { datasheet: "good", vendor: "info", estimated: "warning" };
const MATCH_TONE: Record<ParamMatch["status"], Tone> = { pass: "good", unknown: "warning", fail: "critical" };

// SI value -> display unit, by parameter name.
const UNITS: [RegExp, string, number][] = [
  [/^(diameter|width|length|height|mount_hole_diameter|mount_holes)$/, "mm", 1e3], [/^mass$/, "g", 1e3], [/^(supply)$/, "V", 1],
  [/^supply_current$/, "A", 1], [/^band$/, "GHz", 1e-9], [/^ram$/, "GB", 1e-9], [/^telemetry_power$/, "dBm", 1],
  [/^operating_temperature$/, "°C", 1],
];

function num(value: number, scale: number): string {
  return Number((value * scale).toPrecision(4)).toString();
}

export function formatValue(name: string, value: unknown): string {
  const [, unit, scale] = UNITS.find(([re]) => re.test(name)) ?? [null, "", 1];
  if (typeof value === "number") return `${num(value, scale)}${unit ? ` ${unit}` : ""}`;
  if (Array.isArray(value)) {
    const [low, high] = value;
    if (value.length === 2 && typeof low === "number" && typeof high === "number" && !/mount_holes/.test(name)) {
      return `${num(low, scale)}–${num(high, scale)}${unit ? ` ${unit}` : ""}`;
    }
    return value.map((v) => (typeof v === "number" ? num(v, scale) : String(v))).join(name === "mount_holes" ? " × " : ", ") + (unit && value.every((v) => typeof v === "number") ? ` ${unit}` : "");
  }
  if (value && typeof value === "object" && "size" in value) {
    const tagged = value as { size: number; shape: string };
    return `${num(tagged.size, 1e3)} mm ${tagged.shape}`;
  }
  return value === null || value === undefined ? "—" : String(value);
}

const requirementText = (requires: Record<string, unknown>) =>
  Object.entries(requires).filter(([k]) => k !== "kind").map(([k, v]) => `${k} ${Array.isArray(v) ? `${v[0]} – ${v[1]}` : String(v)}`).join(" · ");

const price = (r: { unit_price: number | null; currency: string }) => (r.unit_price === null ? "—" : `${r.unit_price.toFixed(2)} ${r.currency}`);

function Link({ href, children }: { href: string; children: React.ReactNode }) {
  if (!/^https?:\/\//.test(href)) return <span className="muted">{children}</span>;
  return <a href={href} target="_blank" rel="noreferrer noopener">{children} <Icon name="external" size={11} /></a>;
}

export function PartsPage() {
  const { pid = "" } = useParams();
  const slots = useSlots(pid);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Parts</h1>
          <p>Components declared by requirements. R3 finds real parts online, ranks them with the digital twin and pins your choice in <code>r3.lock.yaml</code>.</p>
        </div>
      </div>
      <QueryView query={slots} height={300}>
        {(data) => {
          if (!data.slots.length) {
            return (
              <Card title="No sourcing slots">
                <Empty title="Declare a part by its specification">
                  Add <code>source: {"{requires: {kind: radio, protocol: crsf, band: 2.4 GHz}}"}</code> to a component in robot.yaml,
                  or run <code>r3 source</code> from the CLI.
                </Empty>
              </Card>
            );
          }
          const slot = data.slots.find((s) => s.slot === selected) ?? data.slots[0]!;
          return (
            <div className="grid side" style={{ gridTemplateColumns: "300px minmax(0, 1fr)" }}>
              <div className="stack">
                <Card title="Slots" flush>
                  <div className="stack" style={{ gap: 0 }}>
                    {data.slots.map((s) => <SlotRow key={s.slot} slot={s} active={s.slot === slot.slot} onSelect={() => setSelected(s.slot)} />)}
                  </div>
                </Card>
                <Card title="Providers" subtitle="Keys come from the server environment">
                  <div className="stack" style={{ gap: 4 }}>
                    {data.providers.map((p) => (
                      <div key={p.id} className="stack small" style={{ gap: 2 }} title={p.reason || p.title}>
                        <Chip tone={p.available ? "good" : "muted"}>{p.id}</Chip>
                        <span className="muted">{p.available ? p.title : p.env.length ? `needs ${p.env.join(", ")}` : p.reason}</span>
                      </div>
                    ))}
                  </div>
                </Card>
              </div>
              <SlotDetail key={slot.slot} pid={pid} slot={slot} />
            </div>
          );
        }}
      </QueryView>
    </div>
  );
}

function SlotRow({ slot, active, onSelect }: { slot: Slot; active: boolean; onSelect: () => void }) {
  return (
    <button className={`ghost${active ? " active" : ""}`} onClick={onSelect} aria-pressed={active}
            style={{ display: "grid", justifyItems: "start", gap: 3, padding: "10px 14px", borderRadius: 0, textAlign: "left",
                     background: active ? "var(--surface-2)" : undefined }}>
      <span className="row"><strong>{slot.slot}</strong><span className="muted small">{slot.kind} × {slot.quantity}</span></span>
      <span className="small muted">{requirementText(slot.requires)}</span>
      {slot.pinned ? <Chip tone={slot.pin_stale ? "stale" : "good"}>{slot.pin_stale ? "requirements changed" : slot.pinned.name}</Chip>
        : <Chip tone="muted">{slot.resolved_type ? `using ${slot.resolved_type}` : "not sourced"}</Chip>}
    </button>
  );
}

function SlotDetail({ pid, slot }: { pid: string; slot: Slot }) {
  const client = useQueryClient();
  const stored = useStoredSearch(pid, slot.slot);
  const [events, setEvents] = useState<SourcingEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState<"ranked" | "all">("ranked");
  const [open, setOpen] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<RankedCandidate | null>(null);
  const stream = useRef<StreamHandle | null>(null);
  useEffect(() => () => stream.current?.close(), []);

  const search = async () => {
    setError(null);
    setEvents([]);
    setRunning(true);
    try {
      const job = await sourcingApi.startSearch(pid, slot.slot);
      stream.current = streamEvents<SourcingEvent>(apiUrl(job.events_url), (event) => {
        if (event.type !== "candidate") setEvents((list) => [...list, event]);
        if (event.type === "job_finished") {
          setRunning(false);
          if (event.status === "failed") setError(new Error(event.error ?? "search failed"));
          void invalidateSourcing(client, pid);
        }
      }, (e) => { setRunning(false); setError(e); });
    } catch (e) {
      setRunning(false);
      setError(e);
    }
  };

  const choose = async (entry: RankedCandidate) => {
    setConfirm(null);
    setError(null);
    try {
      await sourcingApi.choose(pid, slot.slot, entry.candidate.id);
      await invalidateSourcing(client, pid);
    } catch (e) {
      setError(e);
    }
  };

  const unpin = async () => {
    try {
      await sourcingApi.unpin(pid, slot.slot);
      await invalidateSourcing(client, pid);
    } catch (e) {
      setError(e);
    }
  };

  return (
    <div className="stack">
      <Card title={<>{slot.slot} <span className="muted small">{slot.components.join(", ")}</span></>}
            subtitle={`${slot.kind}: ${requirementText(slot.requires)}${slot.budget ? ` · budget ${slot.budget.amount} ${slot.budget.currency}` : ""}`}
            actions={<div className="row">
              {slot.pinned && <button className="small" onClick={unpin}>Unpin</button>}
              <button className="primary small" onClick={search} disabled={running}>
                <Icon name={running ? "spinner" : "refresh"} size={13} />{running ? "Searching…" : stored.data ? "Search again" : "Search online"}
              </button>
            </div>}>
        <div className="stack">
          {slot.pinned ? (
            <div className="row small">
              <Chip tone={slot.pin_stale ? "stale" : "good"}>pinned</Chip>
              <strong>{slot.pinned.name}</strong>
              <span className="muted">{slot.pinned.manufacturer} · {slot.pinned.mpn}</span>
              {slot.pinned.price && <span className="num">{slot.pinned.price.amount} {slot.pinned.price.currency} × {slot.pinned.price.quantity}</span>}
              <Chip tone={slot.pinned.cad === "vendor_step" ? "info" : "muted"}>{slot.pinned.cad === "vendor_step" ? "vendor STEP" : "CAD proxy"}</Chip>
              <code className="muted">{slot.pinned.catalog_file}</code>
              <Link href={slot.pinned.url}>source</Link>
            </div>
          ) : <div className="small muted">Not pinned: the pipeline uses {slot.resolved_type ? <code>{slot.resolved_type}</code> : "nothing (component_unsourced)"}.</div>}
          {(running || events.length > 0) && (
            <div className="run-log" aria-label="Search log">
              {events.map((e, i) => (
                <div key={i} className={e.type === "provider_failed" || e.status === "failed" ? "err" : ""}>
                  {e.type === "provider_skipped" ? `skipped ${e.provider}: ${e.message}` : e.type === "provider_finished" ? `${e.provider}: ${e.candidates} candidates`
                    : e.type === "provider_failed" ? `${e.provider} failed: ${e.message}` : e.type === "provider_started" ? `searching ${e.provider}…`
                    : e.type === "ranking_started" ? `ranking ${e.candidates} candidates with twin checks…`
                    : e.type === "search_finished" ? `done: ${e.counts?.eligible ?? 0} eligible, ${e.counts?.unverified ?? 0} unverified, ${e.counts?.rejected ?? 0} rejected`
                    : e.type.replace(/_/g, " ")}
                </div>
              ))}
            </div>
          )}
          {error !== null && <ErrorBox error={error} />}
        </div>
      </Card>
      <QueryView query={stored} height={240}>
        {(result) => !result ? (
          <Card><Empty icon="target" title="Not searched yet">Search online to find parts that meet the requirements.</Empty></Card>
        ) : (
          <Card title="Candidates" subtitle={`${result.counts.eligible} eligible · ${result.counts.unverified} unverified · ${result.counts.rejected} rejected · searched ${timeAgo(result.searched_at)}${result.template ? ` · template ${result.template}` : ""}`}
                actions={<Segmented value={filter} onChange={setFilter} label="Show" options={[{ value: "ranked", label: "Ranked" }, { value: "all", label: "All" }]} />} flush>
            {result.stale && <div className="banner" role="status"><Icon name="alert" size={14} />The requirements changed since this search; search again before choosing.</div>}
            <div className="row small" style={{ padding: "8px 12px", gap: 6 }}>
              {result.reports.map((r) => <Chip key={r.provider} tone={r.status === "ok" ? "good" : r.status === "skipped" ? "muted" : "critical"} title={r.message}>
                {r.provider}{r.status === "ok" ? ` ${r.candidates}` : ` ${r.status}`}</Chip>)}
            </div>
            <div className="table-wrap" style={{ maxHeight: 640 }}>
              <table>
                <thead><tr><th>#</th><th>Part</th><th>Tier</th><th className="num">Score</th><th className="num">Unit price</th><th className="num">Stock</th><th>Twin checks</th><th /></tr></thead>
                <tbody>
                  {result.ranked.filter((r) => filter === "all" || r.tier !== "rejected").slice(0, filter === "all" ? 400 : 60).map((entry) => (
                    <CandidateRows key={entry.candidate.id} entry={entry} open={open === entry.candidate.id} pinned={slot.pinned?.candidate_id === entry.candidate.id}
                                   stale={result.stale} onToggle={() => setOpen(open === entry.candidate.id ? null : entry.candidate.id)} onChoose={() => setConfirm(entry)} />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </QueryView>
      {confirm && (
        <ConfirmDialog title={`Pin ${confirm.candidate.name}?`} confirmLabel="Choose part" onCancel={() => setConfirm(null)} onConfirm={() => void choose(confirm)}>
          <p>Writes <code>catalog/components/sourced_…yaml</code> with provenance per field and pins it under <code>sourcing.{slot.slot}</code> in r3.lock.yaml.
            Every stage then uses it; run the pipeline afterwards.</p>
          {confirm.tier !== "eligible" && <p><Chip tone={TIER_TONE[confirm.tier]}>{confirm.tier}</Chip> {confirm.reasons.join("; ")}</p>}
          {confirm.twin.warnings + confirm.twin.errors > 0 && <p>{confirm.twin.errors} twin errors, {confirm.twin.warnings} warnings (see the candidate's checks).</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}

function TwinSummary({ entry }: { entry: RankedCandidate }) {
  if (entry.tier === "rejected") return <span className="muted small">not run</span>;
  if (!entry.twin.checked) return <span className="muted small">not checked</span>;
  if (!entry.twin.errors && !entry.twin.warnings) return <Chip tone="good">pass</Chip>;
  return <span className="row">{entry.twin.errors > 0 && <Chip tone="critical">{entry.twin.errors} E</Chip>}{entry.twin.warnings > 0 && <Chip tone="warning">{entry.twin.warnings} W</Chip>}</span>;
}

function CandidateRows({ entry, open, pinned, stale, onToggle, onChoose }: {
  entry: RankedCandidate; open: boolean; pinned: boolean; stale: boolean; onToggle: () => void; onChoose: () => void;
}) {
  const c = entry.candidate;
  return (
    <>
      <tr onClick={onToggle} style={{ cursor: "pointer" }} aria-expanded={open}>
        <td className="num">{entry.rank}</td>
        <td>
          <div className="stack" style={{ gap: 1 }}>
            <strong>{c.name}{pinned && <> <Chip tone="good">pinned</Chip></>}</strong>
            <span className="small muted">{c.manufacturer}{c.mpn ? ` · ${c.mpn}` : ""} · {c.provider}</span>
          </div>
        </td>
        <td><Chip tone={TIER_TONE[entry.tier]}>{entry.tier}</Chip></td>
        <td className="num" title={Object.entries(entry.breakdown).map(([k, v]) => `${k} ${v}`).join("\n")}>{entry.tier === "rejected" ? "—" : entry.score.toFixed(1)}</td>
        <td className="num">{price(entry)}</td>
        <td className="num">{entry.stock === null ? "?" : entry.stock}</td>
        <td><TwinSummary entry={entry} /></td>
        <td className="right">
          <button className="small" disabled={pinned || stale || entry.tier === "rejected"} onClick={(e) => { e.stopPropagation(); onChoose(); }}>Choose</button>
        </td>
      </tr>
      {open && <tr><td colSpan={8} style={{ background: "var(--surface-1)" }}><CandidateDetail entry={entry} candidate={c} /></td></tr>}
    </>
  );
}

function CandidateDetail({ entry, candidate }: { entry: RankedCandidate; candidate: Candidate }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.15fr) minmax(0, 1fr)", gap: 16, padding: "6px 2px" }}>
      <div className="stack" style={{ minWidth: 0, alignContent: "start" }}>
        <h3 className="small">Requirements</h3>
        <div className="table-wrap"><table>
          <tbody>
            {entry.matches.map((m) => (
              <tr key={m.param} title={m.detail}>
                <td>{m.param}</td><td className="muted">{m.required}</td>
                <td className="num">{m.value === null || m.value === undefined ? "—" : formatValue(m.param, m.value)}</td>
                <td><Chip tone={MATCH_TONE[m.status]}>{m.status}</Chip></td>
                <td>{m.provenance && <Chip tone={STATUS_TONE[m.provenance as ValueStatus] ?? "muted"}>{m.provenance}</Chip>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
        {entry.reasons.length > 0 && <div className="small muted">{entry.reasons.join("; ")}</div>}
        <h3 className="small">Parameters</h3>
        <div className="table-wrap"><table>
          <tbody>
            {Object.entries(candidate.params).map(([name, p]) => (
              <tr key={name} title={p.note}>
                <td>{name}</td><td className="num">{formatValue(name, p.value)}</td>
                <td><Chip tone={STATUS_TONE[p.status]}>{p.status}</Chip></td>
                <td className="small ellipsis" style={{ maxWidth: 170 }}>{p.source ? <Link href={p.source}>{new URL(p.source, "https://x").hostname.replace(/^www\./, "")}</Link> : <span className="muted">no citation</span>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      <div className="stack" style={{ minWidth: 0, alignContent: "start" }}>
        <h3 className="small">Twin checks</h3>
        {entry.twin.checks.length === 0 ? <span className="small muted">{entry.twin.checked ? "no new findings with this part in the robot" : "not run for this candidate"}</span> : (
          <div className="stack" style={{ gap: 4 }}>
            {entry.twin.checks.map((check, i) => (
              <div key={i} className="diag">
                <div className="row"><Chip tone={check.severity === "error" ? "critical" : check.severity === "warning" ? "warning" : "info"}>{check.check}</Chip><code className="small">{check.code}</code></div>
                <Markdown className="small">{check.message}</Markdown>
              </div>
            ))}
          </div>
        )}
        <h3 className="small">Offers</h3>
        {candidate.offers.length === 0 ? <span className="small muted">no price or stock from this source</span> : (
          <div className="table-wrap"><table>
            <tbody>
              {candidate.offers.map((o, i) => (
                <tr key={i}><td><Link href={o.url || o.source}>{o.vendor}</Link></td><td className="num">{o.price === null ? "—" : `${o.price} ${o.currency}`}</td>
                  <td className="num">{o.stock === null ? "stock ?" : `${o.stock} in stock`}</td><td className="num muted">{o.lead_time_days ? `${Math.round(o.lead_time_days)} d lead` : ""}</td></tr>
              ))}
            </tbody>
          </table></div>
        )}
        <h3 className="small">Links and models</h3>
        <BankMatch mpn={candidate.mpn} />
        <div className="row small">
          {Object.entries(candidate.urls).map(([k, u]) => <Link key={k} href={u}>{k.replace(/_/g, " ")}</Link>)}
          {candidate.models.map((m) => <Link key={m.url} href={m.url}>{m.format === "pdf_drawing" ? "drawing" : m.format.replace("_", " ")}</Link>)}
          {!candidate.models.some((m) => m.format.startsWith("step")) && <Chip tone="muted">no vendor model: CAD proxy</Chip>}
        </div>
        {candidate.notes.length > 0 && <Markdown className="small muted">{candidate.notes.join("\n\n")}</Markdown>}
      </div>
    </div>
  );
}
