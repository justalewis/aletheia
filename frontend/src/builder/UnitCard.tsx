import { useEffect, useId, useRef, useState, type MouseEvent } from "react";
import { Icon } from "../components/Icon";
import { briefCache, type AssignmentRole, type StateAssignment, type StateReading, type StateUnit, type Term } from "../api";
import { TierBadge } from "../components/TierBadge";
import { assignmentByline, kindLabel, readingByline, ROLE_LABELS } from "../components/meta";
import { unitBudget, unitPages, weeksLabel } from "./model";
import { formatDay, type UnitDates } from "./schedule";

export interface UnitActions {
  toggleReading: (id: string) => void;
  moveReading: (index: number, dir: -1 | 1) => void;
  removeReading: (id: string) => void;
  toggleAssignment: (id: string) => void;
  removeAssignment: (id: string) => void;
  rename: (title: string) => void;
  openSearch: (kind: "reading" | "assignment") => void;
  // Blank courses only
  setWeeks?: (weeks: number) => void;
  remove?: () => void;
  move?: (dir: -1 | 1) => void;
}

interface Props {
  unit: StateUnit;
  index: number;
  count: number;
  term: Term;
  readOnly: boolean;
  blank: boolean;
  actions: UnitActions;
  /** Calendar dates and due dates, once the course has a start date. */
  dates?: UnitDates;
}

const ROLES: AssignmentRole[] = ["major", "short_term", "activity"];
const ROLE_HINTS: Partial<Record<AssignmentRole, string>> = {
  short_term: "A lighter version of the major project. Aletheia checks it instead for terms of 8 weeks or fewer.",
};

export function UnitCard({ unit, index, count, term, readOnly, blank, actions, dates }: Props) {
  const headingId = useId();
  const pages = unitPages(unit);
  const budget = unitBudget(unit, term);
  const over = pages > budget;
  const pct = Math.min(100, Math.round((pages / budget) * 100));
  const [open, setOpen] = useState(true);
  const bodyId = useId();

  return (
    <section className="unit-card" id={`unit-${unit.key}`} aria-labelledby={headingId}>
      <header className="unit-head">
        <p className="unit-weeks">
          <Icon name="unit" size={16} />
          Unit {index + 1} · {weeksLabel(unit)}
          {dates ? ` · ${dates.label}` : ""}
          <button
            type="button"
            className={`unit-toggle${open ? " is-open" : ""}`}
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "Collapse" : "Expand"}
            <span className="visually-hidden"> unit {unit.title}</span>
            <Icon name="chevron" size={16} />
          </button>
        </p>
        <UnitTitle unit={unit} headingId={headingId} readOnly={readOnly} onRename={actions.rename} />
        {blank && !readOnly && (
          <div className="unit-tools">
            <WeeksInput unit={unit} onChange={(n) => actions.setWeeks?.(n)} />
            <button
              type="button"
              className="btn btn-small btn-quiet"
              data-move="up"
              disabled={index === 0}
              onClick={(e) => moveWithFocus(e, () => actions.move?.(-1), ".unit-card")}
            >
              Move up<span className="visually-hidden"> unit {unit.title}</span>
            </button>
            <button
              type="button"
              className="btn btn-small btn-quiet"
              data-move="down"
              disabled={index === count - 1}
              onClick={(e) => moveWithFocus(e, () => actions.move?.(1), ".unit-card")}
            >
              Move down<span className="visually-hidden"> unit {unit.title}</span>
            </button>
            <button type="button" className="btn btn-small btn-danger" disabled={count <= 1} onClick={() => actions.remove?.()}>
              Delete unit<span className="visually-hidden"> {unit.title}</span>
            </button>
          </div>
        )}
      </header>

      <div className="unit-body" id={bodyId} hidden={!open}>
      {unit.summary && <p className="prose unit-summary">{unit.summary}</p>}
      {unit.goals.length > 0 && (
        <div className="unit-goals">
          <h4 className="h-tiny">Goals</h4>
          <ul className="goals">
            {unit.goals.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}

      <div className={`meter${over ? " meter-over" : ""}`}>
        <div className="meter-label">
          <span>Reading load</span>
          <span>
            {pages} of {budget} pages
            <span className="visually-hidden"> (estimated)</span>
          </span>
        </div>
        <div className="meter-track" aria-hidden="true">
          <div className="meter-fill" style={{ width: `${pct}%` }} />
        </div>
        {over ? (
          <p className="meter-warn">
            <span aria-hidden="true">▲ </span>
            <strong>Over by {pages - budget} pages.</strong> That's about {Math.round(pages / Math.max(1, unit.weeks))} pages a
            week against your {term.pages_per_week}. Uncheck a reading{blank ? " or give this unit more weeks" : ""}.
          </p>
        ) : (
          <p className="meter-note">
            Budget: {unit.weeks} {unit.weeks === 1 ? "week" : "weeks"} × {term.pages_per_week} pages. Page counts are
            estimates.
          </p>
        )}
      </div>

      <div className="unit-section">
        <h4 className="unit-section-title">Readings</h4>
        {unit.readings.length === 0 ? (
          <p className="empty-inline">No readings yet.</p>
        ) : (
          <ul className="checklist">
            {unit.readings.map((r, i) => (
              <ReadingRow
                key={r.id}
                item={r}
                index={i}
                last={i === unit.readings.length - 1}
                readOnly={readOnly}
                onToggle={() => actions.toggleReading(r.id)}
                onMove={(dir) => actions.moveReading(i, dir)}
                onRemove={() => actions.removeReading(r.id)}
              />
            ))}
          </ul>
        )}
        {!readOnly && (
          <button type="button" className="btn btn-small unit-add" onClick={() => actions.openSearch("reading")}>
            Add reading<span className="visually-hidden"> to {unit.title}</span>
          </button>
        )}
      </div>

      <div className="unit-section">
        <h4 className="unit-section-title">Assignments</h4>
        {unit.assignments.length === 0 && <p className="empty-inline">No assignments yet.</p>}
        {ROLES.map((role) => {
          const items = unit.assignments.filter((a) => a.role === role);
          if (!items.length) return null;
          return (
            <div key={role} className="role-group">
              <h5 className="role-title">{ROLE_LABELS[role]}</h5>
              {ROLE_HINTS[role] && <p className="hint">{ROLE_HINTS[role]}</p>}
              <ul className="checklist">
                {items.map((a) => (
                  <AssignmentRow
                    key={a.id}
                    item={a}
                    due={dates?.due[a.id]}
                    readOnly={readOnly}
                    onToggle={() => actions.toggleAssignment(a.id)}
                    onRemove={() => actions.removeAssignment(a.id)}
                  />
                ))}
              </ul>
            </div>
          );
        })}
        {!readOnly && (
          <button type="button" className="btn btn-small unit-add" onClick={() => actions.openSearch("assignment")}>
            Add assignment<span className="visually-hidden"> to {unit.title}</span>
          </button>
        )}
      </div>

      {unit.notes && (
        <details className="unit-notes">
          <summary>Notes for instructors</summary>
          <p className="prose">{unit.notes}</p>
        </details>
      )}
      </div>
    </section>
  );
}

function UnitTitle({
  unit,
  headingId,
  readOnly,
  onRename,
}: {
  unit: StateUnit;
  headingId: string;
  readOnly: boolean;
  onRename: (t: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(unit.title);
  const inputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const inputId = useId();

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const finish = (save: boolean) => {
    if (save && text.trim() && text.trim() !== unit.title) onRename(text.trim());
    else setText(unit.title);
    setEditing(false);
    window.setTimeout(() => buttonRef.current?.focus(), 0);
  };

  if (editing) {
    return (
      <form
        className="rename-form"
        onSubmit={(e) => {
          e.preventDefault();
          finish(true);
        }}
      >
        <h3 id={headingId} className="visually-hidden">
          {unit.title}
        </h3>
        <label htmlFor={inputId} className="visually-hidden">
          Unit title
        </label>
        <input
          id={inputId}
          ref={inputRef}
          className="rename-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              finish(false);
            }
          }}
        />
        <button type="submit" className="btn btn-small btn-primary">
          Save
        </button>
        <button type="button" className="btn btn-small btn-quiet" onClick={() => finish(false)}>
          Cancel
        </button>
      </form>
    );
  }
  return (
    <div className="unit-title-row">
      <h3 id={headingId} className="unit-title">
        {unit.title}
      </h3>
      {!readOnly && (
        <button
          ref={buttonRef}
          type="button"
          className="btn btn-small btn-quiet"
          onClick={() => {
            setText(unit.title);
            setEditing(true);
          }}
        >
          Rename<span className="visually-hidden"> {unit.title}</span>
        </button>
      )}
    </div>
  );
}

function WeeksInput({ unit, onChange }: { unit: StateUnit; onChange: (n: number) => void }) {
  const id = useId();
  const [text, setText] = useState(String(unit.weeks));
  useEffect(() => setText(String(unit.weeks)), [unit.weeks]);
  return (
    <span className="weeks-input">
      <label htmlFor={id}>Weeks</label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={1}
        max={30}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const n = parseInt(e.target.value, 10);
          if (!Number.isNaN(n) && n >= 1 && n <= 30) onChange(n);
        }}
        onBlur={() => setText(String(unit.weeks))}
      />
    </span>
  );
}

/** After removing a row, put focus on the next row (or the previous one, or the section's Add button). */
function removeWithFocus(e: MouseEvent<HTMLButtonElement>, remove: () => void) {
  const li = e.currentTarget.closest("li");
  const sibling = (li?.nextElementSibling ?? li?.previousElementSibling) as HTMLElement | null;
  const section = li?.closest(".unit-section");
  remove();
  window.setTimeout(() => {
    const target =
      (sibling && document.contains(sibling) ? sibling.querySelector<HTMLElement>("input[type=checkbox]") : null) ??
      section?.querySelector<HTMLElement>(".unit-add");
    target?.focus();
  }, 0);
}

/** Keep focus on the Up/Down control after a move; if it just became disabled, use the other one. */
function moveWithFocus(e: MouseEvent<HTMLButtonElement>, move: () => void, container = "li") {
  const btn = e.currentTarget;
  const row = btn.closest(container);
  const which = btn.dataset.move;
  move();
  window.setTimeout(() => {
    const same = row?.querySelector<HTMLButtonElement>(`:scope [data-move="${which}"]`);
    const other = row?.querySelector<HTMLButtonElement>(`:scope [data-move="${which === "up" ? "down" : "up"}"]`);
    (same && !same.disabled ? same : other)?.focus();
  }, 0);
}

function ExternalLink({ href, title }: { href: string; title: string }) {
  return (
    <a className="row-action" href={href} target="_blank" rel="noopener noreferrer">
      Open<span className="visually-hidden"> {title} (opens in a new tab)</span>
      <span aria-hidden="true"> ↗</span>
    </a>
  );
}

function ReadingRow({
  item,
  index,
  last,
  readOnly,
  onToggle,
  onMove,
  onRemove,
}: {
  item: StateReading;
  index: number;
  last: boolean;
  readOnly: boolean;
  onToggle: () => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const cbId = useId();
  const detailsId = useId();
  const r = briefCache.readings.get(item.id);
  const title = r?.title ?? item.id;
  const role = item.added ? "Added" : item.role === "core" ? "Core" : item.role === "optional" ? "Optional" : "";

  return (
    <li className={`check-row${item.selected ? " is-selected" : ""}`}>
      <div className="check-main">
        <input id={cbId} type="checkbox" checked={item.selected} disabled={readOnly} onChange={onToggle} />
        <div className="check-body">
          <label htmlFor={cbId} className="check-title">
            {title}
          </label>
          {r ? (
            <p className="meta">
              {readingByline(r)}
              {readingByline(r) ? " · " : ""}~{r.est_pages} pp.
              {role ? ` · ${role}` : ""}
            </p>
          ) : (
            <p className="meta">This item isn't in the catalog anymore. You can remove it.</p>
          )}
        </div>
        {r && <TierBadge tier={r.reuse_tier} verified={r.license_verified} />}
      </div>
      <div className="row-actions">
        {r?.url && <ExternalLink href={r.url} title={title} />}
        {r?.summary && (
          <button type="button" className="row-action" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide details" : "Details"}
            <span className="visually-hidden"> for {title}</span>
          </button>
        )}
        {!readOnly && (
          <>
            <button
              type="button"
              className="row-action"
              data-move="up"
              disabled={index === 0}
              onClick={(e) => moveWithFocus(e, () => onMove(-1))}
            >
              Up<span className="visually-hidden">: move {title} up</span>
            </button>
            <button
              type="button"
              className="row-action"
              data-move="down"
              disabled={last}
              onClick={(e) => moveWithFocus(e, () => onMove(1))}
            >
              Down<span className="visually-hidden">: move {title} down</span>
            </button>
            <button type="button" className="row-action row-action-danger" onClick={(e) => removeWithFocus(e, onRemove)}>
              Remove<span className="visually-hidden"> {title}</span>
            </button>
          </>
        )}
      </div>
      {open && r?.summary && (
        <p id={detailsId} className="row-details">
          {r.summary}
        </p>
      )}
    </li>
  );
}

function AssignmentRow({
  item,
  due,
  readOnly,
  onToggle,
  onRemove,
}: {
  item: StateAssignment;
  /** ISO due date, for checked major assignments in a dated course. */
  due?: string;
  readOnly: boolean;
  onToggle: () => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const cbId = useId();
  const detailsId = useId();
  const a = briefCache.assignments.get(item.id);
  const title = a?.title ?? item.id;

  return (
    <li className={`check-row${item.selected ? " is-selected" : ""}`}>
      <div className="check-main">
        <input id={cbId} type="checkbox" checked={item.selected} disabled={readOnly} onChange={onToggle} />
        <div className="check-body">
          <label htmlFor={cbId} className="check-title">
            {title}
          </label>
          {a ? (
            <p className="meta">
              {assignmentByline(a)}
              {a.kind ? `${assignmentByline(a) ? " · " : ""}${kindLabel(a.kind)}` : ""}
              {item.added ? " · Added" : ""}
              {due && <span className="due-on"> · Due {formatDay(due)}</span>}
            </p>
          ) : (
            <p className="meta">This item isn't in the catalog anymore. You can remove it.</p>
          )}
        </div>
        {a && <TierBadge tier={a.reuse_tier} verified={a.license_verified} />}
      </div>
      <div className="row-actions">
        {a?.url && <ExternalLink href={a.url} title={title} />}
        {a?.summary && (
          <button type="button" className="row-action" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide details" : "Details"}
            <span className="visually-hidden"> for {title}</span>
          </button>
        )}
        {!readOnly && (
          <button type="button" className="row-action row-action-danger" onClick={(e) => removeWithFocus(e, onRemove)}>
            Remove<span className="visually-hidden"> {title}</span>
          </button>
        )}
      </div>
      {open && a?.summary && (
        <p id={detailsId} className="row-details">
          {a.summary}
        </p>
      )}
    </li>
  );
}
