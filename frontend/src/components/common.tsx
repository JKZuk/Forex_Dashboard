import type { ReactNode } from "react";
import { ARROW, MINUS, signed, tone } from "../lib/format";

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

export function CardSkeleton({ rows = 4, height = 16 }: { rows?: number; height?: number }) {
  return (
    <div className="card" aria-busy="true">
      <Skeleton height={18} width="40%" />
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} style={{ marginTop: 12 }}>
          <Skeleton height={height} width={`${90 - ((i * 13) % 35)}%`} />
        </div>
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <div className="state-title">
        <span aria-hidden="true">!</span> Could not load data
      </div>
      <p>{message}</p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state">
      <div className="state-title">{title}</div>
      {children}
    </div>
  );
}

export function Card({
  title,
  aside,
  children,
  className = "",
  dim = false,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  dim?: boolean;
}) {
  return (
    <section className={`card ${className} ${dim ? "is-refetching" : ""}`}>
      {(title || aside) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {aside && <div className="card-aside">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

/** Signed change with an arrow; the arrow carries colour, the text stays ink. */
export function Delta({
  value,
  digits = 1,
  suffix = "",
  deadband = 0,
  invert = false,
  title,
}: {
  value: number | null | undefined;
  digits?: number;
  suffix?: string;
  deadband?: number;
  invert?: boolean; // a rise is currency-negative (e.g. unemployment)
  title?: string;
}) {
  if (value == null) return <span className="muted">{"–"}</span>;
  const direction = tone(value, deadband);
  const colour = invert && direction !== "flat" ? (direction === "bull" ? "bear" : "bull") : direction;
  return (
    <span className="delta" title={title}>
      <span className={`arrow ${colour}`} aria-hidden="true">
        {direction === "bull" ? ARROW.bull : direction === "bear" ? ARROW.bear : ARROW.flat}
      </span>
      {signed(value, digits)}
      {suffix}
    </span>
  );
}

/** Diverging -100..+100 bar: fill grows from the centre line. */
export function ScoreBar({ score, max = 100 }: { score: number | null; max?: number }) {
  const s = score ?? 0;
  const w = Math.min(100, (Math.abs(s) / max) * 100) / 2;
  const t = tone(score, 0.05);
  return (
    <span className="scorebar" aria-hidden="true">
      <span className="scorebar-mid" />
      {score !== null && t !== "flat" && (
        <span className={`scorebar-fill ${t}`} style={t === "bull" ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
      )}
    </span>
  );
}

export function scoreText(score: number | null | undefined): string {
  return score == null ? "–" : signed(score, 0);
}

export function ToneLabel({ score, label }: { score: number; label: string }) {
  const t = /bull/i.test(label) ? "bull" : /bear/i.test(label) ? "bear" : tone(score, 15);
  return (
    <span className="tone-label">
      <span className={`arrow ${t}`} aria-hidden="true">
        {ARROW[t]}
      </span>
      {label}
    </span>
  );
}

export function SentimentChip({ score, prefix }: { score: number; prefix?: string }) {
  const t = tone(score, 0.1);
  const word = t === "bull" ? "bullish" : t === "bear" ? "bearish" : "neutral";
  return (
    <span className={`chip chip-${t}`} title={`${prefix ? `${prefix}: ` : ""}${word} (${signed(score, 2)} on a ${MINUS}1..+1 scale)`}>
      {prefix && <span className="chip-prefix">{prefix}</span>}
      <span className={`arrow ${t}`} aria-hidden="true">
        {ARROW[t]}
      </span>
      {signed(score, 2)}
    </span>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? "active" : ""} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ImpactBadge({ impact }: { impact: string }) {
  const bars = impact === "high" ? 3 : impact === "medium" ? 2 : 1;
  return (
    <span className={`impact impact-${impact}`} title={`${impact} impact`}>
      <span className="impact-bars" aria-hidden="true">
        {[1, 2, 3].map((i) => (
          <i key={i} className={i <= bars ? "on" : ""} />
        ))}
      </span>
      {impact}
    </span>
  );
}
