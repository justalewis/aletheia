/**
 * Client for the program endpoints (backend/programs/api.py) and the admin tokens this browser keeps.
 * Admin calls send the program's secret token in the X-Program-Token header.
 */
import { ApiError, type BuildState, type SectionInfo } from "../api";
import { readJSON, writeJSON } from "../storage";

export interface ProgramSettings {
  textbook_cost_per_student: number;
  currency: string;
  terms: string[];
}

export interface ProgramArcBrief {
  id: string;
  title: string;
  summary: string;
  focus: string;
  unit_count: number;
  readings: number;
  assignments: number;
  published: boolean;
  order: number;
  updated: string;
  source_build: string | null;
}

export interface ProgramInfo {
  id: string;
  name: string;
  institution: string;
  description: string;
  settings: ProgramSettings;
  arcs: ProgramArcBrief[];
}

export interface ProgramArcFull extends ProgramArcBrief {
  state: BuildState;
  program: { id: string; name: string };
}

export interface ProgramSection {
  id: string;
  title: string;
  updated: string;
  section: Partial<SectionInfo>;
  instructor: string;
  units: number;
  readings: number;
  assignments: number;
}

export interface ReportRow {
  build_id: string;
  title: string;
  course: string;
  section: string;
  term: string;
  instructor: string;
  enrollment: number;
  readings: number;
  assignments: number;
  zero_cost: boolean;
  open: number;
  link_only: number;
  not_free: { kind: string; id: string; cost: string; reason: string }[];
  note: string;
  savings: number;
  updated: string;
}

export interface ZtcReport {
  program: { id: string; name: string; institution: string };
  term: string;
  terms: string[];
  textbook_cost_per_student: number;
  currency: string;
  rows: ReportRow[];
  totals: { sections: number; students: number; zero_cost_sections: number; zero_cost_students: number; savings: number };
  warnings: string[];
}

/* ------------------------------------------------------------------ admin tokens in this browser */

const KEY = "aletheia:programs";

interface Held {
  token: string;
  name: string;
}

function held(): Record<string, Held> {
  const data = readJSON<Record<string, Held>>(KEY, {});
  return data && typeof data === "object" ? data : {};
}

export function adminToken(programId: string): string | null {
  return held()[programId]?.token ?? null;
}

export function rememberAdmin(programId: string, token: string, name: string): void {
  const all = held();
  all[programId] = { token, name };
  writeJSON(KEY, all);
}

export function forgetAdmin(programId: string): void {
  const all = held();
  delete all[programId];
  writeJSON(KEY, all);
}

export function adminUrl(programId: string, token: string): string {
  return `${window.location.origin}/programs/${programId}/admin#token=${encodeURIComponent(token)}`;
}

/* ------------------------------------------------------------------ transport */

async function call(path: string, opts: { method?: string; body?: unknown; token?: string | null; signal?: AbortSignal } = {}) {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: opts.method ?? "GET",
      headers: {
        Accept: "application/json",
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(opts.token ? { "X-Program-Token": opts.token } : {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the Aletheia server. Check your connection and try again.");
  }
  if (!res.ok) {
    let message = "";
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") message = data.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message || (res.status >= 500 ? "The server ran into a problem. Try again in a moment." : `Request failed (${res.status}).`));
  }
  return res;
}

async function json<T>(path: string, opts?: Parameters<typeof call>[1]): Promise<T> {
  return (await (await call(path, opts)).json()) as T;
}

const pid = (id: string) => `/programs/${encodeURIComponent(id)}`;

export const programs = {
  create: (body: { name: string; institution: string; description: string }) =>
    json<{ id: string; admin_token: string }>("/programs", { method: "POST", body }),

  get: (id: string, signal?: AbortSignal) => json<ProgramInfo>(pid(id), { signal }),

  arc: (id: string, arcId: string, token?: string | null, signal?: AbortSignal) =>
    json<ProgramArcFull>(`${pid(id)}/arcs/${encodeURIComponent(arcId)}`, { token, signal }),

  update: (id: string, token: string, body: { name?: string; institution?: string; description?: string; settings?: Partial<ProgramSettings> }) =>
    json<Omit<ProgramInfo, "arcs">>(pid(id), { method: "PUT", body, token }),

  arcs: (id: string, token: string, signal?: AbortSignal) => json<ProgramArcBrief[]>(`${pid(id)}/arcs`, { token, signal }),

  addArc: (id: string, token: string, body: { state?: BuildState; build_id?: string; title?: string; summary?: string }) =>
    json<ProgramArcBrief>(`${pid(id)}/arcs`, { method: "POST", body, token }),

  updateArc: (id: string, token: string, arcId: string, body: Partial<Pick<ProgramArcBrief, "title" | "summary" | "published" | "order">> & { state?: BuildState }) =>
    json<ProgramArcBrief>(`${pid(id)}/arcs/${encodeURIComponent(arcId)}`, { method: "PUT", body, token }),

  deleteArc: (id: string, token: string, arcId: string) =>
    json<{ deleted: string }>(`${pid(id)}/arcs/${encodeURIComponent(arcId)}`, { method: "DELETE", token }),

  sections: (id: string, token: string, signal?: AbortSignal) => json<ProgramSection[]>(`${pid(id)}/sections`, { token, signal }),

  report: (id: string, token: string, term: string, signal?: AbortSignal) =>
    json<ZtcReport>(`${pid(id)}/report${term ? `?term=${encodeURIComponent(term)}` : ""}`, { token, signal }),

  reportCsv: async (id: string, token: string, term: string) => {
    const q = new URLSearchParams({ format: "csv", ...(term ? { term } : {}) });
    const res = await call(`${pid(id)}/report?${q.toString()}`, { token });
    const m = /filename="?([^";]+)"?/i.exec(res.headers.get("Content-Disposition") ?? "");
    return { blob: await res.blob(), filename: m ? m[1] : "ztc-report.csv" };
  },
};

export function money(n: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Strip what belongs to one instructor's copy before sharing a course with the program. */
export function shareableState(state: BuildState): BuildState {
  const copy = { ...state };
  delete copy.program;
  delete copy.section;
  return copy;
}
