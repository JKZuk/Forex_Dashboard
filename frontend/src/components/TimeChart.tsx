import { useEffect, useMemo, useRef, useState } from "react";
import { fmtDate } from "../lib/format";

export interface ChartPoint {
  t: number; // ms epoch
  v: number;
}

export interface ChartSeries {
  key: string;
  name: string;
  color: string; // CSS colour (usually a var(--token))
  points: ChartPoint[];
  area?: boolean; // light wash down to the zero line / chart floor
}

interface Props {
  series: ChartSeries[];
  ariaLabel: string;
  height?: number;
  yDomain?: [number, number];
  xDomain?: [number, number];
  format?: (v: number) => string;
  zeroLine?: boolean;
  /** controlled crosshair, to link stacked charts that share an x-axis */
  hoverT?: number | null;
  onHover?: (t: number | null) => void;
  legend?: boolean;
  table?: boolean;
  emptyText?: string;
}

const M = { top: 10, right: 14, bottom: 24, left: 50 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = 86400000;

export function useElementWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w !== undefined) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2.5 ? 5 : norm >= 1.5 ? 2 : 1) * mag;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    ticks.push(Math.abs(v) < step * 1e-9 ? 0 : Number(v.toPrecision(12)));
  }
  return ticks;
}

function timeTicks(t0: number, t1: number, maxTicks: number): { t: number; label: string }[] {
  const span = t1 - t0;
  const out: { t: number; label: string }[] = [];
  if (span <= 0) return out;
  if (span < 80 * DAY) {
    const stepDays = [1, 2, 7, 14, 28].find((s) => span / (s * DAY) <= maxTicks) ?? 28;
    const d = new Date(t0);
    d.setHours(0, 0, 0, 0);
    if (stepDays >= 7) d.setDate(d.getDate() + ((8 - d.getDay()) % 7)); // Mondays
    for (; d.getTime() <= t1; d.setDate(d.getDate() + stepDays)) {
      if (d.getTime() >= t0) out.push({ t: d.getTime(), label: `${d.getDate()} ${MONTHS[d.getMonth()]}` });
    }
    return out;
  }
  const months = span / (30.44 * DAY);
  const step = [1, 2, 3, 6, 12, 24, 60, 120].find((s) => months / s <= maxTicks) ?? 120;
  const start = new Date(t0);
  const d = new Date(start.getFullYear(), Math.ceil((start.getMonth() + (start.getDate() > 1 ? 1 : 0)) / step) * step, 1);
  for (; d.getTime() <= t1; d.setMonth(d.getMonth() + step)) {
    const yy = `’${String(d.getFullYear()).slice(2)}`;
    const label =
      step >= 12 ? String(d.getFullYear()) : d.getMonth() === 0 || out.length === 0 ? `${MONTHS[d.getMonth()]} ${yy}` : MONTHS[d.getMonth()]!;
    out.push({ t: d.getTime(), label });
  }
  return out;
}

function nearest(points: ChartPoint[], t: number): ChartPoint | null {
  if (!points.length) return null;
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid]!.t <= t) lo = mid;
    else hi = mid;
  }
  const a = points[lo]!;
  const b = points[hi]!;
  return Math.abs(a.t - t) <= Math.abs(b.t - t) ? a : b;
}

const defaultFormat = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2 });

export function TimeChart({
  series,
  ariaLabel,
  height = 220,
  yDomain,
  xDomain,
  format = defaultFormat,
  zeroLine = false,
  hoverT,
  onHover,
  legend,
  table = false,
  emptyText = "No data for this period.",
}: Props) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const [localHover, setLocalHover] = useState<number | null>(null);
  const controlled = hoverT !== undefined;
  const hover = controlled ? hoverT : localHover;
  const setHover = (t: number | null) => (onHover ? onHover(t) : setLocalHover(t));

  const visible = useMemo(() => {
    if (!xDomain) return series;
    const [a, b] = xDomain;
    return series.map((s) => ({ ...s, points: s.points.filter((p) => p.t >= a && p.t <= b) }));
  }, [series, xDomain]);

  const geometry = useMemo(() => {
    const all = visible.flatMap((s) => s.points);
    if (!all.length || width < 80) return null;
    let t0 = xDomain?.[0] ?? Infinity;
    let t1 = xDomain?.[1] ?? -Infinity;
    let lo = Infinity;
    let hi = -Infinity;
    for (const p of all) {
      if (!xDomain) {
        if (p.t < t0) t0 = p.t;
        if (p.t > t1) t1 = p.t;
      }
      if (p.v < lo) lo = p.v;
      if (p.v > hi) hi = p.v;
    }
    if (t1 <= t0) t1 = t0 + DAY;
    if (yDomain) [lo, hi] = yDomain;
    else {
      if (zeroLine) {
        lo = Math.min(lo, 0);
        hi = Math.max(hi, 0);
      }
      const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
      lo -= pad;
      hi += pad;
    }
    const yTicks = niceTicks(lo, hi, height < 150 ? 3 : 5);
    const innerW = width - M.left - M.right;
    const innerH = height - M.top - M.bottom;
    const x = (t: number) => M.left + ((t - t0) / (t1 - t0)) * innerW;
    const y = (v: number) => M.top + (1 - (v - lo) / (hi - lo)) * innerH;
    const xTicks = timeTicks(t0, t1, Math.max(2, Math.floor(innerW / 84)));
    const ts = Array.from(new Set(all.map((p) => p.t))).sort((a, b) => a - b);
    return { t0, t1, lo, hi, x, y, innerW, innerH, yTicks, xTicks, ts };
  }, [visible, width, height, yDomain, xDomain, zeroLine]);

  const showLegend = legend ?? series.length > 1;
  const hasData = visible.some((s) => s.points.length > 0);

  const legendRow = showLegend && (
    <div className="chart-legend">
      {series.map((s) => (
        <span key={s.key} className="legend-item">
          <span className="line-key" style={{ background: s.color }} />
          {s.name}
        </span>
      ))}
    </div>
  );

  if (!hasData) {
    return (
      <div ref={ref} className="chart">
        {legendRow}
        <div className="chart-empty" style={{ height }}>
          {emptyText}
        </div>
      </div>
    );
  }

  const g = geometry;
  const hoverPoints =
    g && hover != null && hover >= g.t0 && hover <= g.t1
      ? visible.map((s) => ({ s, p: nearest(s.points, hover) })).filter((h) => h.p !== null)
      : [];
  const hoverX = g && hoverPoints.length ? g.x(hover as number) : null;

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    if (!g) return;
    const box = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!box) return;
    const t = g.t0 + ((e.clientX - box.left - M.left) / g.innerW) * (g.t1 - g.t0);
    // snap to the nearest data position
    let best = g.ts[0] ?? t;
    let lo = 0;
    let hi = g.ts.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const v = g.ts[mid]!;
      if (Math.abs(v - t) < Math.abs(best - t)) best = v;
      if (v < t) lo = mid + 1;
      else hi = mid - 1;
    }
    if (best !== hover) setHover(best);
  };

  return (
    <div ref={ref} className="chart">
      {legendRow}
      <div className="chart-plot" style={{ height }}>
        {g && (
          <svg width={width} height={height} role="img" aria-label={ariaLabel}>
            {g.yTicks.map((v) => (
              <g key={v}>
                <line x1={M.left} x2={width - M.right} y1={g.y(v)} y2={g.y(v)} className={zeroLine && v === 0 ? "axis-line" : "grid-line"} />
                <text x={M.left - 8} y={g.y(v)} dy="0.32em" textAnchor="end" className="tick">
                  {format(v)}
                </text>
              </g>
            ))}
            {zeroLine && g.lo < 0 && g.hi > 0 && !g.yTicks.includes(0) && (
              <line x1={M.left} x2={width - M.right} y1={g.y(0)} y2={g.y(0)} className="axis-line" />
            )}
            {g.xTicks.map((tk) => (
              <text key={tk.t} x={g.x(tk.t)} y={height - 6} textAnchor="middle" className="tick">
                {tk.label}
              </text>
            ))}
            {visible.map((s) => {
              if (!s.points.length) return null;
              const stride = Math.max(1, Math.floor(s.points.length / (g.innerW * 1.5)));
              const pts = stride === 1 ? s.points : s.points.filter((_, i) => i % stride === 0 || i === s.points.length - 1);
              const d = pts.map((p, i) => `${i ? "L" : "M"}${g.x(p.t).toFixed(1)},${g.y(p.v).toFixed(1)}`).join("");
              const last = pts[pts.length - 1]!;
              const first = pts[0]!;
              const base = g.y(Math.min(Math.max(0, g.lo), g.hi));
              return (
                <g key={s.key}>
                  {s.area && (
                    <path d={`${d}L${g.x(last.t).toFixed(1)},${base}L${g.x(first.t).toFixed(1)},${base}Z`} fill={s.color} opacity={0.1} />
                  )}
                  <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                  <circle cx={g.x(last.t)} cy={g.y(last.v)} r={4} fill={s.color} className="dot-ring" />
                </g>
              );
            })}
            {hoverX !== null && (
              <g pointerEvents="none">
                <line x1={hoverX} x2={hoverX} y1={M.top} y2={height - M.bottom} className="crosshair" />
                {hoverPoints.map(({ s, p }) => (
                  <circle key={s.key} cx={g.x(p!.t)} cy={g.y(p!.v)} r={4.5} fill={s.color} className="dot-ring" />
                ))}
              </g>
            )}
            <rect
              x={M.left}
              y={M.top}
              width={Math.max(0, g.innerW)}
              height={Math.max(0, g.innerH)}
              fill="transparent"
              onPointerMove={onMove}
              onPointerLeave={() => setHover(null)}
            />
          </svg>
        )}
        {g && hoverX !== null && (
          <div
            className="chart-tooltip"
            style={hoverX > width * 0.6 ? { right: width - hoverX + 12 } : { left: hoverX + 12 }}
          >
            <div className="tt-date">{fmtDate(new Date(hover as number))}</div>
            {hoverPoints.map(({ s, p }) => (
              <div key={s.key} className="tt-row">
                <span className="line-key" style={{ background: s.color }} />
                <strong>{format(p!.v)}</strong>
                <span className="tt-name">{s.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {table && <ChartTable series={visible} format={format} />}
    </div>
  );
}

function ChartTable({ series, format }: { series: ChartSeries[]; format: (v: number) => string }) {
  const [open, setOpen] = useState(false);
  const rows = useMemo(() => {
    if (!open) return [];
    const byT = new Map<number, Record<string, number>>();
    for (const s of series) {
      for (const p of s.points) {
        const row = byT.get(p.t) ?? {};
        row[s.key] = p.v;
        byT.set(p.t, row);
      }
    }
    return Array.from(byT.entries())
      .sort((a, b) => b[0] - a[0])
      .slice(0, 150);
  }, [open, series]);
  return (
    <details className="chart-table" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Data table</summary>
      {open && (
        <div className="table-scroll" style={{ maxHeight: 240 }}>
          <table className="data">
            <thead>
              <tr>
                <th>Date</th>
                {series.map((s) => (
                  <th key={s.key} className="num">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(([t, row]) => (
                <tr key={t}>
                  <td>{fmtDate(new Date(t))}</td>
                  {series.map((s) => (
                    <td key={s.key} className="num">
                      {row[s.key] === undefined ? "–" : format(row[s.key]!)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}

export function Sparkline({
  values,
  width = 84,
  height = 22,
  zero = false,
  label,
}: {
  values: number[];
  width?: number;
  height?: number;
  zero?: boolean;
  label: string;
}) {
  if (values.length < 2) return <span className="muted">{"–"}</span>;
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (zero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (hi === lo) hi = lo + 1;
  const pad = 4;
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (height - pad * 2);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return (
    <svg width={width} height={height} className="spark" role="img" aria-label={label}>
      {zero && lo < 0 && hi > 0 && <line x1={pad} x2={width - pad} y1={y(0)} y2={y(0)} className="grid-line" />}
      <path d={d} fill="none" stroke="var(--spark)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1]!)} r={2.5} fill="var(--text-2)" />
    </svg>
  );
}
