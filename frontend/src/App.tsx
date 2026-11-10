import { useEffect, useRef, useState } from "react";
import { DataStatusPanel, statusSummary } from "./components/DataStatus";
import { DataVersionContext, useApi } from "./lib/api";
import { timeAgo } from "./lib/format";
import { RefreshContext, useRefresh, useRefreshController } from "./lib/refresh";
import { href, useRoute } from "./lib/router";
import type { Region } from "./lib/types";
import { CalendarView } from "./views/CalendarView";
import { CotView } from "./views/CotView";
import { HeatmapView } from "./views/HeatmapView";
import { NewsView } from "./views/NewsView";
import { OverviewView } from "./views/OverviewView";
import { RegionView } from "./views/RegionView";

const NAV = [
  { key: "", label: "Overview" },
  { key: "heatmap", label: "Heatmap" },
  { key: "cot", label: "Positioning" },
  { key: "calendar", label: "Calendar" },
  { key: "news", label: "News" },
];

type Theme = "light" | "dark";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    // Follow the OS setting until the user picks a theme manually.
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = (e: MediaQueryListEvent) => {
      let stored: string | null = null;
      try {
        stored = localStorage.getItem("fx-theme");
      } catch {
        /* storage unavailable */
      }
      if (!stored) setTheme(e.matches ? "dark" : "light");
    };
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const toggle = () =>
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem("fx-theme", next);
      } catch {
        /* storage unavailable */
      }
      return next;
    });
  return [theme, toggle];
}

function Header({ route, theme, onToggleTheme }: { route: string[]; theme: Theme; onToggleTheme: () => void }) {
  const { status, statusError, running, start, startError } = useRefresh();
  const [open, setOpen] = useState(false);
  const [, setNow] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setNow((n) => n + 1), 30000); // keep "x min ago" fresh
    return () => window.clearInterval(id);
  }, []);
  const autoOpened = useRef(false);
  const failed = status?.jobs.some((j) => j.status === "error" || j.status === "interrupted") ?? false;
  useEffect(() => {
    if (running) {
      // show per-source progress while refreshing
      setOpen((o) => {
        if (!o) autoOpened.current = true;
        return true;
      });
    } else if (autoOpened.current) {
      // finished: get out of the way unless something failed
      autoOpened.current = false;
      if (!failed) setOpen(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const current = route[0] === "region" ? "" : (route[0] ?? "");
  const summary = statusSummary(status, running, statusError);

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="brand" href={href()}>
          <span className="brand-mark" aria-hidden="true">
            <i className="bear" />
            <i className="bull" />
            <i className="bull short" />
          </span>
          FX Sentiment
        </a>
        <nav className="nav" aria-label="Views">
          {NAV.map((n) => (
            <a key={n.key} href={n.key ? href(n.key) : href()} className={current === n.key && route[0] !== "region" ? "active" : ""} aria-current={current === n.key ? "page" : undefined}>
              {n.label}
            </a>
          ))}
        </nav>
        <div className="topbar-right">
          <button className={`status-chip ${summary.kind}`} onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Data status">
            <span className={`status-dot ${summary.kind}`} aria-hidden="true" />
            <span className="status-text">
              {running ? summary.text : `Updated ${timeAgo(status?.last_success_at)}`}
              {!running && summary.kind !== "ok" && summary.text ? ` · ${summary.text}` : ""}
            </span>
          </button>
          <button className="btn btn-primary" onClick={() => start()} disabled={running}>
            <span className={`refresh-icon ${running ? "spin" : ""}`} aria-hidden="true">
              {"↻"}
            </span>
            {running ? "Refreshing…" : "Refresh"}
          </button>
          <button className="btn btn-icon" onClick={onToggleTheme} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
            {theme === "dark" ? "☼" : "☾"}
          </button>
        </div>
      </div>
      {open && (
        <>
          <div className="popover-backdrop" onClick={() => setOpen(false)} />
          <DataStatusPanel onClose={() => setOpen(false)} startError={startError} />
        </>
      )}
    </header>
  );
}

export function App() {
  const controller = useRefreshController();
  const [theme, toggleTheme] = useTheme();
  const route = useRoute();

  return (
    <RefreshContext.Provider value={controller.state}>
      <DataVersionContext.Provider value={controller.version}>
        <Header route={route} theme={theme} onToggleTheme={toggleTheme} />
        <main className="page">
          <Routes route={route} />
        </main>
        <footer className="footer">
          Informational sentiment model built on free, delayed data. Scores and score differentials are not investment
          advice.
        </footer>
      </DataVersionContext.Provider>
    </RefreshContext.Provider>
  );
}

function Routes({ route }: { route: string[] }) {
  // Regions always come from the backend registry.
  const regions = useApi<Region[]>("/api/regions");
  const list = regions.data ?? [];
  switch (route[0]) {
    case undefined:
      return <OverviewView />;
    case "heatmap":
      return <HeatmapView />;
    case "region":
      return <RegionView code={(route[1] ?? "").toUpperCase()} regions={list} regionsLoading={regions.loading && !regions.data} />;
    case "cot":
      return <CotView selected={route[1]?.toUpperCase()} />;
    case "calendar":
      return <CalendarView regions={list} />;
    case "news":
      return <NewsView regions={list} />;
    default:
      return (
        <div className="state">
          <div className="state-title">Page not found</div>
          <p>
            <a href={href()}>Back to the overview</a>
          </p>
        </div>
      );
  }
}
