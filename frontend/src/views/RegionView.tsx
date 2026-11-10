import { useMemo, useState } from "react";
import { Card, CardSkeleton, Delta, EmptyState, ErrorState, ImpactBadge, Segmented, SentimentChip, ToneLabel, scoreText } from "../components/common";
import { Sparkline, TimeChart, type ChartSeries } from "../components/TimeChart";
import { useApi } from "../lib/api";
import { dateTs, flag, fmtDate, fmtDateShort, fmtDateTime, fmtTime, groupLabel, parseDate, pct, price, signed, smart, timeAgo, titleCase } from "../lib/format";
import { href } from "../lib/router";
import type { CalendarEvent, Component, CotHistory, HorizonPanel, IndicatorRow, NewsItem, PriceHistory, Region, RegionDetail, ScoreHistory, Series } from "../lib/types";
import { NoDataYet } from "./OverviewView";

const DAY = 86400000;
const RANGES = [
  { value: "3M", label: "3M", days: 92 },
  { value: "6M", label: "6M", days: 183 },
  { value: "1Y", label: "1Y", days: 366 },
  { value: "2Y", label: "2Y", days: 731 },
  { value: "MAX", label: "Max", days: 0 },
] as const;
type RangeKey = (typeof RANGES)[number]["value"];

export const HORIZON_COLOR: Record<string, string> = {
  short: "var(--h-short)",
  medium: "var(--h-medium)",
  long: "var(--h-long)",
};
const horizonColor = (h: string, i: number) => HORIZON_COLOR[h] ?? ["var(--h-short)", "var(--h-medium)", "var(--h-long)"][i % 3]!;

// ---------------------------------------------------------------- drivers ---
function DriverRow({ c, scale }: { c: Component; scale: number }) {
  const [open, setOpen] = useState(false);
  const w = Math.min(50, (Math.abs(c.contribution) / scale) * 50);
  const positive = c.contribution > 0;
  return (
    <li className={`driver ${c.has_data ? "" : "driver-nodata"}`}>
      <button className="driver-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open} title={c.detail || undefined}>
        <span className="driver-label">
          <span className="driver-name">{c.label}</span>
          <span className="driver-raw">
            {c.has_data ? c.raw_display || `signal ${signed(c.signal ?? 0, 2)}` : "no data"}
            <span className="driver-weight"> {"·"} weight {Math.round(c.weight * 100)}%</span>
          </span>
        </span>
        <span className="driver-bar" aria-hidden="true">
          <span className="scorebar-mid" />
          {c.has_data && Math.abs(c.contribution) >= 0.05 && (
            <span className={`driver-fill ${positive ? "bull" : "bear"}`} style={positive ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
          )}
        </span>
        <span className="driver-val">{c.has_data ? signed(c.contribution, 1) : "–"}</span>
      </button>
      {open && (
        <div className="driver-detail">
          {c.detail || "No further detail for this driver."}
          {c.has_data && c.signal !== null && (
            <span className="muted">
              {" "}
              Signal {signed(c.signal, 2)} {"×"} weight {Math.round(c.weight * 100)}% {"→"} {signed(c.contribution, 1)} points.
            </span>
          )}
        </div>
      )}
    </li>
  );
}

function HorizonCard({ panel, scale }: { panel: HorizonPanel; scale: number }) {
  const noData = panel.score?.no_data ?? false;
  const s = noData ? null : panel.score; // "No Data" renders as a grey gap, not Neutral
  const missing = panel.components.filter((c) => !c.has_data).length;
  return (
    <Card className="horizon-card">
      <div className="hz-head">
        <div>
          <div className="hz-name">{titleCase(panel.horizon)} term</div>
          {s ? <ToneLabel score={s.score} label={s.label} /> : <span className="muted">{noData ? "No data" : "No score"}</span>}
        </div>
        <div className="hz-score">
          <span className="hz-value">{scoreText(s?.score)}</span>
          {s && <Delta value={s.change_1w} digits={0} deadband={0.5} suffix=" 1w" title="Change vs one week earlier (points)" />}
        </div>
      </div>
      {s && (
        <div className={`hz-coverage ${s.coverage < 0.6 ? "warn-note" : "muted"}`}>
          {s.coverage < 0.6 && <span aria-hidden="true">{"⚠"} </span>}
          Data coverage {Math.round(s.coverage * 100)}%{missing > 0 ? ` · ${missing} driver${missing > 1 ? "s" : ""} without data` : ""}
        </div>
      )}
      {panel.components.length === 0 ? (
        <p className="muted">No driver breakdown stored for this horizon.</p>
      ) : (
        <>
          <div className="driver-axis" aria-hidden="true">
            <span>Driver</span>
            <span className="driver-axis-mid">
              <span>{"◀"} bear</span>
              <span>bull {"▶"}</span>
            </span>
            <span>pts</span>
          </div>
          <ul className="drivers">
            {panel.components.map((c) => (
              <DriverRow key={c.component} c={c} scale={scale} />
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- history ---
function HistoryCard({ region }: { region: Region }) {
  const [range, setRange] = useState<RangeKey>("1Y");
  const [hover, setHover] = useState<number | null>(null);
  const scores = useApi<ScoreHistory>(`/api/history/scores?region=${region.code}`);
  const prices = useApi<PriceHistory>(`/api/history/price?region=${region.code}`);

  const scoreSeries: ChartSeries[] = useMemo(
    () =>
      Object.entries(scores.data?.series ?? {}).map(([h, pts], i) => ({
        key: h,
        name: `${titleCase(h)} term`,
        color: horizonColor(h, i),
        points: pts.map((p) => ({ t: dateTs(p.date), v: p.score })),
      })),
    [scores.data],
  );
  const priceSeries: ChartSeries[] = useMemo(
    () => [
      {
        key: "price",
        name: prices.data?.name ?? "Price",
        color: "var(--price-line)",
        points: (prices.data?.points ?? []).map((p) => ({ t: dateTs(p.date), v: p.value })),
      },
    ],
    [prices.data],
  );

  const xDomain = useMemo<[number, number] | undefined>(() => {
    const ends = [...scoreSeries, ...priceSeries].map((s) => s.points[s.points.length - 1]?.t).filter((t): t is number => t !== undefined);
    const starts = [...scoreSeries, ...priceSeries].map((s) => s.points[0]?.t).filter((t): t is number => t !== undefined);
    if (!ends.length) return undefined;
    const end = Math.max(...ends);
    const days = RANGES.find((r) => r.value === range)?.days ?? 0;
    // Max: start where the scores start, so both panels cover the same window.
    const scoreStart = scoreSeries.map((s) => s.points[0]?.t).filter((t): t is number => t !== undefined);
    const start = days ? end - days * DAY : scoreStart.length ? Math.min(...scoreStart) : Math.min(...starts);
    return [start, end];
  }, [scoreSeries, priceSeries, range]);

  const loading = (scores.loading && !scores.data) || (prices.loading && !prices.data);
  const error = scores.error ?? prices.error;
  const hasScores = scoreSeries.some((s) => s.points.length > 0);
  const hasPrice = (priceSeries[0]?.points.length ?? 0) > 0;

  return (
    <Card
      title="Score vs price history"
      aside={<Segmented label="Time range" value={range} onChange={setRange} options={RANGES.map((r) => ({ value: r.value, label: r.label }))} />}
      dim={scores.loading || prices.loading}
    >
      {loading ? (
        <div className="skeleton" style={{ height: 380 }} />
      ) : error && !hasScores && !hasPrice ? (
        <ErrorState message={error} onRetry={() => (scores.retry(), prices.retry())} />
      ) : !hasScores && !hasPrice ? (
        <p className="muted">No score or price history stored yet.</p>
      ) : (
        <div className="stacked-charts">
          <div>
            <h3 className="chart-title">Sentiment score ({"−"}100 to +100)</h3>
            <TimeChart
              ariaLabel={`${region.currency} sentiment score history by horizon`}
              series={scoreSeries}
              yDomain={[-100, 100]}
              xDomain={xDomain}
              zeroLine
              height={210}
              format={(v) => signed(v, 0)}
              hoverT={hover}
              onHover={setHover}
              emptyText="No score history in this period."
              table
            />
          </div>
          <div>
            <h3 className="chart-title">
              {prices.data?.name ?? "Price"}
              <span className="muted"> {"·"} {region.currency === "USD" ? "dollar index, higher = stronger USD" : `USD per 1 ${region.currency}, higher = stronger ${region.currency}`}</span>
            </h3>
            <TimeChart
              ariaLabel={`${prices.data?.name ?? "price"} history`}
              series={priceSeries}
              xDomain={xDomain}
              height={170}
              format={price}
              hoverT={hover}
              onHover={setHover}
              legend={false}
              emptyText="No price history stored for this currency."
              table
            />
          </div>
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------- indicators ---
function unitSuffix(unit: string): string {
  return unit === "%" ? "%" : "";
}

function IndicatorTable({ rows }: { rows: IndicatorRow[] }) {
  if (!rows.length) return <p className="muted">No macro indicators stored for this region yet.</p>;
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th>Indicator</th>
            <th className="num">Latest</th>
            <th className="num">Previous</th>
            <th className="num">Change</th>
            <th>Trend</th>
            <th>Period</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.series_id}>
              <td>
                <span className="ind-name" title={r.note || r.series_id}>
                  {r.name}
                </span>
                {r.note && <span className="ind-note">{r.note}</span>}
              </td>
              <td className="num strong">
                {smart(r.latest_value)}
                {unitSuffix(r.unit)}
                {r.unit && r.unit !== "%" && <span className="unit"> {r.unit}</span>}
              </td>
              <td className="num">
                {r.previous_value === null ? "–" : smart(r.previous_value)}
                {r.previous_value !== null && unitSuffix(r.unit)}
              </td>
              <td className="num">
                <Delta
                  value={r.change}
                  digits={Math.abs(r.change ?? 0) >= 100 ? 0 : 2}
                  deadband={0.004}
                  invert={r.bullish_when === "lower"}
                  title={
                    r.bullish_when
                      ? `A ${r.bullish_when} reading is usually positive for the currency; the arrow colour reflects that.`
                      : undefined
                  }
                />
              </td>
              <td>
                <Sparkline values={r.spark.map((p) => p.value)} label={`${r.name}, last ${r.spark.length} readings`} />
              </td>
              <td className="nowrap">{fmtDate(r.latest_date)}</td>
              <td className="muted">{r.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ trade ---
function TradeCharts({ series }: { series: Series[] }) {
  return (
    <div className="mini-grid">
      {series.map((s) => {
        const last = s.points[s.points.length - 1];
        return (
          <div key={s.series_id} className="mini-chart">
            <h3 className="chart-title">
              {titleCase((s.indicator ?? s.series_id).toLowerCase()).replace("yoy", "y/y").replace("gdp", "% of GDP")}
              {s.unit && <span className="muted"> {"·"} {s.unit}</span>}
              {last && (
                <span className="chart-title-value">
                  {smart(last.value)} <span className="muted">({fmtDateShort(last.date)})</span>
                </span>
              )}
            </h3>
            <TimeChart
              ariaLabel={`${s.name ?? s.series_id} history`}
              series={[{ key: s.series_id, name: s.name ?? s.series_id, color: "var(--series-1)", points: s.points.map((p) => ({ t: dateTs(p.date), v: p.value })), area: true }]}
              height={150}
              zeroLine
              format={smart}
              legend={false}
              table
            />
          </div>
        );
      })}
    </div>
  );
}

// -------------------------------------------------------------------- COT ---
function CotMini({ region }: { region: Region }) {
  const { data, loading, error } = useApi<CotHistory>(`/api/cot/${region.currency}`);
  const points = useMemo(() => {
    const all = (data?.points ?? []).map((p) => ({ t: dateTs(p.date), v: p.net_pct_oi }));
    const last = all[all.length - 1];
    return last ? all.filter((p) => p.t >= last.t - 731 * DAY) : all;
  }, [data]);
  const last = data?.points[data.points.length - 1];
  return (
    <Card
      title="Speculative positioning (COT)"
      aside={
        <a href={href("cot", region.currency)} className="link-more">
          All currencies {"→"}
        </a>
      }
    >
      {loading && !data ? (
        <div className="skeleton" style={{ height: 150 }} />
      ) : error && !data ? (
        <p className="inline-error">{error}</p>
      ) : !points.length ? (
        <p className="muted">No CFTC positioning data for {region.currency} (there may be no liquid futures contract, or the COT source has not been refreshed).</p>
      ) : (
        <>
          {last && (
            <p className="mini-stat">
              Net <strong>{signed(last.net_pct_oi, 1)}%</strong> of open interest <span className="muted">({signed(last.net, 0)} contracts, {fmtDate(last.date)})</span>
            </p>
          )}
          <TimeChart
            ariaLabel={`${region.currency} speculative net position, percent of open interest`}
            series={[{ key: "net", name: "Net % of open interest", color: "var(--series-1)", points, area: true }]}
            height={150}
            zeroLine
            format={(v) => `${signed(v, 0)}%`}
            legend={false}
          />
        </>
      )}
    </Card>
  );
}

// ----------------------------------------------------------- news / events ---
export function NewsList({ items, region }: { items: NewsItem[]; region?: string }) {
  return (
    <ul className="news-list">
      {items.map((n) => (
        <li key={n.id}>
          <div className="news-main">
            <a href={n.url} target="_blank" rel="noopener noreferrer" className="news-title">
              {n.title}
            </a>
            <div className="news-meta">
              {n.source} {"·"} <span title={fmtDateTime(n.published_at)}>{timeAgo(n.published_at)}</span>
              {n.kind === "central_bank" && <span className="tag">Central bank</span>}
            </div>
          </div>
          <div className="news-chips">
            {region && n.region_score !== null ? (
              <SentimentChip score={n.region_score} prefix={region} />
            ) : (
              n.regions.slice(0, 4).map((s) => <SentimentChip key={s.region} score={s.score} prefix={s.region === "GLOBAL" ? "Risk" : s.region} />)
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function eventDay(e: CalendarEvent): Date {
  // Date-only events keep their calendar date; timed events use local time.
  return e.time_known ? new Date(e.event_time) : parseDate(e.event_time.slice(0, 10));
}

function EventList({ events }: { events: CalendarEvent[] }) {
  return (
    <ul className="event-list">
      {events.map((e) => (
        <li key={e.id}>
          <span className="event-when">
            <span>{fmtDateShort(eventDay(e))}</span>
            <span className="muted">{e.time_known ? fmtTime(e.event_time) : "time tba"}</span>
          </span>
          <span className="event-title">{e.title}</span>
          <ImpactBadge impact={e.impact} />
          <span className="event-figs muted">
            {e.actual && <>A {e.actual} </>}
            {e.forecast && <>F {e.forecast} </>}
            {e.previous && <>P {e.previous}</>}
          </span>
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------- view ---
export function RegionView({ code, regions, regionsLoading }: { code: string; regions: Region[]; regionsLoading: boolean }) {
  const known = regions.find((r) => r.code === code);
  const { data, error, loading, retry } = useApi<RegionDetail>(known ? `/api/regions/${code}` : null);

  if (regionsLoading) return <CardSkeleton rows={6} />;
  if (!known) {
    return (
      <EmptyState title={`Unknown region “${code}”`}>
        <p>
          <a href={href()}>Back to the overview</a>
        </p>
      </EmptyState>
    );
  }

  const tabs = (
    <nav className="region-tabs" aria-label="Regions">
      {regions.map((r) => (
        <a key={r.code} href={href("region", r.code)} className={r.code === code ? "active" : ""} title={r.name}>
          <span aria-hidden="true">{flag(r.code)}</span> {r.currency}
        </a>
      ))}
    </nav>
  );

  if (error && !data)
    return (
      <>
        {tabs}
        <ErrorState message={error} onRetry={retry} />
      </>
    );
  if (!data)
    return (
      <>
        {tabs}
        <div className="three-col">
          <CardSkeleton rows={7} />
          <CardSkeleton rows={7} />
          <CardSkeleton rows={7} />
        </div>
      </>
    );

  const r = data.region;
  const scale = Math.max(5, ...data.horizons.flatMap((p) => p.components.map((c) => Math.abs(c.contribution))));
  const tag = r.group === "major" ? null : groupLabel(r.group);

  return (
    <div className={loading ? "is-refetching" : ""}>
      {tabs}
      <div className="view-head">
        <div className="region-title">
          <span className="rc-flag big" aria-hidden="true">
            {flag(r.code) || r.code}
          </span>
          <div>
            <h1>
              {r.name} <span className="muted">{r.currency}</span>
              {tag && <span className="tag">{tag}</span>}
            </h1>
            <p className="sub">{data.as_of ? `Scores as of ${fmtDate(data.as_of)}` : "No scores yet"}</p>
          </div>
        </div>
        {data.price && (
          <div className="price-box" title={`${data.price.name}, ${fmtDate(data.price.date)}`}>
            <span className="price-name">{data.price.name}</span>
            <span className="price-value">{price(data.price.value)}</span>
            <span className="price-changes">
              <span>1d {pct(data.price.change_1d_pct)}</span>
              <span>1w {pct(data.price.change_1w_pct)}</span>
              <span>1m {pct(data.price.change_1m_pct)}</span>
            </span>
          </div>
        )}
      </div>

      {!data.has_data && <NoDataYet what={`scores or indicators for ${r.name}`} />}

      {data.conclusion && (
        <Card className="conclusion">
          <h2 className="conclusion-headline">{data.conclusion.headline}</h2>
          <p>{data.conclusion.text}</p>
        </Card>
      )}

      {data.has_data && (
        <>
          <div className="three-col">
            {data.horizons.map((p) => (
              <HorizonCard key={p.horizon} panel={p} scale={scale} />
            ))}
          </div>
          <p className="muted small below-grid">
            Bars show how many points each driver adds to or subtracts from the score; all three panels share one scale.
            Click a driver for its explanation.
          </p>

          <HistoryCard region={r} />

          {data.trade.length > 0 &&
            (r.trade_sensitive ? (
              <Card title="Trade context" aside={<span className="muted small">Trade flows carry extra weight for {r.currency}</span>}>
                <TradeCharts series={data.trade} />
              </Card>
            ) : (
              <details className="card collapsible">
                <summary>
                  <h2>Trade context</h2>
                  <span className="muted small">secondary driver for {r.currency} {"–"} click to expand</span>
                </summary>
                <TradeCharts series={data.trade} />
              </details>
            ))}

          <Card title="Key indicators" aside={<span className="muted small">Latest release vs previous</span>}>
            <IndicatorTable rows={data.indicators} />
          </Card>

          <div className="split">
            <CotMini region={r} />
            <Card
              title="Upcoming events"
              aside={
                <a href={href("calendar")} className="link-more">
                  Calendar {"→"}
                </a>
              }
            >
              {data.events.length ? <EventList events={data.events} /> : <p className="muted">No upcoming events stored for {r.name}.</p>}
            </Card>
          </div>

          <Card
            title={`Recent news for ${r.currency}`}
            aside={<span className="muted small">Chip = estimated effect on {r.currency}</span>}
          >
            {data.news.length ? <NewsList items={data.news} region={r.code} /> : <p className="muted">No recent news tagged for {r.name}.</p>}
          </Card>
        </>
      )}
    </div>
  );
}
