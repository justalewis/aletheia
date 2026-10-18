/**
 * Pure helpers over the build state: turning a plan into state, keeping the instructor's
 * edits across a re-plan, and laying blank-course units over the term.
 */
import {
  briefCache,
  type AssignmentRole,
  type BuildState,
  type Plan,
  type PlanUnit,
  type StateAssignment,
  type StateReading,
  type StateUnit,
  type Term,
} from "../api";
import { normalizeSchedule } from "./schedule";

export function teachingWeeks(t: Term): number {
  return Math.max(t.weeks - t.fixed_start - t.fixed_end, 1);
}

export function planUnitToState(u: PlanUnit): StateUnit {
  return {
    key: u.key,
    title: u.title,
    start_week: u.start_week,
    end_week: u.end_week,
    weeks: u.weeks,
    goals: u.goals ?? [],
    outcomes: u.outcomes ?? [],
    readings: u.readings.map((r) => ({ id: r.id, selected: r.selected, role: r.role })),
    assignments: u.assignments.map((a) => ({ id: a.id, role: a.role, selected: a.selected })),
    summary: u.summary ?? "",
    notes: u.notes ?? "",
    topics: u.topics ?? [],
    merged_from: u.merged_from ?? [u.key],
  };
}

export function stateFromPlan(plan: Plan, focusFallback = ""): BuildState {
  return {
    title: plan.arc.title,
    instructor: "",
    institution: "",
    term_label: "",
    focus: plan.arc.focus || focusFallback,
    arc_id: plan.arc.id,
    term: plan.term,
    units: plan.units.map(planUnitToState),
    plan_note: { weeks: plan.term.weeks, dropped: plan.dropped, merged: plan.merged },
  };
}

let counter = 0;
export function newUnitKey(): string {
  counter += 1;
  return `unit-${Date.now().toString(36)}-${counter}`;
}

export function blankUnit(n: number, weeks: number): StateUnit {
  return {
    key: newUnitKey(),
    title: `Unit ${n}`,
    start_week: null,
    end_week: null,
    weeks: Math.max(1, weeks),
    goals: [],
    outcomes: [],
    readings: [],
    assignments: [],
    topics: [],
  };
}

export function blankState(term: Term, focus: string): BuildState {
  return relayout({
    title: "",
    instructor: "",
    institution: "",
    term_label: "",
    focus,
    arc_id: null,
    term,
    units: [blankUnit(1, teachingWeeks(term))],
    plan_note: null,
  });
}

/** Lay units end to end after the orientation weeks. */
export function relayout(s: BuildState): BuildState {
  let week = s.term.fixed_start + 1;
  const units = s.units.map((u) => {
    const weeks = Math.max(1, Math.round(u.weeks || 1));
    const start = week;
    week += weeks;
    return { ...u, weeks, start_week: start, end_week: start + weeks - 1 };
  });
  return { ...s, units };
}

/** Make sure a state loaded from storage or the server has every field the UI expects. */
export function normalizeState(raw: Partial<BuildState> | null | undefined): BuildState {
  const s = raw ?? {};
  const term: Term = {
    weeks: Number(s.term?.weeks) || 16,
    fixed_start: Number(s.term?.fixed_start ?? 1),
    fixed_end: Number(s.term?.fixed_end ?? 1),
    pages_per_week: Number(s.term?.pages_per_week) || 40,
  };
  return {
    title: s.title ?? "",
    instructor: s.instructor ?? "",
    institution: s.institution ?? "",
    term_label: s.term_label ?? "",
    focus: s.focus ?? "",
    arc_id: s.arc_id ?? null,
    term,
    plan_note: s.plan_note ?? null,
    program: s.program ?? null,
    section: s.section ?? null,
    schedule: normalizeSchedule(s.schedule),
    units: (s.units ?? []).map((u, i) => ({
      key: u.key || newUnitKey(),
      title: u.title ?? `Unit ${i + 1}`,
      start_week: u.start_week ?? null,
      end_week: u.end_week ?? null,
      weeks: Number(u.weeks) || 1,
      goals: u.goals ?? [],
      outcomes: u.outcomes ?? [],
      readings: (u.readings ?? []).filter((r) => r && r.id),
      assignments: (u.assignments ?? []).filter((a) => a && a.id).map((a) => ({ ...a, role: a.role ?? "activity" })),
      summary: u.summary ?? "",
      notes: u.notes ?? "",
      topics: u.topics ?? [],
      merged_from: u.merged_from,
      removed: u.removed,
      renamed: u.renamed,
      reordered: u.reordered,
    })),
  };
}

/* ------------------------------------------------------------------ edits and re-planning */

export function unitEdited(u: StateUnit): boolean {
  return Boolean(
    u.renamed ||
      u.reordered ||
      (u.removed && u.removed.length) ||
      u.readings.some((r) => r.manual || r.added) ||
      u.assignments.some((a) => a.manual || a.added),
  );
}

export function anyEdits(s: BuildState): boolean {
  return s.units.some(unitEdited);
}

function sourcesOf(u: { key: string; merged_from?: string[] }): string[] {
  return u.merged_from && u.merged_from.length ? u.merged_from : u.key.split("+");
}

type Item = { id: string; selected: boolean; manual?: boolean; added?: boolean };

/** Keep the old unit's order and hand edits; take the plan's selection for untouched items. */
function mergeItems<T extends Item>(oldItems: T[], fresh: T[], removed: string[]): T[] {
  const byId = new Map(fresh.map((i) => [i.id, i]));
  const out: T[] = [];
  const seen = new Set<string>();
  for (const o of oldItems) {
    if (seen.has(o.id)) continue;
    if (o.added) {
      out.push(o);
      seen.add(o.id);
      continue;
    }
    const n = byId.get(o.id);
    if (!n) continue;
    out.push(o.manual ? { ...n, selected: o.selected, manual: true } : n);
    seen.add(o.id);
  }
  for (const n of fresh) if (!seen.has(n.id) && !removed.includes(n.id)) out.push(n);
  return out;
}

/** Lay a unit's hand edits over a new unit that absorbed it (or part of it). */
function overlay<T extends Item>(items: T[], source: T[], claimed: Set<string>, sourceKey: string): T[] {
  const out = [...items];
  for (const s of source) {
    const at = out.findIndex((i) => i.id === s.id);
    if (s.added) {
      const claim = `${sourceKey}:${s.id}`;
      if (!claimed.has(claim) && at < 0) {
        out.push(s);
        claimed.add(claim);
      }
    } else if (s.manual && at >= 0) {
      out[at] = { ...out[at], selected: s.selected, manual: true };
    }
  }
  return out;
}

/**
 * Apply a new plan (after a term change) while keeping the instructor's edits wherever the
 * unit still exists, including units that were merged or split. Returns the edited units
 * that the new term drops entirely, so the caller can confirm before discarding them.
 */
export function mergePlan(old: BuildState, plan: Plan): { state: BuildState; lost: StateUnit[] } {
  const edited = old.units.filter(unitEdited);
  const claimed = new Set<string>();
  const units = plan.units.map((pu) => {
    const fresh = planUnitToState(pu);
    const keys = new Set(sourcesOf(fresh));
    const sources = edited.filter((o) => sourcesOf(o).some((k) => keys.has(k)));
    if (!sources.length) return fresh;
    const exact = sources.find((o) => o.key === fresh.key);
    const removed = [...new Set(sources.flatMap((o) => o.removed ?? []))];
    let readings: StateReading[] = exact
      ? mergeItems(exact.readings, fresh.readings, removed)
      : fresh.readings.filter((r) => !removed.includes(r.id));
    let assignments: StateAssignment[] = exact
      ? mergeItems(exact.assignments, fresh.assignments, removed)
      : fresh.assignments.filter((a) => !removed.includes(a.id));
    if (exact) {
      exact.readings.filter((r) => r.added).forEach((r) => claimed.add(`${exact.key}:${r.id}`));
      exact.assignments.filter((a) => a.added).forEach((a) => claimed.add(`${exact.key}:${a.id}`));
    }
    for (const o of sources) {
      if (o === exact) continue;
      readings = overlay(readings, o.readings, claimed, o.key);
      assignments = overlay(assignments, o.assignments, claimed, o.key);
    }
    return {
      ...fresh,
      title: exact?.renamed ? exact.title : fresh.title,
      renamed: exact?.renamed,
      reordered: exact?.reordered,
      removed: removed.length ? removed : undefined,
      readings,
      assignments,
    };
  });
  const covered = new Set(plan.units.flatMap((pu) => sourcesOf(pu)));
  const lost = edited.filter((o) => !sourcesOf(o).some((k) => covered.has(k)));
  return {
    state: {
      ...old,
      term: plan.term,
      units,
      plan_note: { weeks: plan.term.weeks, dropped: plan.dropped, merged: plan.merged },
    },
    lost,
  };
}

/* ------------------------------------------------------------------ unit edits */

export function mapUnit(s: BuildState, key: string, fn: (u: StateUnit) => StateUnit): BuildState {
  return { ...s, units: s.units.map((u) => (u.key === key ? fn(u) : u)) };
}

export function toggleReading(u: StateUnit, id: string): StateUnit {
  return { ...u, readings: u.readings.map((r) => (r.id === id ? { ...r, selected: !r.selected, manual: true } : r)) };
}

export function toggleAssignment(u: StateUnit, id: string): StateUnit {
  return { ...u, assignments: u.assignments.map((a) => (a.id === id ? { ...a, selected: !a.selected, manual: true } : a)) };
}

export function moveReading(u: StateUnit, index: number, dir: -1 | 1): StateUnit {
  const j = index + dir;
  if (j < 0 || j >= u.readings.length) return u;
  const readings = [...u.readings];
  [readings[index], readings[j]] = [readings[j], readings[index]];
  return { ...u, readings, reordered: true };
}

export function removeReading(u: StateUnit, id: string): StateUnit {
  const item = u.readings.find((r) => r.id === id);
  const removed = item && !item.added ? [...new Set([...(u.removed ?? []), id])] : u.removed;
  return { ...u, readings: u.readings.filter((r) => r.id !== id), removed };
}

export function removeAssignment(u: StateUnit, id: string): StateUnit {
  const item = u.assignments.find((a) => a.id === id);
  const removed = item && !item.added ? [...new Set([...(u.removed ?? []), id])] : u.removed;
  return { ...u, assignments: u.assignments.filter((a) => a.id !== id), removed };
}

export function addReading(u: StateUnit, id: string): StateUnit {
  if (u.readings.some((r) => r.id === id)) return u;
  return {
    ...u,
    readings: [...u.readings, { id, selected: true, role: "added", added: true }],
    removed: u.removed?.filter((x) => x !== id),
  };
}

export function addAssignment(u: StateUnit, id: string, role: AssignmentRole): StateUnit {
  if (u.assignments.some((a) => a.id === id)) return u;
  return {
    ...u,
    assignments: [...u.assignments, { id, role, selected: true, added: true }],
    removed: u.removed?.filter((x) => x !== id),
  };
}

/* ------------------------------------------------------------------ totals */

export function readingPages(id: string): number {
  return briefCache.readings.get(id)?.est_pages ?? 0;
}

export function unitPages(u: StateUnit): number {
  return u.readings.filter((r) => r.selected).reduce((sum, r) => sum + readingPages(r.id), 0);
}

export function unitBudget(u: StateUnit, t: Term): number {
  return Math.max(1, u.weeks) * t.pages_per_week;
}

export function weeksLabel(u: { start_week: number | null; end_week: number | null; weeks: number }): string {
  if (!u.start_week) return `${u.weeks} ${u.weeks === 1 ? "week" : "weeks"}`;
  if (u.start_week === u.end_week) return `Week ${u.start_week}`;
  return `Weeks ${u.start_week}–${u.end_week}`;
}

export function selectedIds(s: BuildState): { readings: string[]; assignments: string[] } {
  return {
    readings: s.units.flatMap((u) => u.readings.filter((r) => r.selected).map((r) => r.id)),
    assignments: s.units.flatMap((u) => u.assignments.filter((a) => a.selected).map((a) => a.id)),
  };
}

export function allIds(s: BuildState): { readings: string[]; assignments: string[] } {
  return {
    readings: s.units.flatMap((u) => u.readings.map((r) => r.id)),
    assignments: s.units.flatMap((u) => u.assignments.map((a) => a.id)),
  };
}
