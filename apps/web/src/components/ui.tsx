import type { UseQueryResult } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../api/client.ts";
import type { EvidenceStatus, Severity } from "../api/types.ts";
import { ratio } from "../lib/domain.ts";
import { fmt, pct, timeAgo } from "../lib/units.ts";
import { Markdown } from "./Markdown.tsx";
import { Icon, type IconName } from "./icons.tsx";
import { ProjectLoadError } from "./ProjectLoadError.tsx";

export function Card({ title, subtitle, actions, children, flush, collapsed = false, className = "" }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; collapsed?: boolean; className?: string;
}) {
  if (collapsed) return <details className={`card disclosure ${className}`}><summary>{title ?? "Details"}</summary>
    <div className="card-body">{subtitle && <p className="muted small">{subtitle}</p>}{actions}{children}</div>
  </details>;
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

export function Empty({ icon, title, children }: { icon?: IconName; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon && <Icon name={icon} size={22} />}
      <strong className="secondary">{title}</strong>
      {children && <div className="small">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div className="error-box" role="alert">
      {api?.code === "project_invalid"
        ? <ProjectLoadError error={{ code: api.code, message: api.message, data: api.data }} />
        : <Markdown>{api ? api.message : String((error as Error)?.message ?? error)}</Markdown>}
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
      return <Empty title={error.message}>{empty}</Empty>;
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

// ------------------------------------------------------------------ shared pieces of the UI pass

/** Copies `value` to the clipboard; shows "Copied" briefly. */
export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button type="button" className="small ghost copy" aria-label={`${label} ${value}`}
            onClick={() => { void navigator.clipboard?.writeText(value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); }, () => undefined); }}>
      {copied ? "Copied" : label}
    </button>
  );
}

/** Implementation details (ids, codes, hashes, paths, toolchains) kept out of the way, each value copyable. */
export function Details({ summary = "Details", rows, children, open }: {
  summary?: ReactNode; rows?: [string, string | null | undefined][]; children?: ReactNode; open?: boolean;
}) {
  const shown = (rows ?? []).filter((r): r is [string, string] => !!r[1]);
  if (!shown.length && !children) return null;
  return (
    <details className="details" open={open}>
      <summary>{summary}</summary>
      {shown.length > 0 && (
        <dl className="details-rows">
          {shown.map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd><code>{v}</code><CopyButton value={v} /></dd></div>
          ))}
        </dl>
      )}
      {children}
    </details>
  );
}

/** Freshness, stated in words and separate from pass/fail: the result predates the current inputs. */
export function StaleNotice({ what, since, onRerun, rerunLabel, busy, children }: {
  what: string; since?: number | null; onRerun?: () => void; rerunLabel?: string; busy?: boolean; children?: ReactNode;
}) {
  return (
    <div className="stale-notice" role="status">
      <Icon name="clock" size={14} />
      <span><strong>{what} are out of date.</strong><Details summary="Why">They were computed from earlier inputs{since ? ` (${timeAgo(since)})` : ""}; robot.yaml or a pinned revision has changed since.</Details>{children}</span>
      {onRerun && <button className="small" onClick={onRerun} disabled={busy}><Icon name="refresh" size={12} />{rerunLabel ?? "Rerun"}</button>}
    </div>
  );
}

export type CheckOutcome = "passed" | "failed" | "not_run" | "unavailable" | "warning";

const CHECK_ICON: Record<CheckOutcome, IconName> = { passed: "check", failed: "x", not_run: "dot", unavailable: "dot", warning: "alert" };

/** One row of a checks list: a quiet mark, the check, its result text, and optional detail/action. */
export function CheckRow({ outcome, title, result, children, action }: {
  outcome: CheckOutcome; title: ReactNode; result?: ReactNode; children?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className={`check-row ${outcome}`}>
      <span className="mark" aria-label={outcome.replace("_", " ")}><Icon name={CHECK_ICON[outcome]} size={13} /></span>
      <div className="body">
        <div className="line"><span className="title">{title}</span>{result !== undefined && <span className="result">{result}</span>}</div>
        {children && <div className="detail">{children}</div>}
      </div>
      {action && <div className="action">{action}</div>}
    </div>
  );
}

/** Click-to-open panel anchored under its trigger; closes on outside click or Escape. */
export function Popover({ trigger, children, label, align = "left", className = "" }: {
  trigger: (open: boolean) => ReactNode; children: ReactNode | ((close: () => void) => ReactNode); label: string;
  align?: "left" | "right"; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [open]);
  const close = () => setOpen(false);
  return (
    <div className={`popover-root ${className}`} ref={root}>
      <button type="button" className="popover-trigger" aria-expanded={open} aria-haspopup="dialog" aria-label={label} onClick={() => setOpen((o) => !o)}>
        {trigger(open)}
      </button>
      {open && <div className={`popover ${align}`} role="dialog" aria-label={label}>{typeof children === "function" ? children(close) : children}</div>}
    </div>
  );
}

/** Tabs for narrow screens (Issues / 3D, Chat / Changes). */
export function Tabs<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={o.value === value} className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

/** Modal dialog with its own footer (forms); Escape and the backdrop close it. */
export function Dialog({ title, children, footer, onClose, wide }: {
  title: ReactNode; children: ReactNode; footer: ReactNode; onClose: () => void; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <header><h2 id="dialog-title">{title}</h2></header>
        <div className="body">{children}</div>
        <footer>{footer}</footer>
      </div>
    </div>
  );
}
