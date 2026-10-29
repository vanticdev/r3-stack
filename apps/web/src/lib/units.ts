// robot.yaml quantities ("260 mm", "15 min", "90 deg") to SI numbers, and display formatting.

const SCALE: Record<string, number> = {
  m: 1, mm: 1e-3, cm: 1e-2, km: 1e3, um: 1e-6,
  kg: 1, g: 1e-3,
  s: 1, ms: 1e-3, min: 60, h: 3600,
  rad: 1, deg: Math.PI / 180,
  "m/s": 1, "km/h": 1 / 3.6,
  V: 1, mV: 1e-3, A: 1, mA: 1e-3, W: 1, Hz: 1, kHz: 1e3, MHz: 1e6, ohm: 1, mohm: 1e-3,
  uH: 1e-6, mH: 1e-3, dBm: 1, dBi: 1, dB: 1, C: 1,
};

/** SI value of a quantity: numbers pass through; "<number> <unit>" is scaled. NaN when unparseable. */
export function quantity(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return NaN;
  const match = value.trim().match(/^([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*(\S*)$/i);
  if (!match) return NaN;
  const number = Number(match[1]);
  const unit = match[2] ?? "";
  if (!unit) return number;
  const scale = SCALE[unit];
  return scale === undefined ? NaN : number * scale;
}

export function quantityUnit(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.trim().match(/^[-+]?[\d.e+-]+\s*(\S*)$/i)?.[1] ?? "";
}

export function vector(value: unknown): [number, number, number] {
  const list = Array.isArray(value) ? value : [];
  return [quantity(list[0] ?? 0), quantity(list[1] ?? 0), quantity(list[2] ?? 0)];
}

/** Compact engineering formatting with a unit and an SI prefix (mm for lengths, mA for small currents). */
export function fmt(value: number | null | undefined, unit = "", digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (!Number.isFinite(value)) return value > 0 ? "∞" : "−∞";
  const abs = Math.abs(value);
  if (unit === "m" && abs < 1 && abs > 0) return `${round(value * 1000, digits)} mm`;
  if ((unit === "A" || unit === "V" || unit === "W" || unit === "s") && abs < 1 && abs >= 1e-3) return `${round(value * 1000, digits)} m${unit}`;
  if (unit === "s" && abs >= 120) return `${round(value / 60, digits)} min`;
  if (unit === "kg" && abs < 1) return `${round(value * 1000, digits)} g`;
  return unit ? `${round(value, digits)} ${unit}` : round(value, digits);
}

function round(value: number, digits: number): string {
  if (value === 0) return "0";
  const abs = Math.abs(value);
  const decimals = Math.max(0, digits - 1 - Math.floor(Math.log10(abs)));
  return value.toFixed(Math.min(decimals, 6)).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

export function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function humanize(id: string): string {
  return id.replace(/[_@]/g, " ").replace(/\s+/g, " ").trim();
}

export function timeAgo(epochSeconds: number | null | undefined, now = Date.now() / 1000): string {
  if (!epochSeconds) return "never";
  const dt = Math.max(0, now - epochSeconds);
  if (dt < 60) return "just now";
  if (dt < 3600) return `${Math.round(dt / 60)} min ago`;
  if (dt < 86400) return `${Math.round(dt / 3600)} h ago`;
  return `${Math.round(dt / 86400)} d ago`;
}
