import { useMemo, useState } from "react";
import { Card, CardSkeleton, Delta, EmptyState, ErrorState, Segmented } from "../components/common";
import { Sparkline, TimeChart } from "../components/TimeChart";
import { useApi } from "../lib/api";
import { compact, dateTs, fmtDate, signed } from "../lib/format";
import { useRefresh } from "../lib/refresh";
import { href } from "../lib/router";
import type { CotHistory, CotSummary } from "../lib/types";

const DAY = 86400000;
const RANGES = [
  { value: "1Y", label: "1Y", days: 366 },
  { value: "3Y", label: "3Y", days: 1096 },
  { value: "MAX", label: "Max", days: 0 },
];

function PercentileBar({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">not enough history</span>;
  return (
    <span className="pctl" title={`Current net % of open interest is higher than ${value.toFixed(0)}% of weekly readings in the last 3 years`}>
      <span className="pctl-track" aria-hidden="true">
        <span className="pctl-zone low" />
        <span className="pctl-zone high" />
        <span className="pctl-marker" style={{ left: `${value}%` }} />
      </span>
      <span className="pctl-value">{value.toFixed(0)}</span>
    </span>
  );
}

function History({ currency }: { currency: string }) {
  const { data, loading, error, retry } = useApi<CotHistory>(`/api/cot/${currency}`, { keepPrevious: true });
  const [range, setRange] = useState("3Y");
  const [hover, setHover] = useState<number | null>(null);
  const series = useMemo(() => {
    const pts = data?.currency === currency ? data.points : [];
    return {
      net: pts.map((p) => ({ t: dateTs(p.date), v: p.net_pct_oi })),
      long: pts.map((p) => ({ t: dateTs(p.date), v: p.noncomm_long })),
      short: pts.map((p) => ({ t: dateTs(p.date), v: p.noncomm_short })),
    };
  }, [data, currency]);
  const last = series.net[series.net.length - 1]?.t;
  const days = RANGES.find((r) => r.value === range)?.days ?? 0;
  const xDomain: [number, number] | undefined = last && days ? [last - days * DAY, last] : undefined;

  return (
    <Card
      title={`${currency} positioning history`}
      aside={<Segmented label="Time range" value={range} onChange={setRange} options={RANGES} />}
      dim={loading && !!data}
    >
      {loading && !data ? (
        <div className="skeleton" style={{ height: 360 }} />
      ) : error && !data ? (
        <ErrorState message={error} onRetry={retry} />
      ) : !series.net.length ? (
        <p className="muted">No positioning history stored for {currency}.</p>
      ) : (
        <div className="stacked-charts">
          <div>
            <h3 className="chart-title">Speculative net position, % of open interest</h3>
            <TimeChart
              ariaLabel={`${currency} net speculative position as a percentage of open interest`}
              series={[{ key: "net", name: "Net % of OI", color: "var(--series-1)", points: series.net, area: true }]}
              xDomain={xDomain}
              height={200}
              zeroLine
              format={(v) => `${signed(v, 0)}%`}
              legend={false}
              hoverT={hover}
              onHover={setHover}
              table
            />
          </div>
          <div>
            <h3 className="chart-title">Gross speculative longs and shorts, contracts</h3>
            <TimeChart
              ariaLabel={`${currency} gross speculative long and short contracts`}
              series={[
                { key: "long", name: "Longs", color: "var(--bull)", points: series.long },
                { key: "short", name: "Shorts", color: "var(--bear)", points: series.short },
              ]}
              xDomain={xDomain}
              height={170}
              format={(v) => compact(v)}
              hoverT={hover}
              onHover={setHover}
              table
            />
          </div>
        </div>
      )}
    </Card>
  );
}

export function CotView({ selected }: { selected?: string }) {
  const { data, error, loading, retry } = useApi<CotSummary>("/api/cot");
  const { start, running } = useRefresh();
  // The selected currency lives in the URL (#/cot/EUR) so it survives reloads.
  const setPicked = (currency: string) => {
    window.history.replaceState(null, "", href("cot", currency));
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  };

  if (error && !data) return <ErrorState message={error} onRetry={retry} />;
  if (!data) return <CardSkeleton rows={9} height={20} />;
  if (!data.has_data) {
    return (
      <EmptyState title="No positioning data yet">
        <p>The CFTC Commitments of Traders report has not been downloaded. Click Refresh to fetch it.</p>
        <button className="btn btn-primary" onClick={() => start()} disabled={running}>
          {running ? "Refreshing…" : "Refresh now"}
        </button>
      </EmptyState>
    );
  }

  const current = data.rows.find((r) => r.currency === selected)?.currency ?? data.rows[0]?.currency;

  return (
    <div className={loading ? "is-refetching" : ""}>
      <div className="view-head">
        <div>
          <h1>COT positioning</h1>
          <p className="sub">
            CFTC Commitments of Traders: non-commercial (speculative) futures positions, weekly
            {data.report_date ? ` · latest report ${fmtDate(data.report_date)}` : ""}
          </p>
        </div>
      </div>
      <Card>
        <div className="table-scroll">
          <table className="data selectable">
            <thead>
              <tr>
                <th>Currency</th>
                <th className="num">Net contracts</th>
                <th className="num">Net % of OI</th>
                <th className="num">1w change</th>
                <th className="num">4w change</th>
                <th>52 weeks</th>
                <th>
                  3-year percentile <span className="muted">(0 = most short, 100 = most long)</span>
                </th>
                <th>Flag</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.currency} className={r.currency === current ? "is-selected" : ""} onClick={() => setPicked(r.currency)}>
                  <td>
                    <button className="row-btn" aria-pressed={r.currency === current}>
                      {r.currency}
                    </button>
                    {r.currency === "USD" && <span className="muted small"> index</span>}
                  </td>
                  <td className="num">{signed(r.net, 0)}</td>
                  <td className="num strong">{signed(r.net_pct_oi, 1)}%</td>
                  <td className="num">
                    <Delta value={r.change_1w} digits={0} />
                  </td>
                  <td className="num">
                    <Delta value={r.change_4w} digits={0} />
                  </td>
                  <td>
                    <Sparkline values={r.spark.map((p) => p.value)} zero label={`${r.currency} net % of open interest, last 52 weeks`} />
                  </td>
                  <td>
                    <PercentileBar value={r.percentile_3y} />
                  </td>
                  <td>
                    {r.extreme ? (
                      <span className="chip chip-warn" title="Positioning in the top or bottom 10% of its 3-year range: crowded trades are prone to sharp reversals.">
                        <span aria-hidden="true">{"⚠"}</span> Extreme {r.extreme}
                      </span>
                    ) : (
                      <span className="muted">{"–"}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">
          Positive net = speculators are net long the currency against USD (USD row: the dollar index contract). Shaded
          ends of the percentile bar mark the extreme zones (below 10, above 90). Click a row to chart its history.
        </p>
      </Card>
      {current && <History currency={current} />}
    </div>
  );
}
