/** Lys icon set: one sprite (public/icons.svg), 1.5px line, takes the surrounding text color. */
export type IconName = string;

export function Icon({ name, size = 24, label }: { name: IconName; size?: 16 | 20 | 24 | 32; label?: string }) {
  return (
    <svg
      className="ic"
      width={size}
      height={size}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <use href={`/icons.svg#i-${name}`} />
    </svg>
  );
}

const LEVEL_ICONS: Record<string, string> = {
  book: "book",
  chapter: "chapter",
  article: "article",
  "primary-text": "primary-text",
  website: "website",
  journal: "journal",
  collection: "book",
};

const KIND_ICONS: Record<string, string> = {
  "major assignment": "assignment",
  "scaffolding activity": "scaffold",
  "in-class activity": "activity",
  syllabus: "syllabus",
  "course design": "course-design",
  "lesson plan": "lesson-plan",
  rubric: "rubric",
  "grading contract": "contract",
};

export const levelIcon = (level: string) => LEVEL_ICONS[level] ?? "chapter";
export const kindIcon = (kind: string) => KIND_ICONS[kind] ?? "assignment";

const TIER_ICONS: Record<string, string> = {
  adapt: "reuse-adapt",
  "adapt-nc": "reuse-nc",
  verbatim: "reuse-whole",
  "link-only": "reuse-link",
};
export const tierIcon = (tier: string) => TIER_ICONS[tier] ?? "reuse-link";
