// Number / date formatting helpers. Dates from the API are ISO strings:
// "YYYY-MM-DD" for dates, "...Z" (UTC) for datetimes.

export const MINUS = "−";

export function signed(value: number, digits = 0): string {
  const rounded = Number(value.toFixed(digits));
  const body = Math.abs(rounded).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  if (rounded > 0) return `+${body}`;
  if (rounded < 0) return `${MINUS}${body}`;
  return body;
}

export function num(value: number, digits = 2): string {
  const body = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return value < 0 ? `${MINUS}${body}` : body;
}

/** Compact form for large values: 12.9K, 4.2M. */
export function compact(value: number, withSign = false): string {
  const abs = Math.abs(value);
  let body: string;
  if (abs >= 1e6) body = `${(abs / 1e6).toFixed(abs >= 1e7 ? 1 : 2)}M`;
  else if (abs >= 1e4) body = `${(abs / 1e3).toFixed(1)}K`;
  else body = abs.toLocaleString("en-US", { maximumFractionDigits: abs < 100 ? 2 : 0 });
  const sign = value < 0 ? MINUS : withSign && value > 0 ? "+" : "";
  return sign + body;
}

/** Enough decimals for an FX rate or index level whatever its magnitude. */
export function price(value: number): string {
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 1 : abs >= 20 ? 2 : abs >= 0.1 ? 4 : 5;
  return num(value, digits);
}

export function smart(value: number): string {
  const abs = Math.abs(value);
  return num(value, abs >= 1000 ? 0 : abs >= 100 ? 1 : 2);
}

export function pct(value: number | null | undefined, digits = 2): string {
  return value == null ? "–" : `${signed(value, digits)}%`;
}

export function parseDate(iso: string): Date {
  // Plain dates are parsed as local midnight so they never shift a day.
  if (iso.length === 10) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
  }
  return new Date(iso);
}

export function dateTs(iso: string): number {
  return parseDate(iso).getTime();
}

const dShort = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
const dLong = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const dDay = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });
const tShort = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const dtShort = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export const fmtDate = (iso: string | Date) => dLong.format(typeof iso === "string" ? parseDate(iso) : iso);
export const fmtDateShort = (iso: string | Date) => dShort.format(typeof iso === "string" ? parseDate(iso) : iso);
export const fmtDay = (d: Date) => dDay.format(d);
export const fmtTime = (iso: string) => tShort.format(new Date(iso));
export const fmtDateTime = (iso: string) => dtShort.format(new Date(iso));

export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

export function titleCase(text: string): string {
  return text.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function tone(value: number | null | undefined, deadband = 0): "bull" | "bear" | "flat" {
  if (value == null || Math.abs(value) <= deadband) return "flat";
  return value > 0 ? "bull" : "bear";
}

export const ARROW = { bull: "▲", bear: "▼", flat: "▬" } as const;

/** Regional-indicator flag from a region code (cosmetic; falls back to none). */
const FLAG_ALIAS: Record<string, string> = { EZ: "EU", UK: "GB" };
export function flag(code: string): string {
  const iso = FLAG_ALIAS[code] ?? code;
  if (!/^[A-Z]{2}$/.test(iso)) return "";
  return String.fromCodePoint(...[...iso].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

const GROUP_LABEL: Record<string, string> = {
  major: "Majors",
  commodity: "Commodity bloc",
  influence: "Influence",
  haven: "Safe haven",
};
export const groupLabel = (group: string) => GROUP_LABEL[group] ?? titleCase(group);
