/**
 * Starting a course inside a program. The builder's /build/new route reads:
 *   ?program=<id>&course=<program course id>   an instructor's copy of a program course
 *   ?program=<id>                              an instructor's own course, linked to the program
 *   ...&as=template                            a coordinator drafting a course for the program
 */
import { api, ensureBriefs, type ArcFull, type BuildState, type ProgramLink, type SectionInfo } from "../api";
import { allIds, normalizeState } from "../builder/model";
import { adminToken, programs } from "./client";

export interface ProgramStart {
  link: ProgramLink;
  /** The program course to start from, or null to start the usual way and attach the link. */
  state: BuildState | null;
  arc: ArcFull | null;
}

export function emptySection(): SectionInfo {
  return { course_code: "", section: "", term: "", enrollment: null };
}

export async function programStart(search: URLSearchParams, signal: AbortSignal): Promise<ProgramStart | null> {
  const id = search.get("program");
  if (!id) return null;
  const mode = search.get("as") === "template" ? "template" : "section";
  const course = search.get("course");
  if (!course) {
    const p = await programs.get(id, signal);
    return { link: { id, name: p.name, course: null, mode }, state: null, arc: null };
  }
  const a = await programs.arc(id, course, adminToken(id), signal);
  const link: ProgramLink = { id, name: a.program.name, course, mode };
  const state = normalizeState({
    ...a.state,
    // The instructor fills in their own name and term.
    ...(mode === "section" ? { instructor: "", term_label: "" } : {}),
    program: link,
    section: mode === "section" ? emptySection() : null,
  });
  const ids = allIds(state);
  const [arc] = await Promise.all([
    state.arc_id ? api.arc(state.arc_id, signal).catch(() => null) : Promise.resolve(null),
    ensureBriefs(ids.readings, ids.assignments, signal),
  ]);
  return { link, state, arc };
}

/** Attach a program link to a state the builder made the usual way. */
export function withProgram(state: BuildState, start: ProgramStart | null): BuildState {
  if (!start) return state;
  return { ...state, program: start.link, section: start.link.mode === "template" ? null : state.section ?? emptySection() };
}

/** Whether a draft kept in this browser belongs to the course this URL starts. */
export function draftFits(draft: BuildState, arcId: string | null, start: ProgramStart | null): boolean {
  const p = draft.program ?? null;
  const sameArc = (draft.arc_id ?? null) === (arcId ?? null);
  if (!start) return !p && sameArc;
  if (!p || p.id !== start.link.id || (p.mode ?? "section") !== start.link.mode) return false;
  // A coordinator's draft records the program course it was saved to, so only compare when the URL names one.
  if (start.link.course && p.course !== start.link.course) return false;
  return start.state ? true : sameArc;
}
