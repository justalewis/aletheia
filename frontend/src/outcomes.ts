/**
 * Helpers shared by the Outcomes page and the builder's coverage panel: outcome set labels,
 * putting a matched item into a unit, and adding to the course draft kept in this browser.
 */
import type {
  AssignmentBrief,
  BuildState,
  ItemMatch,
  OutcomeSetBrief,
  ReadingBrief,
  StateUnit,
  Term,
} from "./api";
import { roleForKind } from "./components/meta";
import { addAssignment, addReading, blankState, mapUnit, normalizeState } from "./builder/model";
import { loadDraft, saveDraft, type Draft } from "./storage";

const SHORT_NAMES: Record<string, string> = {
  "wpa-os-3.0": "WPA Outcomes Statement 3.0",
  "acrl-framework": "ACRL Framework for Information Literacy",
  "aletheia-tc": "Technical communication categories",
};

export function setLabel(s: OutcomeSetBrief): string {
  if (s.kind === "framework") return SHORT_NAMES[s.id] ?? s.title;
  return [s.institution, s.course].filter(Boolean).join(" ") || s.title;
}

/** Strip the match fields so a brief can go into the draft's brief cache. */
export function toBrief(item: ItemMatch): ReadingBrief | AssignmentBrief {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { item_type, score, why, ...rest } = item;
  return rest as ReadingBrief | AssignmentBrief;
}

/** Put an item in a unit as a checked item. If it's already there, check it. */
export function putInUnit(u: StateUnit, item: ItemMatch): StateUnit {
  if (item.item_type === "reading") {
    if (u.readings.some((r) => r.id === item.id)) {
      return { ...u, readings: u.readings.map((r) => (r.id === item.id ? { ...r, selected: true, manual: true } : r)) };
    }
    return addReading(u, item.id);
  }
  if (u.assignments.some((a) => a.id === item.id)) {
    return { ...u, assignments: u.assignments.map((a) => (a.id === item.id ? { ...a, selected: true, manual: true } : a)) };
  }
  return addAssignment(u, item.id, roleForKind(item.kind));
}

export function isChecked(u: StateUnit, item: ItemMatch): boolean {
  const list = item.item_type === "reading" ? u.readings : u.assignments;
  return list.some((x) => x.id === item.id && x.selected);
}

export function addToUnit(s: BuildState, unitKey: string, item: ItemMatch): BuildState {
  return mapUnit(s, unitKey, (u) => putInUnit(u, item));
}

/* ------------------------------------------------------------------ the course draft */

export const OUTCOMES_UNIT_TITLE = "Outcomes";

export function currentDraft(): Draft | null {
  return loadDraft(null);
}

/** Where the builder picks this draft back up (it restores a draft whose arc matches the URL). */
export function draftLink(d: Draft | null): string {
  const arc = d?.state.arc_id;
  return arc ? `/build/new?arc=${encodeURIComponent(arc)}` : "/build/new";
}

/**
 * Add an item to the unsaved course draft in this browser. With no draft, start a blank course
 * with one unit called "Outcomes". Returns the unit it went into and whether it was already checked.
 */
export function addToDraft(
  item: ItemMatch,
  opts: { unitKey?: string; term: Term; focus: string; outcomeSetId?: string },
): { draft: Draft; unitKey: string; unitTitle: string; already: boolean; created: boolean } {
  const existing = currentDraft();
  let state: BuildState = existing ? normalizeState(existing.state) : blankState(opts.term, opts.focus);
  if (!existing) state = { ...state, units: state.units.map((u) => ({ ...u, title: OUTCOMES_UNIT_TITLE })) };
  if (!state.units.length) {
    const fresh = blankState(state.term, state.focus).units[0];
    state = { ...state, units: [{ ...fresh, title: OUTCOMES_UNIT_TITLE }] };
  }
  const unit = state.units.find((u) => u.key === opts.unitKey) ?? state.units[0];
  const already = isChecked(unit, item);
  state = addToUnit(state, unit.key, item);
  if (opts.outcomeSetId && !(state.outcome_set_ids ?? []).includes(opts.outcomeSetId)) {
    state = { ...state, outcome_set_ids: [...(state.outcome_set_ids ?? []), opts.outcomeSetId] };
  }
  const brief = toBrief(item);
  const readings = (existing?.readings ?? []).filter((r) => r.id !== item.id);
  const assignments = (existing?.assignments ?? []).filter((a) => a.id !== item.id);
  const draft: Draft = {
    state,
    savedAt: new Date().toISOString(),
    readings: item.item_type === "reading" ? [...readings, brief as ReadingBrief] : readings,
    assignments: item.item_type === "assignment" ? [...assignments, brief as AssignmentBrief] : assignments,
  };
  saveDraft(null, draft);
  return { draft, unitKey: unit.key, unitTitle: unit.title, already, created: !existing };
}
