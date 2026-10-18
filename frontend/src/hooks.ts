import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Focus, PackInfo, TermPreset } from "./api";

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

/** Run an async loader when deps change; aborts the previous run. */
export function useAsync<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    loadRef
      .current(ctrl.signal)
      .then((d) => {
        if (!ctrl.signal.aborted) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e: Error) => {
        if (ctrl.signal.aborted || e.name === "AbortError") return;
        setError(e);
        setLoading(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · Aletheia` : "Aletheia";
  }, [title]);
}

/* ------------------------------------------------------------------ pack context */

export const PackContext = createContext<AsyncState<PackInfo> | null>(null);

export function usePack(): AsyncState<PackInfo> {
  const ctx = useContext(PackContext);
  if (!ctx) throw new Error("usePack outside PackContext");
  return ctx;
}

export const PRIMARY_FOCUSES = ["FYC", "TECHCOMM"];

export const FALLBACK_PRESETS: TermPreset[] = [
  { id: "semester-16", label: "16-week semester", weeks: 16, fixed_start: 1, fixed_end: 1, pages_per_week: 40 },
  { id: "semester-15", label: "15-week semester", weeks: 15, fixed_start: 1, fixed_end: 1, pages_per_week: 40 },
  { id: "quarter-10", label: "10-week quarter", weeks: 10, fixed_start: 0, fixed_end: 1, pages_per_week: 50 },
  { id: "summer-8", label: "8-week summer term", weeks: 8, fixed_start: 0, fixed_end: 1, pages_per_week: 60 },
  { id: "summer-5", label: "5-week summer term", weeks: 5, fixed_start: 0, fixed_end: 0, pages_per_week: 75 },
];

export function presetsOf(pack: PackInfo | null): TermPreset[] {
  const p = pack?.config.term_presets;
  return Array.isArray(p) && p.length ? p : FALLBACK_PRESETS;
}

export function focusesOf(pack: PackInfo | null): Focus[] {
  const f = pack?.config.focuses;
  return Array.isArray(f) ? f : [];
}

export function focusLabel(pack: PackInfo | null, code: string): string {
  return focusesOf(pack).find((f) => f.code === code)?.label ?? code;
}
