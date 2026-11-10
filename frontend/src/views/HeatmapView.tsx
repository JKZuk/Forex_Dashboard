import { useState } from "react";
import { Card, CardSkeleton, ErrorState, Segmented, scoreText } from "../components/common";
import { useApi } from "../lib/api";
import { fmtDate, signed, titleCase } from "../lib/format";
import { href } from "../lib/router";
import type { Heatmap, PairIdea } from "../lib/types";
import { NoDataYet, ScaleLegend } from "./OverviewView";

/** Diverging cell colour: neutral grey at 0, blue (bullish) / orange (bearish) poles. */
export function heatStyle(value: number | null, clamp: number): React.CSSProperties {
  if (value === null) return {};
  const share = Math.min(1, Math.abs(value) / clamp);
  const pole = value >= 0 ? "var(--heat-bull)" : "var(--heat-bear)";
  return { background: `color-mix(in oklab, ${pole} ${(share * 100).toFixed(0)}%, var(--heat-mid))` };
}
const heatClass = (value: number | null, clamp: number) =>
  value === null ? "heat-cell heat-none" : `heat-cell ${Math.abs(value) / clamp >= 0.55 ? "heat-strong" : ""}`;

function IdeaList({ title, ideas, scores }: { title: string; ideas: PairIdea[]; scores: Record<string, number | null> }) {
  const max = Math.max(1, ...ideas.map((i) => i.differential));
  return (
    <div className="ideas">
      <h3>{title}</h3>
      {ideas.length === 0 ? (
        <p className="muted">None on this horizon.</p>
      ) : (
        <ol>
          {ideas.map((i) => (
            <li key={i.pair}>
              <span className="idea-pair">{i.pair}</span>
              <span className="idea-why">
                {i.strong} {scoreText(scores[i.strong])} vs {i.weak} {scoreText(scores[i.weak])}
              </span>
              <span className="idea-bar" aria-hidden="true">
                <i className={i.direction === "long" ? "bull" : "bear"} style={{ width: `${(i.differential / max) * 100}%` }} />
              </span>
              <span className="idea-diff">{signed(i.direction === "long" ? i.differential : -i.differential, 0)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function HeatmapView() {
  const { data, error, loading, retry } = useApi<Heatmap>("/api/heatmap");
  const [horizon, setHorizon] = useState<string | null>(null);

  if (error && !data) return <ErrorState message={error} onRetry={retry} />;
  if (!data)
    return (
      <div className="split">
        <CardSkeleton rows={9} />
        <CardSkeleton rows={9} />
      </div>
    );
  if (!data.has_data) return <NoDataYet />;

  const h = horizon && data.horizons.includes(horizon) ? horizon : (data.horizons[0] ?? "short");
  const scored = data.currencies.filter((c) => c.scored && c.scores[h] != null);
  const scoreOf: Record<string, number | null> = Object.fromEntries(data.currencies.map((c) => [c.currency, c.scores[h] ?? null]));
  const ideas = data.pairs[h] ?? [];
  const top = ideas[0];
  const longs = ideas.filter((i) => i.direction === "long").slice(0, 5);
  const shorts = ideas.filter((i) => i.direction === "short").slice(0, 5);
  const ranked = [...scored].sort((a, b) => (b.scores[h] ?? 0) - (a.scores[h] ?? 0));

  return (
    <div className={loading ? "is-refetching" : ""}>
      <div className="view-head">
        <div>
          <h1>Currency heatmap</h1>
          <p className="sub">
            Which currencies are strong or weak, and which pairs have the widest score gap
            {data.as_of ? ` · as of ${fmtDate(data.as_of)}` : ""}
          </p>
        </div>
        <ScaleLegend />
      </div>
      <div className="split split-heat">
        <Card title="Scores by horizon">
          <table className="heat-table">
            <thead>
              <tr>
                <th />
                {data.horizons.map((x) => (
                  <th key={x} className={x === h ? "is-selected" : ""}>
                    {titleCase(x)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.currencies.map((c) => (
                <tr key={c.currency}>
                  <th scope="row">
                    <a href={href("region", c.region)} title={c.name}>
                      {c.currency}
                    </a>
                  </th>
                  {data.horizons.map((x) => {
                    const v = c.scores[x] ?? null;
                    return (
                      <td key={x}>
                        <div className={heatClass(v, 75)} style={heatStyle(v, 75)} title={v === null ? `${c.currency} ${x}-term: no data` : `${c.currency} ${x}-term: ${signed(v, 0)} (${c.labels[x] ?? ""})`}>
                          {scoreText(v)}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Colour saturates at {"±"}75. Click a currency for its drivers.</p>
        </Card>

        <Card
          title="Pair matrix: score differential"
          aside={<Segmented label="Horizon" value={h} onChange={setHorizon} options={data.horizons.map((x) => ({ value: x, label: titleCase(x) }))} />}
        >
          {scored.length < 2 ? (
            <p className="muted">Not enough scored currencies on this horizon.</p>
          ) : (
            <>
              <div className="table-scroll">
                <table className="heat-table matrix">
                  <thead>
                    <tr>
                      <th className="corner">
                        base {"↓"} / quote {"→"}
                      </th>
                      {ranked.map((q) => (
                        <th key={q.currency}>{q.currency}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {ranked.map((b) => (
                      <tr key={b.currency}>
                        <th scope="row">
                          {b.currency} <span className="muted">{scoreText(b.scores[h])}</span>
                        </th>
                        {ranked.map((q) => {
                          if (q.currency === b.currency)
                            return (
                              <td key={q.currency}>
                                <div className="heat-cell heat-diag" />
                              </td>
                            );
                          const diff = (b.scores[h] ?? 0) - (q.scores[h] ?? 0);
                          const isTop = top !== undefined && ((top.strong === b.currency && top.weak === q.currency) || (top.weak === b.currency && top.strong === q.currency));
                          return (
                            <td key={q.currency}>
                              <div
                                className={`${heatClass(diff, 150)} ${isTop ? "heat-top" : ""}`}
                                style={heatStyle(diff, 150)}
                                title={`${b.currency}/${q.currency}: ${b.currency} ${scoreText(b.scores[h])} minus ${q.currency} ${scoreText(q.scores[h])} = ${signed(diff, 0)}`}
                              >
                                {signed(diff, 0)}
                              </div>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small">
                Cell = row currency score minus column currency score ({titleCase(h)}-term). Rows and columns are sorted
                strongest to weakest, so the widest gaps sit in the corners; the outlined cells mark the strongest vs
                weakest currency{top ? ` (${top.strong} vs ${top.weak}, ${top.differential.toFixed(0)} points)` : ""}.
              </p>
              <div className="ideas-grid">
                <IdeaList title="Largest positive differentials (pair as quoted)" ideas={longs} scores={scoreOf} />
                <IdeaList title="Largest negative differentials (pair as quoted)" ideas={shorts} scores={scoreOf} />
              </div>
              <p className="muted small">
                Ranked by score differential, using conventional pair names: a positive value means the base currency
                scores higher than the quote currency, a negative value the opposite. These are model score gaps to
                help decide which pairs to look at {"–"} not trade recommendations.
              </p>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
