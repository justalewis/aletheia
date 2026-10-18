import { useEffect, useId, useState } from "react";
import type { Term, TermPreset } from "../api";

interface Props {
  value: Term;
  onChange: (t: Term) => void;
  presets: TermPreset[];
  disabled?: boolean;
  compact?: boolean;
}

export function matchPreset(t: Term, presets: TermPreset[]): string {
  const p = presets.find((p) => p.weeks === t.weeks && p.fixed_start === t.fixed_start && p.fixed_end === t.fixed_end);
  return p ? p.id : "custom";
}

export function presetToTerm(p: TermPreset): Term {
  return { weeks: p.weeks, fixed_start: p.fixed_start, fixed_end: p.fixed_end, pages_per_week: p.pages_per_week };
}

/** Sensible fixed weeks and reading load for a custom term length. */
export function termForWeeks(weeks: number, presets: TermPreset[]): Term {
  const exact = presets.find((p) => p.weeks === weeks);
  if (exact) return presetToTerm(exact);
  const nearest = [...presets].sort((a, b) => Math.abs(a.weeks - weeks) - Math.abs(b.weeks - weeks))[0];
  return {
    weeks,
    fixed_start: weeks >= 12 ? 1 : 0,
    fixed_end: weeks >= 6 ? 1 : 0,
    pages_per_week: nearest?.pages_per_week ?? 40,
  };
}

export function teachingWeeks(t: Term): number {
  return Math.max(t.weeks - t.fixed_start - t.fixed_end, 1);
}

export function clampInt(v: string, min: number, max: number, fallback: number): number {
  const n = parseInt(v, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export function TermPicker({ value, onChange, presets, disabled, compact }: Props) {
  const id = useId();
  const [choice, setChoice] = useState(() => matchPreset(value, presets));
  const [weeksText, setWeeksText] = useState(String(value.weeks));
  const [ppwText, setPpwText] = useState(String(value.pages_per_week));

  // Follow outside changes (a re-plan, a restored draft).
  useEffect(() => {
    setWeeksText(String(value.weeks));
    setPpwText(String(value.pages_per_week));
    // Once someone picks Custom, keep the weeks field visible while they type.
    setChoice((c) => (c === "custom" ? c : matchPreset(value, presets)));
  }, [value, presets]);

  const pick = (pid: string) => {
    setChoice(pid);
    const p = presets.find((x) => x.id === pid);
    if (p) onChange(presetToTerm(p));
  };

  return (
    <div className={`termpicker${compact ? " termpicker-compact" : ""}`}>
      <fieldset className="field-group" disabled={disabled}>
        <legend>Term length</legend>
        <div className="radio-grid">
          {presets.map((p) => (
            <label key={p.id} className="radio-card">
              <input type="radio" name={`${id}-preset`} value={p.id} checked={choice === p.id} onChange={() => pick(p.id)} />
              <span>{p.label}</span>
            </label>
          ))}
          <label className="radio-card">
            <input
              type="radio"
              name={`${id}-preset`}
              value="custom"
              checked={choice === "custom"}
              onChange={() => setChoice("custom")}
            />
            <span>Custom</span>
          </label>
        </div>
      </fieldset>

      <div className="term-fields">
        {choice === "custom" && (
          <div className="field field-narrow">
            <label htmlFor={`${id}-weeks`}>Weeks in the term</label>
            <input
              id={`${id}-weeks`}
              type="number"
              inputMode="numeric"
              min={1}
              max={30}
              value={weeksText}
              disabled={disabled}
              onChange={(e) => {
                setWeeksText(e.target.value);
                const n = parseInt(e.target.value, 10);
                if (!Number.isNaN(n) && n >= 1 && n <= 30) onChange({ ...value, weeks: n });
              }}
              onBlur={() => setWeeksText(String(value.weeks))}
            />
          </div>
        )}
        <div className="field field-narrow">
          <label htmlFor={`${id}-ppw`}>Pages of reading per week</label>
          <input
            id={`${id}-ppw`}
            type="number"
            inputMode="numeric"
            min={5}
            max={300}
            step={5}
            value={ppwText}
            disabled={disabled}
            onChange={(e) => {
              setPpwText(e.target.value);
              const n = parseInt(e.target.value, 10);
              if (!Number.isNaN(n) && n >= 5 && n <= 300) onChange({ ...value, pages_per_week: n });
            }}
            onBlur={() => setPpwText(String(value.pages_per_week))}
          />
        </div>
        <fieldset className="field-checks" disabled={disabled}>
          <legend className="visually-hidden">Weeks outside the units</legend>
          <label className="check">
            <input
              type="checkbox"
              checked={value.fixed_start > 0}
              onChange={(e) => onChange({ ...value, fixed_start: e.target.checked ? 1 : 0 })}
            />
            Orientation week at the start
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={value.fixed_end > 0}
              onChange={(e) => onChange({ ...value, fixed_end: e.target.checked ? 1 : 0 })}
            />
            Final portfolio week at the end
          </label>
        </fieldset>
      </div>
      <p className="hint">
        {value.weeks} weeks, {teachingWeeks(value)} of them for units. About {value.pages_per_week} pages of reading a week.
      </p>
    </div>
  );
}
