/**
 * Typed client for the Aletheia JSON API (Django + django-ninja).
 * Shapes mirror backend/catalog/serializers.py and backend/builder/api.py.
 */
import type { CourseSchedule } from "./builder/schedule";

export type Tier = "adapt" | "adapt-nc" | "verbatim" | "link-only";
export const TIERS: Tier[] = ["adapt", "adapt-nc", "verbatim", "link-only"];

export interface Focus {
  code: string;
  label: string;
  frameworks?: string[];
  tag_only?: boolean;
}

export interface TermPreset {
  id: string;
  label: string;
  weeks: number;
  fixed_start: number;
  fixed_end: number;
  pages_per_week: number;
}

export interface PackInfo {
  id: string;
  name: string;
  version: string;
  config: {
    focuses?: Focus[];
    reuse_tiers?: Partial<Record<Tier, { label: string; licenses: string[] }>>;
    term_presets?: TermPreset[];
    [key: string]: unknown;
  };
  counts: { readings: number; assignments: number; arcs: number; outcome_sets: number };
}

export interface ReadingBrief {
  id: string;
  level: string;
  title: string;
  authors: string;
  parent_title: string;
  year: string;
  license: string;
  reuse_tier: Tier;
  license_verified: boolean;
  skins: string[];
  topics: string[];
  audience: string;
  url: string;
  pdf_url: string;
  pages: number | null;
  est_pages: number;
  summary: string;
}

export interface ReadingFull extends ReadingBrief {
  parent_id: string | null;
  publisher_platform: string;
  landing_url: string;
  html_url: string;
  epub_url: string;
  license_url: string;
  license_family: string;
  declared_tier: Tier;
  course_level: string;
  notes: string;
  children: ReadingBrief[];
}

export interface AssignmentBrief {
  id: string;
  title: string;
  authors: string;
  source_repository: string;
  year: string;
  kind: string;
  assignment_type: string;
  license: string;
  reuse_tier: Tier;
  license_verified: boolean;
  skins: string[];
  topics: string[];
  url: string;
  estimated_weeks: string;
  summary: string;
}

export interface AssignmentFull extends AssignmentBrief {
  file_url: string;
  license_url: string;
  notes: string;
  pairs_with_readings: string[];
  course_level: string;
}

export interface Paged<T> {
  total: number;
  items: T[];
}

export interface TopicCount {
  topic: string;
  count: number;
}

export interface ArcBrief {
  id: string;
  focus: string;
  title: string;
  tagline: string;
  course_level: string;
  frameworks: string[];
  fits_courses: string[];
  unit_count: number;
  ideal_weeks: number;
}

export interface ArcUnit {
  key: string;
  title: string;
  summary: string;
  goals: string[];
  outcomes: string[];
  topics: string[];
  min_weeks: number;
  ideal_weeks: number;
  priority: "core" | "optional" | string;
  merge_with: string;
  notes: string;
  readings: { core?: string[]; optional?: string[] };
  assignments: { major?: string[]; short_term?: string[]; activity?: string[] };
}

export interface ArcFull extends ArcBrief {
  rationale: string;
  units: ArcUnit[];
  readings: Record<string, ReadingBrief>;
  assignments: Record<string, AssignmentBrief>;
}

export interface OutcomeSetBrief {
  id: string;
  kind: string;
  institution: string;
  course: string;
  title: string;
  count: number;
}

export interface Term {
  weeks: number;
  fixed_start: number;
  fixed_end: number;
  pages_per_week: number;
}

export interface AutoBuildQuery extends Partial<Term> {
  theme?: string;
  outcomes_text?: string;
  outcome_set_ids?: string[];
  course_name?: string;
  focus?: string;
  /** Use this sample course instead of choosing one. */
  arc_id?: string;
  weeks?: number;
}

export interface AutoBuildOutcome {
  id: string;
  set_id: string;
  label: string;
  text: string;
  status: "ok" | "weak" | "none";
  had: number;
  added: { id: string; title: string; type: "reading" | "assignment"; unit: string }[];
  matched_topics: string[];
  unmatched: boolean;
}

export interface AutoBuildResult {
  state: BuildState;
  arc: { id: string; title: string; focus: string; tagline: string };
  alternatives: { id: string; title: string; focus: string }[];
  why: string[];
  theme_added: { id: string; title: string; unit: string }[];
  outcomes: AutoBuildOutcome[];
}

export type AssignmentRole = "major" | "short_term" | "activity";
export type ReadingRole = "core" | "optional";

export interface PlanWeek {
  n: number;
  kind: "fixed" | "unit";
  label?: string;
  unit?: string;
}

export interface PlanUnit {
  key: string;
  title: string;
  merged_from: string[];
  start_week: number;
  end_week: number;
  weeks: number;
  goals: string[];
  outcomes: string[];
  readings: { id: string; role: ReadingRole; selected: boolean; pages: number }[];
  assignments: { id: string; role: AssignmentRole; selected: boolean }[];
  pages_selected: number;
  page_budget: number;
  heavy: boolean;
  summary?: string;
  notes?: string;
  topics?: string[];
}

export interface Plan {
  term: Term;
  weeks: PlanWeek[];
  units: PlanUnit[];
  dropped: string[];
  merged: string[][];
  arc: { id: string; title: string; focus: string; rationale: string };
  readings: Record<string, ReadingBrief>;
  assignments: Record<string, AssignmentBrief>;
}

export interface LicenseReport {
  total: number;
  tiers: Partial<Record<Tier, number>>;
  families: Record<string, number>;
  noncommercial: number;
  noderivatives: number;
  sharealike: number;
  unverified: string[];
  statements: string[];
}

/* ------------------------------------------------------------------ build state */

/** A reading in a unit. The backend reads only id and selected; the rest is UI bookkeeping. */
export interface StateReading {
  id: string;
  selected: boolean;
  role?: ReadingRole | "added";
  /** The instructor checked or unchecked this item by hand. */
  manual?: boolean;
  /** The instructor added this item from the catalog. */
  added?: boolean;
}

export interface StateAssignment {
  id: string;
  role: AssignmentRole;
  selected: boolean;
  manual?: boolean;
  added?: boolean;
}

export interface StateUnit {
  key: string;
  title: string;
  start_week: number | null;
  end_week: number | null;
  weeks: number;
  goals: string[];
  outcomes: string[];
  readings: StateReading[];
  assignments: StateAssignment[];
  summary?: string;
  notes?: string;
  topics?: string[];
  merged_from?: string[];
  /** Arc items the instructor removed, so a re-plan doesn't bring them back. */
  removed?: string[];
  renamed?: boolean;
  reordered?: boolean;
}

export interface PlanNote {
  weeks: number;
  dropped: string[];
  merged: string[][];
}

export interface BuildState {
  title: string;
  instructor: string;
  institution: string;
  term_label: string;
  focus: string;
  arc_id: string | null;
  term: Term;
  units: StateUnit[];
  /** UI only: which arc units the planner dropped or merged for this term. */
  plan_note?: PlanNote | null;
  /** Set when the course belongs to a writing program (see src/programs). */
  program?: ProgramLink | null;
  section?: SectionInfo | null;
  /** Class dates (start date, meeting days, no-class days); see builder/schedule.ts. */
  schedule?: CourseSchedule | null;
  /** Outcome sets the coverage view checks this course against. */
  outcome_set_ids?: string[];
}

export interface ProgramLink {
  id: string;
  name: string;
  /** The program course this one started from. */
  course?: string | null;
  /** "section": an instructor's course, linked on save. "template": a WPA drafting a program course. */
  mode?: "section" | "template";
}

export interface SectionInfo {
  course_code: string;
  section: string;
  term: string;
  enrollment: number | null;
  instructor?: string;
}

/** Program link and section info travel beside the state so the server can report on them. */
function programFields(state: BuildState) {
  const p = state.program;
  if (!p || p.mode === "template") return { program_id: null, section: null };
  return { program_id: p.id, section: { ...state.section, instructor: state.section?.instructor || state.instructor } };
}

/* ------------------------------------------------------------------ outcomes (backend/catalog/outcomes_api.py) */

export interface OutcomeSetInfo extends OutcomeSetBrief {
  source_url: string;
}

/** A catalog item matched to an outcome, with its score and a one-line reason. */
interface MatchFields {
  score: number;
  why: string;
}
export type ReadingMatch = ReadingBrief & MatchFields & { item_type: "reading" };
export type AssignmentMatch = AssignmentBrief & MatchFields & { item_type: "assignment" };
export type ItemMatch = ReadingMatch | AssignmentMatch;

export interface OutcomeInfo {
  id: string;
  set_id: string;
  label: string;
  text: string;
  codes: string[];
  topics: string[];
}

export interface RecommendQuery {
  outcome_set_id?: string;
  outcome_ids?: string[];
  focus?: string;
  tiers?: string[];
  levels?: string[];
  per_outcome?: number;
  exclude_ids?: string[];
}

export interface OutcomeRecommendation extends OutcomeInfo {
  readings: ReadingMatch[];
  assignments: AssignmentMatch[];
  total: { readings: number; assignments: number };
}

export interface Recommendations {
  outcome_set: OutcomeSetInfo | null;
  unknown_ids: string[];
  outcomes: OutcomeRecommendation[];
}

export type CoverageStatus = "ok" | "weak" | "none";

export interface CoverageCell {
  unit_key: string;
  count: number;
  reading_ids: string[];
  assignment_ids: string[];
}

export interface CoverageRow extends OutcomeInfo {
  cells: CoverageCell[];
  total: number;
  strength: number;
  status: CoverageStatus;
}

export interface CoverageGap {
  outcome_id: string;
  set_id: string;
  label: string;
  status: "weak" | "none";
  total: number;
  unit_key: string | null;
  unit_title: string | null;
  suggestions: ItemMatch[];
}

export interface Coverage {
  units: { key: string; title: string }[];
  sets: OutcomeSetInfo[];
  unknown_set_ids: string[];
  outcomes: CoverageRow[];
  gaps: CoverageGap[];
}

export interface SavedBuild {
  id: string;
  title: string;
  state: BuildState;
  updated: string;
  forked_from: string | null;
}

/* ------------------------------------------------------------------ transport */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = "";
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") message = data.detail;
    else if (Array.isArray(data?.detail)) message = data.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    /* not JSON */
  }
  if (!message) {
    message =
      res.status === 404
        ? "We couldn't find that."
        : res.status >= 500
          ? "The server ran into a problem. Try again in a moment."
          : `Request failed (${res.status}).`;
  }
  return new ApiError(res.status, message);
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

async function send(path: string, opts: RequestOptions = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: opts.method ?? "GET",
      headers: {
        Accept: "application/json",
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...opts.headers,
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the Aletheia server. Check your connection and try again.");
  }
  if (!res.ok) throw await errorFrom(res);
  return res;
}

async function json<T>(path: string, opts?: RequestOptions): Promise<T> {
  const res = await send(path, opts);
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/* ------------------------------------------------------------------ brief cache */

/** Briefs seen anywhere in the session, so the builder can show titles without refetching. */
export const briefCache = {
  readings: new Map<string, ReadingBrief>(),
  assignments: new Map<string, AssignmentBrief>(),
};

function remember(readings?: Record<string, ReadingBrief> | ReadingBrief[], assignments?: Record<string, AssignmentBrief> | AssignmentBrief[]) {
  const rs = Array.isArray(readings) ? readings : Object.values(readings ?? {});
  const as = Array.isArray(assignments) ? assignments : Object.values(assignments ?? {});
  rs.forEach((r) => briefCache.readings.set(r.id, r));
  as.forEach((a) => briefCache.assignments.set(a.id, a));
}

/* ------------------------------------------------------------------ endpoints */

export interface ReadingQuery {
  focus?: string;
  topic?: string;
  tier?: string;
  level?: string;
  q?: string;
  parent?: string;
  limit?: number;
  offset?: number;
}

export interface AssignmentQuery {
  focus?: string;
  topic?: string;
  tier?: string;
  kind?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  pack: (signal?: AbortSignal) => json<PackInfo>("/pack", { signal }),

  readings: async (query: ReadingQuery, signal?: AbortSignal) => {
    const page = await json<Paged<ReadingBrief>>(`/readings${qs({ ...query })}`, { signal });
    remember(page.items);
    return page;
  },

  reading: async (id: string, signal?: AbortSignal) => {
    const r = await json<ReadingFull>(`/readings/${encodeURIComponent(id)}`, { signal });
    remember([r, ...r.children]);
    return r;
  },

  topics: (focus: string, kind: "readings" | "assignments", signal?: AbortSignal) =>
    json<TopicCount[]>(`/topics${qs({ focus, kind })}`, { signal }),

  assignments: async (query: AssignmentQuery, signal?: AbortSignal) => {
    const page = await json<Paged<AssignmentBrief>>(`/assignments${qs({ ...query })}`, { signal });
    remember(undefined, page.items);
    return page;
  },

  assignment: async (id: string, signal?: AbortSignal) => {
    const a = await json<AssignmentFull>(`/assignments/${encodeURIComponent(id)}`, { signal });
    remember(undefined, [a]);
    return a;
  },

  arcs: (focus: string, signal?: AbortSignal) => json<ArcBrief[]>(`/arcs${qs({ focus })}`, { signal }),

  arc: async (id: string, signal?: AbortSignal) => {
    const arc = await json<ArcFull>(`/arcs/${encodeURIComponent(id)}`, { signal });
    remember(arc.readings, arc.assignments);
    return arc;
  },

  outcomeSets: (signal?: AbortSignal) => json<OutcomeSetBrief[]>("/outcome-sets", { signal }),

  plan: async (arc_id: string, term: Term, signal?: AbortSignal) => {
    const plan = await json<Plan>("/plan", { method: "POST", body: { arc_id, ...term }, signal });
    remember(plan.readings, plan.assignments);
    return plan;
  },

  sendFeedback: (body: { name: string; email: string; message: string; page: string; website: string }) =>
    json<{ ok: boolean }>("/feedback", { method: "POST", body }),

  autoBuild: (body: AutoBuildQuery, signal?: AbortSignal) =>
    json<AutoBuildResult>("/auto-build", { method: "POST", body, signal }),

  licenseReport: (state: BuildState, signal?: AbortSignal) =>
    json<LicenseReport>("/license-report", { method: "POST", body: { state }, signal }),

  recommend: async (query: RecommendQuery, signal?: AbortSignal) => {
    const r = await json<Recommendations>("/recommend", { method: "POST", body: query, signal });
    r.outcomes.forEach((o) => remember(o.readings, o.assignments));
    return r;
  },

  coverage: async (state: BuildState, outcome_set_ids: string[], signal?: AbortSignal) => {
    const c = await json<Coverage>("/coverage", { method: "POST", body: { state, outcome_set_ids }, signal });
    const items = c.gaps.flatMap((g) => g.suggestions);
    remember(
      items.filter((i): i is ReadingMatch => i.item_type === "reading"),
      items.filter((i): i is AssignmentMatch => i.item_type === "assignment"),
    );
    return c;
  },

  createBuild: (state: BuildState, forked_from?: string) =>
    json<{ id: string; edit_token: string }>("/builds", {
      method: "POST",
      body: forked_from ? { state, forked_from, ...programFields(state) } : { state, ...programFields(state) },
    }),

  getBuild: (id: string, signal?: AbortSignal) => json<SavedBuild>(`/builds/${encodeURIComponent(id)}`, { signal }),

  updateBuild: (id: string, state: BuildState, token: string) =>
    json<{ id: string; updated: string }>(`/builds/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: { state, ...programFields(state) },
      headers: { "X-Edit-Token": token },
    }),

  /** POST the state to an export endpoint and return the file. */
  exportFile: async (
    kind: "html" | "pdf" | "pdf-print" | "imscc-canvas" | "imscc-standard" | "ics" | "docx",
    state: BuildState,
  ) => {
    const path =
      kind === "html" || kind === "ics" || kind === "docx"
        ? `/export/${kind}`
        : kind === "pdf" || kind === "pdf-print"
          ? `/export/pdf?variant=${kind === "pdf" ? "accessible" : "print"}`
          : `/export/imscc?flavor=${kind === "imscc-standard" ? "standard" : "canvas"}`;
    const res = await send(path, { method: "POST", body: { state }, headers: { Accept: "*/*" } });
    const blob = await res.blob();
    return { blob, filename: filenameFrom(res.headers.get("Content-Disposition")) ?? fallbackName(kind) };
  },
};

function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (star) return decodeURIComponent(star[1]);
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1] : null;
}

function fallbackName(kind: string): string {
  if (kind === "html") return "course-reader.html";
  if (kind === "pdf") return "course-reader.pdf";
  if (kind === "ics") return "course-calendar.ics";
  if (kind === "docx") return "course-syllabus.docx";
  if (kind === "pdf-print") return "course-reader-print.pdf";
  return kind === "imscc-standard" ? "course-reader-standard.imscc" : "course-reader.imscc";
}

/** Fetch briefs for ids the cache doesn't have yet, a few at a time. Missing ids resolve to null. */
export async function ensureBriefs(readingIds: string[], assignmentIds: string[], signal?: AbortSignal): Promise<void> {
  const tasks: (() => Promise<unknown>)[] = [];
  for (const id of new Set(readingIds)) {
    if (!briefCache.readings.has(id)) tasks.push(() => api.reading(id, signal).catch(() => null));
  }
  for (const id of new Set(assignmentIds)) {
    if (!briefCache.assignments.has(id)) tasks.push(() => api.assignment(id, signal).catch(() => null));
  }
  const queue = [...tasks];
  const workers = Array.from({ length: Math.min(6, queue.length) }, async () => {
    while (queue.length) {
      if (signal?.aborted) return;
      const next = queue.shift();
      if (next) await next();
    }
  });
  await Promise.all(workers);
}

export function rememberBriefs(readings?: ReadingBrief[], assignments?: AssignmentBrief[]) {
  remember(readings, assignments);
}
