import type { BuildState } from "../api";
import { teachingWeeks } from "./model";

interface Props {
  state: BuildState;
  /** Arc unit titles by key, for naming units the planner dropped. */
  arcTitles: Record<string, string>;
}

interface Segment {
  kind: "fixed" | "unit";
  label: string;
  start: number;
  end: number;
  n?: number;
  key?: string;
  over?: boolean;
}

function range(start: number, end: number): string {
  return start === end ? `Week ${start}` : `Weeks ${start}–${end}`;
}

/** The week grid: fixed weeks plus units laid across the term, with a readable schedule list. */
export function TermBar({ state, arcTitles }: Props) {
  const { term, units } = state;
  const lastTeaching = term.weeks - term.fixed_end;
  const segments: Segment[] = [];
  for (let w = 1; w <= term.fixed_start; w++) segments.push({ kind: "fixed", label: "Orientation", start: w, end: w });
  units.forEach((u, i) => {
    const start = u.start_week ?? term.fixed_start + 1;
    const end = u.end_week ?? start + u.weeks - 1;
    segments.push({ kind: "unit", label: u.title, start, end, n: i + 1, key: u.key, over: end > lastTeaching });
  });
  const unitEnd = Math.max(lastTeaching, ...segments.map((s) => s.end));
  for (let i = 0; i < term.fixed_end; i++) {
    const w = unitEnd + 1 + i;
    segments.push({ kind: "fixed", label: "Final portfolio and reflection", start: w, end: w });
  }
  const totalWeeks = Math.max(term.weeks, ...segments.map((s) => s.end));
  const usedWeeks = units.reduce((n, u) => n + u.weeks, 0);
  const available = teachingWeeks(term);

  const note = state.plan_note;
  const name = (k: string) =>
    k
      .split("+")
      .map((part) => arcTitles[part] ?? part)
      .join(" + ");
  const termName = note ? `${note.weeks}-week term` : "";

  return (
    <div className="termbar">
      <div className="weekbar" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${totalWeeks}, minmax(0, 1fr))` }}>
        {segments.map((s, i) => (
          <div
            key={i}
            className={`seg seg-${s.kind}${s.kind === "unit" ? ` seg-c${((s.n ?? 1) - 1) % 6}` : ""}${s.over ? " seg-over" : ""}`}
            style={{ gridColumn: `${s.start} / ${s.end + 1}` }}
            title={`${range(s.start, s.end)}: ${s.label}`}
          >
            <span className="seg-label">{s.kind === "unit" ? s.n : s.label.startsWith("Orientation") ? "O" : "F"}</span>
          </div>
        ))}
      </div>
      <div className="weekbar-scale" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${totalWeeks}, minmax(0, 1fr))` }}>
        {Array.from({ length: totalWeeks }, (_, i) => (
          <span key={i}>{(i + 1) % 2 === 1 || totalWeeks <= 10 ? i + 1 : ""}</span>
        ))}
      </div>

      <ol className="schedule">
        {segments.map((s, i) => (
          <li key={i} className={s.over ? "is-over" : undefined}>
            <span className="schedule-weeks">{range(s.start, s.end)}</span>{" "}
            {s.kind === "unit" ? (
              <a href={`#unit-${s.key}`}>
                <span className="schedule-num">Unit {s.n}:</span> {s.label}
              </a>
            ) : (
              <span className="schedule-fixed">{s.label}</span>
            )}
          </li>
        ))}
      </ol>

      {usedWeeks > available && (
        <p className="notice notice-warn">
          Your units add up to {usedWeeks} weeks, but this term has {available} weeks for units. Shorten a unit or
          lengthen the term.
        </p>
      )}
      {!state.arc_id && usedWeeks < available && (
        <p className="hint">
          {available - usedWeeks} {available - usedWeeks === 1 ? "week isn't" : "weeks aren't"} assigned to a unit yet.
        </p>
      )}
      {note && (note.dropped.length > 0 || note.merged.length > 0) && (
        <div className="notice notice-info">
          {note.dropped.length > 0 && (
            <p>
              Dropped for a {termName}: {note.dropped.map(name).join("; ")}.
            </p>
          )}
          {note.merged.length > 0 && (
            <p>
              Combined for a {termName}: {note.merged.map((m) => m.map(name).join(" + ")).join("; ")}.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
