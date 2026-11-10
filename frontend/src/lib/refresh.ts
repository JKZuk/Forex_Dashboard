import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { fetchJson } from "./api";
import type { RefreshStatus } from "./types";

export interface RefreshState {
  status: RefreshStatus | undefined;
  statusError: string | null;
  running: boolean;
  startError: string | null;
  /** start a refresh of all (or the named) sources */
  start: (sources?: string[]) => void;
}

export const RefreshContext = createContext<RefreshState>({
  status: undefined,
  statusError: null,
  running: false,
  startError: null,
  start: () => undefined,
});

export const useRefresh = () => useContext(RefreshContext);

const FAST_MS = 1500;
const SLOW_MS = 60000;

/** Owns refresh state: POST to start, poll status, bump the data version when done. */
export function useRefreshController(): { state: RefreshState; version: number } {
  const [status, setStatus] = useState<RefreshStatus>();
  const [statusError, setStatusError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [pending, setPending] = useState(false); // POST sent, first "running" poll not seen yet
  const [version, setVersion] = useState(0);
  const sawRunning = useRef(false);

  const poll = useCallback(async () => {
    try {
      const s = await fetchJson<RefreshStatus>("/api/refresh/status");
      setStatus(s);
      setStatusError(null);
      if (s.running) {
        sawRunning.current = true;
      } else if (sawRunning.current) {
        sawRunning.current = false;
        setVersion((v) => v + 1); // refresh finished: reload every view's data
      }
      if (!s.running) setPending(false);
    } catch (e) {
      setStatusError(e instanceof Error ? e.message : "Status unavailable");
      setPending(false);
    }
  }, []);

  const running = pending || (status?.running ?? false);

  useEffect(() => {
    void poll();
    const id = window.setInterval(() => void poll(), running ? FAST_MS : SLOW_MS);
    // Catch refreshes started elsewhere (CLI, scheduler) when the tab regains focus.
    const onVisible = () => document.visibilityState === "visible" && void poll();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [poll, running]);

  const start = useCallback(
    (sources?: string[]) => {
      setStartError(null);
      setPending(true);
      sawRunning.current = true;
      fetchJson("/api/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(sources?.length ? { sources } : {}),
      })
        .then(() => poll())
        .catch((e: unknown) => {
          sawRunning.current = false;
          setPending(false);
          setStartError(e instanceof Error ? e.message : "Could not start the refresh");
        });
    },
    [poll],
  );

  return { state: { status, statusError, running, startError, start }, version };
}
