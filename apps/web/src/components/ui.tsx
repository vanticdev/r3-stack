import type { UseQueryResult } from "@tanstack/react-query";
import { useEffect, useRef, type ReactNode } from "react";

import { ApiError } from "../api/client.ts";
import type { EvidenceStatus, Severity } from "../api/types.ts";
import { ratio } from "../lib/domain.ts";
import { fmt, pct } from "../lib/units.ts";
import { Icon, type IconName } from "./icons.tsx";

export function Card({ title, subtitle, actions, children, flush, className = "" }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          <div className="stack" style={{ gap: 1 }}>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          <div className="spacer" />
          {actions}
        </div>
      )}
      <div className={`card-body${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}

export type Tone = "good" | "warning" | "critical" | "info" | "muted" | "stale" | "running";

const TONE_ICON: Record<Tone, IconName> = {
  good: "check", warning: "alert", critical: "x", info: "info", muted: "dot", stale: "clock", running: "spinner",
};

/** Status chip: icon + label (never colour alone). */
export function Chip({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`chip ${tone}`} title={title}>
      <Icon name={TONE_ICON[tone]} size={12} />
      {children}
    </span>
  );
}

export const severityTone = (s: Severity | null | undefined): Tone =>
  s === "error" ? "critical" : s === "warning" ? "warning" : s === "info" ? "info" : "muted";

export function SeverityChip({ severity }: { severity: Severity }) {
  return <Chip tone={severityTone(severity)}>{severity}</Chip>;
}

const EVIDENCE_TONE: Record<EvidenceStatus, Tone> = {
  verified: "good", estimated: "warning", failed: "critical", not_run: "muted", stale: "stale",
};
export function EvidenceChip({ status }: { status: EvidenceStatus }) {
  return <Chip tone={EVIDENCE_TONE[status]}>{status.replace("_", " ")}</Chip>;
}

export function StageChip({ status, stage }: { status: string; stage: string }) {
  const tone: Tone = status === "ok" ? "good" : status === "failed" ? "critical" : status === "stale" ? "stale"
    : status === "running" ? "running" : status === "unavailable" ? "warning" : "muted";
  return <Chip tone={tone} title={`${stage}: ${status}`}>{stage}</Chip>;
}

export function Empty({ icon = "inbox", title, children }: { icon?: IconName; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} size={22} />
      <strong className="secondary">{title}</strong>
      {children && <div className="small">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div className="error-box" role="alert">
      <strong>{api ? api.message : String((error as Error)?.message ?? error)}</strong>
      {api && <div className="small"><code>{api.code}</code>{api.status ? ` · HTTP ${api.status}` : ""}</div>}
    </div>
  );
}

/** Missing-artifact errors render as an empty state with the stage to run, others as errors. */
export function QueryView<T>({ query, children, empty, height = 160 }: {
  query: UseQueryResult<T>; children: (data: T) => ReactNode; empty?: ReactNode; height?: number;
}) {
  if (query.isPending) return <div className="skeleton" style={{ height }} />;
  if (query.isError) {
    const error = query.error;
    if (error instanceof ApiError && (error.code === "artifact_missing" || error.code === "scenario_not_found")) {
      return <Empty icon="inbox" title={error.message}>{empty}</Empty>;
    }
    return <ErrorBox error={error} />;
  }
  return <>{children(query.data)}</>;
}

/** Value vs rating: the rating is the vertical tick at 100 %; bursts are drawn dashed. */
export function RatioBar({ value, limit, burst, burstLimit, scaleMax = 1.5 }: {
  value: number; limit: number; burst?: number; burstLimit?: number; scaleMax?: number;
}) {
  const r = ratio(value, limit);
  const width = (x: number) => `${Math.min(100, (x / scaleMax) * 100)}%`;
  const cls = r > 1 ? "over" : r > 0.8 ? "near" : "";
  const burstRatio = burst !== undefined && (burstLimit ?? limit) > 0 ? burst / (burstLimit ?? limit) : null;
  return (
    <div className="ratio" role="img" aria-label={`${pct(r)} of rating`}
         title={`${fmt(value, "A")} of ${fmt(limit, "A")} (${pct(r)})${burst !== undefined ? ` · burst ${fmt(burst, "A")} of ${fmt(burstLimit ?? limit, "A")}` : ""}`}>
      {burstRatio !== null && <i className="burst" style={{ width: width(burstRatio) }} />}
      <i className={cls} style={{ width: width(r) }} />
      <b style={{ left: width(1) }} />
    </div>
  );
}

const KPI_TONE_LABEL: Partial<Record<Tone, string>> = { good: "within limit", critical: "violates limit", warning: "check", muted: "not run", stale: "stale" };

export function Kpi({ label, value, sub, tone, toneLabel }: { label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: Tone; toneLabel?: string }) {
  return (
    <div className="card kpi">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {(sub || tone) && <span className="sub row">{tone && <Chip tone={tone}>{toneLabel ?? KPI_TONE_LABEL[tone] ?? tone}</Chip>}{sub}</span>}
    </div>
  );
}

export function ConfirmDialog({ title, children, confirmLabel, danger, onConfirm, onCancel, disabled }: {
  title: ReactNode; children: ReactNode; confirmLabel: string; danger?: boolean; disabled?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <header><h2 id="dialog-title">{title}</h2></header>
        <div className="body">{children}</div>
        <footer>
          <button ref={ref} onClick={onCancel}>Cancel</button>
          <button className={danger ? "danger solid" : "primary"} onClick={onConfirm} disabled={disabled}>{confirmLabel}</button>
        </footer>
      </div>
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label?: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={o.value === value} className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Gallery({ images }: { images: { name: string; url: string }[] }) {
  if (!images.length) return <Empty title="No images">The stage writes PNG plots under build/.</Empty>;
  return (
    <div className="gallery">
      {images.map((img) => (
        <figure key={img.url}>
          <a href={img.url} target="_blank" rel="noreferrer"><img src={img.url} alt={img.name} loading="lazy" /></a>
          <figcaption>{img.name}</figcaption>
        </figure>
      ))}
    </div>
  );
}
