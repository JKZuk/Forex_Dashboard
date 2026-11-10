import { useMemo, useState } from "react";
import { Card, CardSkeleton, EmptyState, ErrorState, ImpactBadge, Segmented } from "../components/common";
import { useApi } from "../lib/api";
import { flag, fmtDay, fmtTime } from "../lib/format";
import { href } from "../lib/router";
import type { Calendar, CalendarEvent, Region } from "../lib/types";
import { eventDay } from "./RegionView";

const IMPACTS = ["high", "medium", "low"];
const RANGES = [
  { value: "7", label: "1 week" },
  { value: "14", label: "2 weeks" },
  { value: "30", label: "30 days" },
];

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function relativeDay(d: Date): string | null {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - today.getTime()) / 86400000);
  return diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : diff === -1 ? "Yesterday" : null;
}

export function CalendarView({ regions }: { regions: Region[] }) {
  const [impacts, setImpacts] = useState<string[]>(["high"]);
  const [picked, setPicked] = useState<string[]>([]); // empty = all regions
  const [days, setDays] = useState("14");

  const params = new URLSearchParams({ days });
  if (impacts.length && impacts.length < IMPACTS.length) params.set("impact", impacts.join(","));
  if (picked.length) params.set("region", picked.join(","));
  const { data, error, loading, retry } = useApi<Calendar>(impacts.length ? `/api/calendar?${params.toString()}` : null, { keepPrevious: true });

  const groups = useMemo(() => {
    const map = new Map<string, { day: Date; events: CalendarEvent[] }>();
    for (const e of data?.events ?? []) {
      const day = eventDay(e);
      const key = dayKey(day);
      const g = map.get(key) ?? { day, events: [] };
      g.events.push(e);
      map.set(key, g);
    }
    return Array.from(map.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, g]) => ({
        key,
        day: g.day,
        // date-only events first, then by local time
        events: g.events.sort((a, b) => Number(a.time_known) - Number(b.time_known) || a.event_time.localeCompare(b.event_time)),
      }));
  }, [data]);

  const toggle = (list: string[], value: string) => (list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const names = new Map(regions.map((r) => [r.code, r]));
  const now = Date.now();

  return (
    <div>
      <div className="view-head">
        <div>
          <h1>Economic calendar</h1>
          <p className="sub">Upcoming releases and central bank events {"·"} times shown in your timezone ({tz})</p>
        </div>
      </div>

      <div className="filters" role="group" aria-label="Calendar filters">
        <div className="filter">
          <span className="filter-label">Impact</span>
          {IMPACTS.map((i) => (
            <button key={i} className={`pill ${impacts.includes(i) ? "active" : ""}`} aria-pressed={impacts.includes(i)} onClick={() => setImpacts(toggle(impacts, i))}>
              {i}
            </button>
          ))}
        </div>
        <div className="filter">
          <span className="filter-label">Region</span>
          <button className={`pill ${picked.length === 0 ? "active" : ""}`} aria-pressed={picked.length === 0} onClick={() => setPicked([])}>
            All
          </button>
          {regions.map((r) => (
            <button key={r.code} className={`pill ${picked.includes(r.code) ? "active" : ""}`} aria-pressed={picked.includes(r.code)} onClick={() => setPicked(toggle(picked, r.code))} title={r.name}>
              {r.currency}
            </button>
          ))}
        </div>
        <div className="filter">
          <span className="filter-label">Range</span>
          <Segmented label="Range" value={days} onChange={setDays} options={RANGES} />
        </div>
      </div>

      {!impacts.length ? (
        <EmptyState title="No impact level selected">
          <p>Select at least one impact level to see events.</p>
        </EmptyState>
      ) : error && !data ? (
        <ErrorState message={error} onRetry={retry} />
      ) : !data ? (
        <CardSkeleton rows={8} height={20} />
      ) : groups.length === 0 ? (
        <EmptyState title="No events match">
          <p>
            No calendar events in the next {days} days for these filters. If the calendar has never been loaded, click
            Refresh; otherwise widen the impact or region filter.
          </p>
        </EmptyState>
      ) : (
        <div className={loading ? "is-refetching" : ""}>
          {groups.map((g) => {
            const rel = relativeDay(g.day);
            return (
              <Card key={g.key} className="day-card">
                <h2 className="day-title">
                  {rel && <span className="day-rel">{rel}</span>}
                  {fmtDay(g.day)}
                  <span className="muted day-count">
                    {g.events.length} event{g.events.length > 1 ? "s" : ""}
                  </span>
                </h2>
                <table className="data cal-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Region</th>
                      <th>Event</th>
                      <th>Impact</th>
                      <th className="num">Actual</th>
                      <th className="num">Forecast</th>
                      <th className="num">Previous</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.events.map((e) => {
                      const r = names.get(e.region);
                      const past = e.time_known && new Date(e.event_time).getTime() < now;
                      return (
                        <tr key={e.id} className={past ? "is-past" : ""}>
                          <td className="nowrap cal-time">{e.time_known ? fmtTime(e.event_time) : <span className="muted">All day</span>}</td>
                          <td className="nowrap">
                            {r ? (
                              <a href={href("region", r.code)} title={r.name}>
                                <span aria-hidden="true">{flag(r.code)}</span> {r.currency}
                              </a>
                            ) : (
                              e.region
                            )}
                          </td>
                          <td>{e.title}</td>
                          <td>
                            <ImpactBadge impact={e.impact} />
                          </td>
                          <td className="num strong">{e.actual || "–"}</td>
                          <td className="num">{e.forecast || "–"}</td>
                          <td className="num">{e.previous || "–"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
