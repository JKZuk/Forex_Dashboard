import { useState } from "react";
import { Card, CardSkeleton, EmptyState, ErrorState, Segmented } from "../components/common";
import { useApi } from "../lib/api";
import type { NewsFeed, Region } from "../lib/types";
import { NewsList } from "./RegionView";

const KINDS = [
  { value: "all", label: "All" },
  { value: "news", label: "News" },
  { value: "central_bank", label: "Central banks" },
];
const PAGE = 40;

export function NewsView({ regions }: { regions: Region[] }) {
  const [region, setRegion] = useState("");
  const [kind, setKind] = useState("all");
  const [limit, setLimit] = useState(PAGE);

  const params = new URLSearchParams({ limit: String(limit) });
  if (region) params.set("region", region);
  if (kind !== "all") params.set("kind", kind);
  const { data, error, loading, retry } = useApi<NewsFeed>(`/api/news?${params.toString()}`, { keepPrevious: true });

  const pick = (code: string) => {
    setRegion(code);
    setLimit(PAGE);
  };

  return (
    <div>
      <div className="view-head">
        <div>
          <h1>News feed</h1>
          <p className="sub">
            Headlines from free RSS feeds, scored locally. Chips show the estimated effect on each currency ({"▲"} bullish,{" "}
            {"▼"} bearish); {"“"}Risk{"”"} is the global risk tone.
          </p>
        </div>
      </div>
      <div className="filters" role="group" aria-label="News filters">
        <div className="filter">
          <span className="filter-label">Region</span>
          <button className={`pill ${region === "" ? "active" : ""}`} aria-pressed={region === ""} onClick={() => pick("")}>
            All
          </button>
          {regions.map((r) => (
            <button key={r.code} className={`pill ${region === r.code ? "active" : ""}`} aria-pressed={region === r.code} onClick={() => pick(r.code)} title={r.name}>
              {r.currency}
            </button>
          ))}
          <button className={`pill ${region === "GLOBAL" ? "active" : ""}`} aria-pressed={region === "GLOBAL"} onClick={() => pick("GLOBAL")}>
            Global risk
          </button>
        </div>
        <div className="filter">
          <span className="filter-label">Type</span>
          <Segmented label="Type" value={kind} onChange={(k) => (setKind(k), setLimit(PAGE))} options={KINDS} />
        </div>
      </div>
      {error && !data ? (
        <ErrorState message={error} onRetry={retry} />
      ) : !data ? (
        <CardSkeleton rows={10} height={20} />
      ) : data.items.length === 0 ? (
        <EmptyState title="No news items">
          <p>No stored headlines match these filters. If news has never been loaded, click Refresh.</p>
        </EmptyState>
      ) : (
        <Card dim={loading}>
          <NewsList items={data.items} region={region && region !== "GLOBAL" ? region : undefined} />
          <div className="list-foot">
            <span className="muted small">
              Showing {data.items.length} of {data.total}
            </span>
            {data.items.length < data.total && (
              <button className="btn" onClick={() => setLimit((l) => Math.min(200, l + PAGE))} disabled={loading || limit >= 200}>
                Show more
              </button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
