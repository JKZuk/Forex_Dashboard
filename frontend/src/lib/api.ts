import { createContext, useContext, useEffect, useState } from "react";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" }, ...init });
  } catch {
    throw new ApiError(0, "Cannot reach the local API. Is the server running?");
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        if (typeof d === "string") detail = d;
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/** Bumped after every finished refresh; every useApi hook refetches when it changes. */
export const DataVersionContext = createContext(0);

const cache = new Map<string, unknown>();

export interface ApiState<T> {
  data: T | undefined;
  error: string | null;
  /** true while a request is in flight (data may still hold the previous result) */
  loading: boolean;
  retry: () => void;
}

interface Inner<T> {
  path: string | null;
  data: T | undefined;
  error: string | null;
  loading: boolean;
}

/**
 * Tiny GET hook with an in-memory cache. On refetch (data version bump, retry,
 * or - with keepPrevious - a changed path) the previous data stays visible
 * instead of flashing a skeleton.
 */
export function useApi<T>(path: string | null, opts?: { keepPrevious?: boolean }): ApiState<T> {
  const version = useContext(DataVersionContext);
  const keepPrevious = opts?.keepPrevious ?? false;
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<Inner<T>>(() => ({
    path,
    data: path ? (cache.get(path) as T | undefined) : undefined,
    error: null,
    loading: path !== null,
  }));

  useEffect(() => {
    if (path === null) {
      setState({ path, data: undefined, error: null, loading: false });
      return;
    }
    let alive = true;
    setState((s) => ({
      path,
      data: s.path === path || keepPrevious ? (s.data ?? (cache.get(path) as T | undefined)) : (cache.get(path) as T | undefined),
      error: null,
      loading: true,
    }));
    fetchJson<T>(path)
      .then((data) => {
        cache.set(path, data);
        if (alive) setState({ path, data, error: null, loading: false });
      })
      .catch((e: unknown) => {
        if (alive) {
          setState((s) => ({
            ...s,
            error: e instanceof Error ? e.message : "Request failed",
            loading: false,
          }));
        }
      });
    return () => {
      alive = false;
    };
  }, [path, version, tick, keepPrevious]);

  // Path changed but the effect has not run yet: do not show the old path's data.
  const stale = state.path !== path && !keepPrevious;
  return {
    data: stale ? (path ? (cache.get(path) as T | undefined) : undefined) : state.data,
    error: stale ? null : state.error,
    loading: stale ? path !== null : state.loading,
    retry: () => setTick((t) => t + 1),
  };
}
