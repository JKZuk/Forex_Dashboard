import { fmtDateTime, timeAgo, titleCase } from "../lib/format";
import { useRefresh } from "../lib/refresh";
import type { RefreshStatus } from "../lib/types";

const JOB_LABEL: Record<string, string> = {
  macro_fred: "Macro data (FRED)",
  macro_official: "Macro data (official sources)",
  fx_prices: "FX prices",
  cot: "COT positioning (CFTC)",
  news: "News & sentiment",
  calendar: "Economic calendar",
  scoring: "Scoring",
};

const STATUS_TEXT: Record<string, string> = {
  ok: "OK",
  error: "Failed",
  running: "Running",
  never: "Not run yet",
  interrupted: "Interrupted",
};
const STATUS_ICON: Record<string, string> = { ok: "✓", error: "✕", running: "↻", never: "–", interrupted: "!" };

export function statusSummary(
  status: RefreshStatus | undefined,
  running: boolean,
  statusError: string | null,
): { kind: "ok" | "warn" | "error" | "busy" | "idle"; text: string } {
  if (statusError && !status) return { kind: "error", text: "API unreachable" };
  if (running) {
    const jobs = status?.jobs ?? [];
    const active = jobs.find((j) => j.status === "running");
    return { kind: "busy", text: active ? `Refreshing: ${JOB_LABEL[active.job] ?? titleCase(active.job)}` : "Refreshing…" };
  }
  if (!status) return { kind: "idle", text: "" };
  const failed = status.jobs.filter((j) => j.status === "error" || j.status === "interrupted").length;
  if (failed) return { kind: "warn", text: `${failed} source${failed > 1 ? "s" : ""} failed` };
  if (!status.last_success_at) return { kind: "idle", text: "no data yet" };
  return { kind: "ok", text: "" };
}

export function DataStatusPanel({ onClose, startError }: { onClose: () => void; startError: string | null }) {
  const { status, statusError, running, start } = useRefresh();
  return (
    <div className="popover" role="dialog" aria-label="Data status">
      <div className="popover-head">
        <h2>Data status</h2>
        <button className="btn btn-icon" onClick={onClose} aria-label="Close data status">
          {"✕"}
        </button>
      </div>
      {startError && <p className="inline-error">Refresh could not start: {startError}</p>}
      {statusError && <p className="inline-error">Status unavailable: {statusError}</p>}
      {!status && !statusError && <p className="muted">Loading status{"…"}</p>}
      {status && (
        <>
          <p className="muted small">
            {running
              ? "Refresh in progress. Sources run one after another, then scores are recomputed."
              : status.last_success_at
                ? `Last successful update ${fmtDateTime(status.last_success_at)} (${timeAgo(status.last_success_at)}).`
                : "No refresh has completed yet."}
            {status.auto_refresh_minutes > 0 ? ` Auto-refresh every ${status.auto_refresh_minutes} min.` : " Auto-refresh is off."}
          </p>
          <ul className="job-list">
            {status.jobs.map((j) => (
              <li key={j.job} className={`job job-${j.status}`}>
                <span className={`job-icon ${j.status}`} aria-hidden="true">
                  {STATUS_ICON[j.status] ?? "?"}
                </span>
                <div className="job-main">
                  <div className="job-title">
                    <span>{JOB_LABEL[j.job] ?? titleCase(j.job)}</span>
                    <span className="job-status">{STATUS_TEXT[j.status] ?? j.status}</span>
                  </div>
                  <div className="job-meta">
                    {j.status === "never" ? (
                      "No runs recorded"
                    ) : (
                      <>
                        {j.status === "running" ? `started ${timeAgo(j.started_at)}` : timeAgo(j.finished_at ?? j.started_at)}
                        {j.status === "ok" && ` · ${j.rows.toLocaleString("en-US")} rows`}
                        {j.status !== "ok" && j.last_success_at && ` · last OK ${timeAgo(j.last_success_at)}`}
                      </>
                    )}
                  </div>
                  {j.message && <div className="job-message">{j.message}</div>}
                </div>
                {status.sources.includes(j.job) && (
                  <button className="btn btn-small" disabled={running} onClick={() => start([j.job])} title={`Refresh only ${JOB_LABEL[j.job] ?? j.job}, then rescore`}>
                    Run
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
