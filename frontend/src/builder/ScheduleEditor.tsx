import { useId, useMemo } from "react";
import { briefCache } from "../api";
import "./schedule.css";
import {
  browserTimezone,
  DAY_NAMES,
  DAYS,
  emptySchedule,
  formatDay,
  isRange,
  timezoneOptions,
  type CourseSchedule,
  type Day,
  type NoClassEntry,
  type ScheduleResult,
} from "./schedule";

interface Props {
  schedule: CourseSchedule | null | undefined;
  dates: ScheduleResult | null;
  readOnly: boolean;
  /** Unit titles by key, for the due-date list. */
  unitTitles: Record<string, string>;
  onChange: (s: CourseSchedule | null) => void;
}

function focusLater(id: string) {
  window.setTimeout(() => document.getElementById(id)?.focus(), 0);
}

/** Start date, meeting days, class time, no-class days, and time zone for the course. */
export function ScheduleEditor({ schedule, dates, readOnly, unitTitles, onChange }: Props) {
  const id = useId();
  const s = schedule ?? null;
  const timezone = s?.timezone || browserTimezone();
  const zones = useMemo(() => timezoneOptions(timezone), [timezone]);
  if (readOnly && !s?.start_date) return null;

  const update = (patch: Partial<CourseSchedule>) => onChange({ ...(s ?? emptySchedule()), ...patch });
  const toggleDay = (d: Day) => {
    const days = s?.meeting_days ?? [];
    update({ meeting_days: DAYS.filter((x) => (x === d ? !days.includes(d) : days.includes(x))) });
  };
  const noClass = s?.no_class ?? [];
  const setEntry = (i: number, e: NoClassEntry) => update({ no_class: noClass.map((x, j) => (j === i ? e : x)) });
  const addEntry = (e: NoClassEntry) => {
    update({ no_class: [...noClass, e] });
    focusLater(`${id}-nc-${noClass.length}-label`);
  };
  const removeEntry = (i: number) => {
    update({ no_class: noClass.filter((_, j) => j !== i) });
    const next = noClass.length - 1 > i ? i : i - 1;
    focusLater(next >= 0 ? `${id}-nc-${next}-label` : `${id}-add-day`);
  };
  const setDue = (aid: string, value: string, suggested: string) => {
    const due = { ...(s?.due_dates ?? {}) };
    if (!value || value === suggested) delete due[aid];
    else due[aid] = value;
    update({ due_dates: due });
  };

  const ok = dates?.ok ? dates : null;
  const lastMeeting = ok?.meetings?.length ? ok.meetings[ok.meetings.length - 1] : null;

  return (
    <details className="term-edit schedule-edit" open={Boolean(s?.start_date) || undefined}>
      <summary>{s?.start_date ? "Class dates" : "Add class dates"}</summary>
      <p className="hint">
        With a start date and meeting days, Aletheia dates each week and class, adds the dates to the reader and LMS
        package, and can export a calendar and a syllabus skeleton.
      </p>
      <fieldset className="schedule-form" disabled={readOnly}>
        <legend className="visually-hidden">Class dates</legend>
        <div className="schedule-row">
          <div className="field">
            <label htmlFor={`${id}-start`}>First day of the term</label>
            <input
              id={`${id}-start`}
              type="date"
              value={s?.start_date ?? ""}
              onChange={(e) => update({ start_date: e.target.value })}
              aria-describedby={`${id}-start-help`}
            />
            <p id={`${id}-start-help`} className="hint">
              Week 1 is the week this date falls in.
            </p>
          </div>
          <div className="field">
            <label htmlFor={`${id}-tz`}>Time zone</label>
            <select id={`${id}-tz`} value={timezone} onChange={(e) => update({ timezone: e.target.value })}>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
        </div>

        <fieldset className="field-group">
          <legend>Class meets on</legend>
          <div className="day-toggles">
            {DAYS.map((d) => {
              const on = Boolean(s?.meeting_days.includes(d));
              return (
                <button key={d} type="button" className="day-toggle" aria-pressed={on} onClick={() => toggleDay(d)}>
                  <span aria-hidden="true">{d}</span>
                  <span className="visually-hidden">{DAY_NAMES[d]}</span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset className="field-group">
          <legend>Class time (optional)</legend>
          <div className="schedule-row">
            <div className="field">
              <label htmlFor={`${id}-st`}>Starts</label>
              <input
                id={`${id}-st`}
                type="time"
                value={s?.start_time ?? ""}
                onChange={(e) => update({ start_time: e.target.value || undefined })}
              />
            </div>
            <div className="field">
              <label htmlFor={`${id}-et`}>Ends</label>
              <input
                id={`${id}-et`}
                type="time"
                value={s?.end_time ?? ""}
                onChange={(e) => update({ end_time: e.target.value || undefined })}
              />
            </div>
          </div>
          <p className="hint">Without a time, class meetings show as all-day events in the calendar.</p>
        </fieldset>

        <fieldset className="field-group">
          <legend>No-class days</legend>
          <p className="hint">
            A break that covers Monday through Friday skips that week, and later weeks move back a week. A single day
            off cancels only that day's class.
          </p>
          {noClass.length > 0 && (
            <ul className="noclass-list">
              {noClass.map((e, i) => {
                const base = `${id}-nc-${i}`;
                const name = e.label || (isRange(e) ? "break" : "day off");
                return (
                  <li key={i} className="noclass-row">
                    <div className="field">
                      <label htmlFor={`${base}-label`}>Name</label>
                      <input
                        id={`${base}-label`}
                        value={e.label ?? ""}
                        placeholder={isRange(e) ? "Spring break" : "Holiday"}
                        onChange={(ev) => setEntry(i, { ...e, label: ev.target.value })}
                      />
                    </div>
                    {isRange(e) ? (
                      <>
                        <div className="field">
                          <label htmlFor={`${base}-from`}>From</label>
                          <input
                            id={`${base}-from`}
                            type="date"
                            value={e.start}
                            onChange={(ev) => setEntry(i, { ...e, start: ev.target.value })}
                          />
                        </div>
                        <div className="field">
                          <label htmlFor={`${base}-to`}>To</label>
                          <input
                            id={`${base}-to`}
                            type="date"
                            value={e.end}
                            min={e.start || undefined}
                            onChange={(ev) => setEntry(i, { ...e, end: ev.target.value })}
                          />
                        </div>
                      </>
                    ) : (
                      <div className="field">
                        <label htmlFor={`${base}-date`}>Date</label>
                        <input
                          id={`${base}-date`}
                          type="date"
                          value={e.date}
                          onChange={(ev) => setEntry(i, { ...e, date: ev.target.value })}
                        />
                      </div>
                    )}
                    <button type="button" className="btn btn-small btn-danger" onClick={() => removeEntry(i)}>
                      Remove<span className="visually-hidden"> {name}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="actions">
            <button type="button" id={`${id}-add-day`} className="btn btn-small" onClick={() => addEntry({ date: "", label: "" })}>
              Add a day off
            </button>
            <button type="button" className="btn btn-small" onClick={() => addEntry({ start: "", end: "", label: "" })}>
              Add a break
            </button>
          </div>
        </fieldset>
      </fieldset>

      <div aria-live="polite">
        {ok && ok.weeks && ok.weeks.length > 0 && (
          <p className="schedule-summary">
            Week 1 starts {formatDay(ok.weeks[0].start)}.{" "}
            {lastMeeting
              ? `The course has ${ok.meetings!.length} class meetings; the last is ${formatDay(lastMeeting.date)}.`
              : `The last week ends ${formatDay(ok.term_end!)}.`}
            {ok.breaks && ok.breaks.length > 0 && (
              <> Breaks: {ok.breaks.map((b) => `${b.label} (${b.dates})`).join("; ")}.</>
            )}
          </p>
        )}
        {dates && dates.warnings.length > 0 && (
          <ul className="notice notice-warn schedule-messages">
            {dates.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
        {dates && dates.notes.length > 0 && (
          <ul className="hint schedule-messages">
            {dates.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        )}
      </div>

      {ok && ok.due && ok.due.length > 0 && (
        <fieldset className="field-group" disabled={readOnly}>
          <legend>Due dates for major assignments</legend>
          <p className="hint">Each one starts on its unit's last class. Change any of them here.</p>
          <ul className="due-list">
            {ok.due.map((d) => {
              const title = briefCache.assignments.get(d.id)?.title ?? d.id;
              const inputId = `${id}-due-${d.id}`;
              return (
                <li key={`${d.unit}:${d.id}`} className="due-row">
                  <div className="field">
                    <label htmlFor={inputId}>
                      {title}
                      <span className="due-unit"> · {unitTitles[d.unit] ?? d.unit}</span>
                    </label>
                    <input
                      id={inputId}
                      type="date"
                      value={s?.due_dates?.[d.id] ?? d.suggested}
                      onChange={(e) => setDue(d.id, e.target.value, d.suggested)}
                    />
                  </div>
                  {d.overridden && !readOnly && (
                    <button type="button" className="btn btn-small btn-quiet" onClick={() => setDue(d.id, "", d.suggested)}>
                      Use {formatDay(d.suggested)}
                      <span className="visually-hidden"> for {title}</span>
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </fieldset>
      )}

      {s && !readOnly && (
        <button
          type="button"
          className="btn btn-small btn-quiet"
          onClick={() => {
            if (window.confirm("Remove the class dates from this course?")) onChange(null);
          }}
        >
          Remove class dates
        </button>
      )}
    </details>
  );
}
