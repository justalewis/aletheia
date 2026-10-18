/**
 * Class dates for a build: the optional `schedule` key in the build state, and a hook that asks
 * the server to date the course (POST /api/schedule). The date rules live in one place,
 * backend/builder/schedule.py; this file only holds the shapes, defaults, and formatting.
 */
import { useEffect, useRef, useState } from "react";
import type { BuildState } from "../api";

export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
export type Day = (typeof DAYS)[number];
export const DAY_NAMES: Record<Day, string> = {
  Mon: "Monday",
  Tue: "Tuesday",
  Wed: "Wednesday",
  Thu: "Thursday",
  Fri: "Friday",
  Sat: "Saturday",
  Sun: "Sunday",
};

export interface NoClassDay {
  date: string;
  label?: string;
}

export interface NoClassRange {
  start: string;
  end: string;
  label?: string;
}

export type NoClassEntry = NoClassDay | NoClassRange;

export interface CourseSchedule {
  /** ISO date of the first day of the term; week 1 is the week that holds it. */
  start_date: string;
  meeting_days: Day[];
  no_class: NoClassEntry[];
  timezone: string;
  /** "HH:MM", optional; with both set, calendar meetings are timed instead of all-day. */
  start_time?: string;
  end_time?: string;
  /** Assignment id -> ISO date, overriding the suggested due date. */
  due_dates?: Record<string, string>;
}

export interface ScheduleMeeting {
  date: string;
  day: Day;
  label: string;
}

export interface ScheduleWeek {
  n: number;
  start: string;
  end: string;
  label: string;
  meetings: ScheduleMeeting[];
  cancelled: { date: string; day: Day; label: string }[];
  unit: string | null;
  fixed: string | null;
}

export interface ScheduleBreak {
  start: string;
  end: string;
  label: string;
  after_week: number;
  dates: string;
}

export interface UnitDates {
  start: string;
  end: string;
  label: string;
  first_meeting: string | null;
  last_meeting: string | null;
  meetings: string[];
  breaks: string[];
  due: Record<string, string>;
}

export interface DueDate {
  unit: string;
  id: string;
  role: string;
  date: string;
  suggested: string;
  overridden: boolean;
  label: string;
}

export interface ScheduleResult {
  ok: boolean;
  warnings: string[];
  notes: string[];
  start_date?: string;
  timezone?: string;
  meeting_days?: Day[];
  start_time?: string | null;
  end_time?: string | null;
  term_start?: string;
  term_end?: string;
  label?: string;
  weeks?: ScheduleWeek[];
  breaks?: ScheduleBreak[];
  meetings?: (ScheduleMeeting & { n: number; week: number })[];
  units?: Record<string, UnitDates>;
  due?: DueDate[];
}

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Every IANA zone the browser knows, with the given ones first if missing. */
export function timezoneOptions(...keep: string[]): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  let zones: string[] = [];
  try {
    zones = intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    zones = [];
  }
  const extra = [...keep, "UTC"].filter((z) => z && !zones.includes(z));
  return [...extra, ...zones];
}

export function emptySchedule(): CourseSchedule {
  return { start_date: "", meeting_days: [], no_class: [], timezone: browserTimezone() };
}

export function isRange(e: NoClassEntry): e is NoClassRange {
  return "start" in e || "end" in e;
}

/** Make a schedule loaded from storage or the server safe to edit. Null when there isn't one. */
export function normalizeSchedule(raw: unknown): CourseSchedule | null {
  if (!raw || typeof raw !== "object") return null;
  const s = raw as Omit<Partial<CourseSchedule>, "no_class"> & { no_class?: unknown };
  const days = Array.isArray(s.meeting_days) ? s.meeting_days.filter((d): d is Day => DAYS.includes(d as Day)) : [];
  const noClass: NoClassEntry[] = (Array.isArray(s.no_class) ? s.no_class : []).flatMap((e): NoClassEntry[] => {
    if (typeof e === "string") return [{ date: e, label: "" }];
    if (!e || typeof e !== "object") return [];
    const o = e as Record<string, unknown>;
    const label = typeof o.label === "string" ? o.label : "";
    if ("start" in o || "end" in o) return [{ start: String(o.start ?? ""), end: String(o.end ?? ""), label }];
    return [{ date: String(o.date ?? ""), label }];
  });
  const out: CourseSchedule = {
    start_date: typeof s.start_date === "string" ? s.start_date : "",
    meeting_days: DAYS.filter((d) => days.includes(d)),
    no_class: noClass,
    timezone: typeof s.timezone === "string" && s.timezone ? s.timezone : browserTimezone(),
  };
  if (s.start_time) out.start_time = String(s.start_time);
  if (s.end_time) out.end_time = String(s.end_time);
  if (s.due_dates && typeof s.due_dates === "object") out.due_dates = { ...s.due_dates };
  return out;
}

/** "Wed, Jan 27" for an ISO date, in the reader's locale. */
export function formatDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function previewBody(state: BuildState) {
  return {
    term: state.term,
    schedule: state.schedule,
    units: state.units.map((u) => ({
      key: u.key,
      start_week: u.start_week,
      end_week: u.end_week,
      assignments: u.assignments.map((a) => ({ id: a.id, role: a.role, selected: a.selected })),
    })),
  };
}

/**
 * The server's dates for the course, refreshed (debounced) when the schedule, term, or unit
 * layout changes. Null when there's no start date. Keeps the last answer while a new one loads.
 */
export function useScheduleDates(state: BuildState | null): ScheduleResult | null {
  const [result, setResult] = useState<ScheduleResult | null>(null);
  const active = Boolean(state?.schedule?.start_date);
  const key = active && state ? JSON.stringify(previewBody(state)) : "";
  const keyRef = useRef(key);
  keyRef.current = key;

  useEffect(() => {
    if (!key) {
      setResult(null);
      return;
    }
    const ctrl = new AbortController();
    const t = window.setTimeout(async () => {
      try {
        const res = await fetch("/api/schedule", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ state: JSON.parse(key) }),
          signal: ctrl.signal,
        });
        if (!res.ok) throw new Error(`Request failed (${res.status}).`);
        const data = (await res.json()) as ScheduleResult;
        if (keyRef.current === key) setResult(data);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setResult({ ok: false, notes: [], warnings: ["Aletheia couldn't work out the dates just now. Try again in a moment."] });
      }
    }, 300);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
  }, [key]);

  return active ? result : null;
}
