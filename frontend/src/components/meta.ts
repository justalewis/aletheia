import type { AssignmentBrief, AssignmentRole, ReadingBrief } from "../api";

export const LEVELS: { value: string; label: string }[] = [
  { value: "chapter", label: "Chapter" },
  { value: "article", label: "Article" },
  { value: "book", label: "Book" },
  { value: "primary-text", label: "Primary text" },
  { value: "website", label: "Website" },
  { value: "journal", label: "Journal" },
  { value: "collection", label: "Collection" },
];

/** Readings short enough to assign in a week: the usual thing to add to a unit. */
export const SHORT_LEVELS = "chapter,article,primary-text,website";
export const SHORT_LEVELS_LABEL = "Chapters, articles, and short texts";

/** Assignment kinds in the current catalog (the API doesn't list them yet). */
export const KINDS: { value: string; label: string }[] = [
  { value: "major assignment", label: "Major assignment" },
  { value: "scaffolding activity", label: "Scaffolding activity" },
  { value: "in-class activity", label: "In-class activity" },
  { value: "lesson plan", label: "Lesson plan" },
  { value: "syllabus", label: "Syllabus" },
  { value: "course design", label: "Course design" },
  { value: "rubric", label: "Rubric" },
  { value: "grading contract", label: "Grading contract" },
];

export const ROLE_LABELS: Record<AssignmentRole, string> = {
  major: "Major project",
  short_term: "Short-term version",
  activity: "Activities",
};

export function levelLabel(level: string): string {
  return LEVELS.find((l) => l.value === level)?.label ?? level;
}

export function kindLabel(kind: string): string {
  const k = KINDS.find((x) => x.value === kind)?.label;
  if (k) return k;
  return kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "";
}

/** "Authors · In Parent · 2021" for a reading. */
export function readingByline(r: ReadingBrief): string {
  const parts: string[] = [];
  if (r.authors) parts.push(shortAuthors(r.authors));
  if (r.parent_title) parts.push(`in ${r.parent_title}`);
  if (r.year) parts.push(r.year);
  return parts.join(" · ");
}

export function assignmentByline(a: AssignmentBrief): string {
  const parts: string[] = [];
  if (a.authors) parts.push(shortAuthors(a.authors));
  if (a.source_repository) parts.push(a.source_repository);
  if (a.year) parts.push(a.year);
  return parts.join(" · ");
}

/** Trim long author lists to the first two names. */
export function shortAuthors(authors: string): string {
  const sep = authors.includes(";") ? ";" : null;
  if (!sep) return authors.length > 90 ? `${authors.slice(0, 87)}…` : authors;
  const names = authors.split(sep).map((s) => s.trim()).filter(Boolean);
  if (names.length <= 2) return names.join("; ");
  return `${names.slice(0, 2).join("; ")}, and others`;
}

/** Map a catalog assignment kind to the builder role it most likely fills. */
export function roleForKind(kind: string): AssignmentRole {
  return kind === "major assignment" ? "major" : "activity";
}
