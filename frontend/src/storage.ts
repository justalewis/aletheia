/**
 * localStorage helpers. Storage can be missing or full (private windows, blocked site data),
 * so every access is wrapped and the app works without it.
 */
import type { AssignmentBrief, BuildState, ReadingBrief } from "./api";

export function readJSON<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeJSON(key: string, value: unknown): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function removeKey(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ saved builds */

const BUILDS_KEY = "aletheia:builds";

export interface MyBuild {
  token: string;
  title: string;
  updated: string;
}

export function myBuilds(): Record<string, MyBuild> {
  const data = readJSON<Record<string, MyBuild>>(BUILDS_KEY, {});
  return data && typeof data === "object" ? data : {};
}

export function editToken(id: string): string | null {
  return myBuilds()[id]?.token ?? null;
}

export function rememberBuild(id: string, token: string, title: string, updated: string): void {
  const all = myBuilds();
  all[id] = { token, title, updated };
  writeJSON(BUILDS_KEY, all);
}

export function forgetBuild(id: string): void {
  const all = myBuilds();
  delete all[id];
  writeJSON(BUILDS_KEY, all);
}

/* ------------------------------------------------------------------ drafts */

export interface Draft {
  state: BuildState;
  savedAt: string;
  readings?: ReadingBrief[];
  assignments?: AssignmentBrief[];
}

export function draftKey(buildId: string | null): string {
  return `aletheia:draft:${buildId ?? "new"}`;
}

export function loadDraft(buildId: string | null): Draft | null {
  const d = readJSON<Draft | null>(draftKey(buildId), null);
  return d && d.state && Array.isArray(d.state.units) ? d : null;
}

export function saveDraft(buildId: string | null, draft: Draft): void {
  if (!writeJSON(draftKey(buildId), draft)) {
    // Storage full: try again without the cached briefs.
    writeJSON(draftKey(buildId), { state: draft.state, savedAt: draft.savedAt });
  }
}

export function clearDraft(buildId: string | null): void {
  removeKey(draftKey(buildId));
}
