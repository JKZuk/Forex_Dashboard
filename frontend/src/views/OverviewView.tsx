import { CardSkeleton, Delta, EmptyState, ErrorState, ScoreBar, ToneLabel, scoreText } from "../components/common";
import { useApi } from "../lib/api";
import { flag, fmtDate, groupLabel, pct, price, smart, titleCase } from "../lib/format";
import { useRefresh } from "../lib/refresh";
import { href } from "../lib/router";
import type { Gauge, Overview, OverviewRegion } from "../lib/types";

const LOW_COVERAGE = 0.6;

export function NoDataYet({ what = "scores" }: { what?: string }) {
  const { start, running } = useRefresh();
  return (
    <EmptyState title="No data yet">
      <p>
        There are no {what} in the database. Click Refresh to download the free data sources and compute the first
        scores. The first run can take a few minutes.
      </p>
      <button className="btn btn-primary" onClick={() => start()} disabled={running}>
        {running ? "Refreshing…" : "Refresh now"}
      </button>
    </EmptyState>
  );
}

function RegionCard({ item, horizons }: { item: OverviewRegion; horizons: string[] }) {
  const r = item.region;
  const scores = horizons.map((h) => item.scores[h]).filter((s) => s !== undefined);
  const real = scores.filter((s) => !s.no_data);
  const minCoverage = real.length ? Math.min(...real.map((s) => s.coverage)) : null;
  const tag = r.group === "influence" ? "Influence" : r.group === "haven" ? "Safe haven" : null;
  return (
    <a className="region-card card" href={href("region", r.code)} aria-label={`${r.name} (${r.currency}) details`}>
      <div className="rc-head">
        <span className="rc-flag" aria-hidden="true">
          {flag(r.code) || r.code}
        </span>
        <div className="rc-title">
          <span className="rc-ccy">{r.currency}</span>
          <span className="rc-name">{r.name}</span>
        </div>
        {tag && <span className="tag">{tag}</span>}
        {item.price && (
          <div className="rc-price" title={`${item.price.name}, ${fmtDate(item.price.date)}`}>
            <span className="rc-price-value">{price(item.price.value)}</span>
            <span className="rc-price-name">{item.price.name}</span>
          </div>
        )}
      </div>
      {scores.length === 0 ? (
        <p className="muted rc-empty">No scores yet for this region.</p>
      ) : (
        <div className="rc-scores">
          {horizons.map((h) => {
            const raw = item.scores[h];
            const s = raw?.no_data ? undefined : raw; // "No Data" is a grey gap, not Neutral
            return (
              <div className="rc-row" key={h}>
                <span className="rc-h">{titleCase(h)}</span>
                <ScoreBar score={s?.score ?? null} />
                <span className="rc-val">{scoreText(s?.score)}</span>
                <span className="rc-label">{s ? <ToneLabel score={s.score} label={s.label} /> : <span className="muted">No data</span>}</span>
                <span className="rc-delta">
                  <Delta value={s?.change_1w} digits={0} deadband={0.5} title="Change vs one week earlier (points)" />
                </span>
              </div>
            );
          })}
        </div>
      )}
      {item.headline && <p className="rc-headline">{item.headline}</p>}
      <div className="rc-foot">
        {item.price && (
          <span className="rc-changes">
            <span>1d {pct(item.price.change_1d_pct)}</span>
            <span>1w {pct(item.price.change_1w_pct)}</span>
            <span>1m {pct(item.price.change_1m_pct)}</span>
          </span>
        )}
        {minCoverage !== null && minCoverage < LOW_COVERAGE && (
          <span className="warn-note" title="Share of the model's weight that has data behind it. Low coverage means the score rests on few inputs.">
            <span aria-hidden="true">{"⚠"}</span> Low data coverage ({Math.round(minCoverage * 100)}%)
          </span>
        )}
      </div>
    </a>
  );
}

function Gauges() {
  const { data } = useApi<Gauge[]>("/api/global");
  if (!data?.length) return null;
  return (
    <section className="gauges" aria-label="Global risk and commodity gauges">
      {data.map((g) => (
        <div className="gauge" key={g.series_id} title={`${g.name} · ${fmtDate(g.date)}`}>
          <span className="gauge-name">{g.series_id.replace("GLOBAL.", "").replace(/_/g, " ")}</span>
          <span className="gauge-value">{smart(g.value)}</span>
          <Delta value={g.change_1w_pct} digits={1} suffix="% 1w" deadband={0.05} />
        </div>
      ))}
    </section>
  );
}

export function OverviewView() {
  const { data, error, loading, retry } = useApi<Overview>("/api/overview");

  if (error && !data) return <ErrorState message={error} onRetry={retry} />;
  if (!data) {
    return (
      <div className="card-grid">
        {Array.from({ length: 6 }, (_, i) => (
          <CardSkeleton key={i} rows={5} />
        ))}
      </div>
    );
  }
  if (!data.has_data) return <NoDataYet />;

  // Group in registry order (core regions come first there); the commodity bloc
  // gets its own framed group, single-region groups share the last row.
  const groups: { group: string; items: OverviewRegion[] }[] = [];
  for (const item of [...data.regions].sort((a, b) => Number(b.region.core) - Number(a.region.core))) {
    const g = groups.find((x) => x.group === item.region.group);
    if (g) g.items.push(item);
    else groups.push({ group: item.region.group, items: [item] });
  }
  const multi = groups.filter((g) => g.items.length > 1);
  const singles = groups.filter((g) => g.items.length === 1);

  return (
    <div className={loading ? "is-refetching" : ""}>
      <div className="view-head">
        <div>
          <h1>Overview</h1>
          <p className="sub">
            Sentiment score per currency, {"−"}100 (bearish) to +100 (bullish), on three horizons
            {data.as_of ? ` · scores as of ${fmtDate(data.as_of)}` : ""}
          </p>
        </div>
        <ScaleLegend />
      </div>
      <Gauges />
      {multi.map((g) => (
        <section key={g.group} className={`region-group group-${g.group}`}>
          <h2 className="group-title">
            {groupLabel(g.group)}
            {g.group === "commodity" && <span className="group-note">{g.items.map((i) => i.region.currency).join(" · ")} tend to move with commodities and risk appetite</span>}
          </h2>
          <div className="card-grid">
            {g.items.map((item) => (
              <RegionCard key={item.region.code} item={item} horizons={data.horizons} />
            ))}
          </div>
        </section>
      ))}
      {singles.length > 0 && (
        <section className="region-group">
          <h2 className="group-title">{singles.map((g) => groupLabel(g.group)).join(" & ")}</h2>
          <div className="card-grid">
            {singles.map((g) => (
              <RegionCard key={g.items[0]!.region.code} item={g.items[0]!} horizons={data.horizons} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export function ScaleLegend() {
  return (
    <div className="scale-legend" aria-label="Colour scale: orange is bearish, blue is bullish">
      <span>
        <span className="arrow bear" aria-hidden="true">
          {"▼"}
        </span>{" "}
        Bearish
      </span>
      <span className="scale-ramp" aria-hidden="true" />
      <span>
        Bullish{" "}
        <span className="arrow bull" aria-hidden="true">
          {"▲"}
        </span>
      </span>
    </div>
  );
}
