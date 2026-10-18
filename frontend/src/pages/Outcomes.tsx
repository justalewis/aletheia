import { useId, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, TIERS, type ItemMatch, type OutcomeRecommendation, type OutcomeSetBrief, type Term } from "../api";
import { MatchCard } from "../components/MatchCard";
import { Empty, ErrorMessage, Loading } from "../components/Status";
import { presetToTerm } from "../components/TermPicker";
import { TIER_LABELS } from "../components/TierBadge";
import { LEVELS, SHORT_LEVELS, SHORT_LEVELS_LABEL } from "../components/meta";
import { focusesOf, presetsOf, useAsync, useDocumentTitle, usePack } from "../hooks";
import { addToDraft, currentDraft, draftLink, OUTCOMES_UNIT_TITLE, setLabel } from "../outcomes";
import "../outcomes.css";

const PER_OUTCOME = 8;

function list(v: string | null): string[] {
  return (v ?? "").split(",").filter(Boolean);
}

interface AddContext {
  setId: string;
  focus: string;
  term: Term;
  onAdded: () => void;
}

/** The unit the instructor last added to on this page, so the menu starts there. */
let lastUnitKey = "";

export default function Outcomes() {
  useDocumentTitle("Readings by outcome");
  const pack = usePack();
  const [params, setParams] = useSearchParams();
  const setId = params.get("set") ?? "";
  const focus = params.get("focus") ?? "";
  const tiers = list(params.get("tier"));
  const level = params.get("level") ?? "";
  // Bumped after each add so the draft line below re-reads the draft from storage.
  const [, setDraftNonce] = useState(0);
  const groupId = useId();

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  };
  const toggleTier = (t: string) => {
    const s = new Set(tiers);
    if (s.has(t)) s.delete(t);
    else s.add(t);
    update({ tier: [...s].join(",") || null });
  };

  const sets = useAsync((signal) => api.outcomeSets(signal), []);
  const results = useAsync(
    (signal) =>
      setId
        ? api.recommend(
            { outcome_set_id: setId, focus, tiers, levels: list(level), per_outcome: PER_OUTCOME },
            signal,
          )
        : Promise.resolve(null),
    [setId, focus, tiers.join(","), level],
  );

  const all = sets.data ?? [];
  const frameworks = all.filter((s) => s.kind === "framework");
  const courses = all.filter((s) => s.kind !== "framework");
  const focuses = focusesOf(pack.data);
  const addCtx: AddContext = {
    setId,
    focus,
    term: presetToTerm(presetsOf(pack.data)[0]),
    onAdded: () => setDraftNonce((n) => n + 1),
  };
  const draft = currentDraft();
  const chosen = all.find((s) => s.id === setId);
  const data = results.data;

  return (
    <div className="container page">
      <h1>Readings by outcome</h1>
      <p className="prose">
        Choose a set of outcomes to get a reading list for each one. Aletheia matches catalog items to outcomes through
        their topic tags and through the sample courses that already use them for an outcome, then favors openly
        licensed chapters and articles written for students.
      </p>

      <section className="panel" aria-label="Choose outcomes and filters">
        <fieldset className="field-checks outcome-set-picker">
          <legend>Outcome set</legend>
          {sets.loading && <Loading label="Loading outcome sets…" />}
          {sets.error && <ErrorMessage error={sets.error} onRetry={sets.reload} />}
          {frameworks.length > 0 && (
            <div role="group" aria-labelledby={`${groupId}-fw`}>
              <p id={`${groupId}-fw`} className="h-tiny">
                Frameworks
              </p>
              <div className="radio-grid">
                {frameworks.map((s) => (
                  <SetOption key={s.id} set={s} checked={setId === s.id} onPick={() => update({ set: s.id })} />
                ))}
              </div>
            </div>
          )}
          {courses.length > 0 && (
            <div role="group" aria-labelledby={`${groupId}-course`}>
              <p id={`${groupId}-course`} className="h-tiny">
                Course outcomes
              </p>
              <div className="radio-grid">
                {courses.map((s) => (
                  <SetOption key={s.id} set={s} checked={setId === s.id} onPick={() => update({ set: s.id })} />
                ))}
              </div>
            </div>
          )}
        </fieldset>

        <div className="field-row outcome-filters">
          <div className="field">
            <label htmlFor="outcomes-focus">Focus</label>
            <select id="outcomes-focus" value={focus} onChange={(e) => update({ focus: e.target.value || null })}>
              <option value="">All focuses</option>
              {focuses.map((f) => (
                <option key={f.code} value={f.code}>
                  {f.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="outcomes-level">Type of reading</label>
            <select id="outcomes-level" value={level} onChange={(e) => update({ level: e.target.value || null })}>
              <option value="">Any type</option>
              <option value={SHORT_LEVELS}>{SHORT_LEVELS_LABEL}</option>
              {LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
          <fieldset className="field-checks inline">
            <legend>Reuse</legend>
            {TIERS.map((t) => (
              <label key={t} className="check">
                <input type="checkbox" checked={tiers.includes(t)} onChange={() => toggleTier(t)} />
                {TIER_LABELS[t]}
              </label>
            ))}
          </fieldset>
        </div>
      </section>

      <p className="hint draft-note">
        {draft ? (
          <>
            Add to course puts an item in your draft course, “{draft.state.title || "Untitled course"}” (
            {draft.state.units.length} {draft.state.units.length === 1 ? "unit" : "units"}).{" "}
            <Link to={draftLink(draft)}>Open the draft</Link>
          </>
        ) : (
          <>
            You don’t have a draft course in this browser yet. The first item you add starts a blank one with a unit
            called {OUTCOMES_UNIT_TITLE}.
          </>
        )}
      </p>

      <section aria-labelledby="outcome-results-title" className="outcome-results">
        <h2 id="outcome-results-title" className="visually-hidden">
          Results
        </h2>
        {!setId && (
          <Empty>
            <p>Choose an outcome set above to see readings for each outcome.</p>
          </Empty>
        )}
        {setId && results.loading && <Loading label="Finding readings…" />}
        {results.error && <ErrorMessage error={results.error} onRetry={results.reload} />}
        {data && !results.loading && (
          <>
            <p className="result-count" aria-live="polite">
              {data.outcomes.length === 0
                ? ""
                : `${data.outcomes.length} ${data.outcomes.length === 1 ? "outcome" : "outcomes"} in ${chosen ? setLabel(chosen) : "this set"}`}
            </p>
            {data.outcome_set?.source_url && (
              <p className="hint">
                Outcome text from{" "}
                <a href={data.outcome_set.source_url} target="_blank" rel="noopener noreferrer">
                  {data.outcome_set.title}
                  <span className="visually-hidden"> (opens in a new tab)</span>
                </a>
                .
              </p>
            )}
            {data.outcomes.length === 0 && (
              <Empty>
                <p>This course’s outcomes aren’t loaded yet, so there’s nothing to match against.</p>
              </Empty>
            )}
            {data.outcomes.map((o) => (
              <OutcomeBlock key={o.id} outcome={o} ctx={addCtx} />
            ))}
          </>
        )}
      </section>
    </div>
  );
}

function SetOption({ set, checked, onPick }: { set: OutcomeSetBrief; checked: boolean; onPick: () => void }) {
  const empty = set.count === 0;
  const hintId = useId();
  return (
    <label className={`radio-card set-option${empty ? " is-disabled" : ""}`}>
      <input
        type="radio"
        name="outcome-set"
        value={set.id}
        checked={checked}
        disabled={empty}
        aria-describedby={hintId}
        onChange={onPick}
      />
      <span>
        <span className="set-name">{setLabel(set)}</span>
        <span id={hintId} className="set-hint">
          {empty
            ? "outcomes not loaded yet"
            : set.kind === "framework"
              ? `${set.count} ${set.count === 1 ? "outcome" : "outcomes"}`
              : `${set.title}, ${set.count} ${set.count === 1 ? "outcome" : "outcomes"}`}
        </span>
      </span>
    </label>
  );
}

function OutcomeBlock({ outcome: o, ctx }: { outcome: OutcomeRecommendation; ctx: AddContext }) {
  const id = useId();
  const more = o.total.readings - o.readings.length;
  return (
    <section className="panel outcome-block" aria-labelledby={id}>
      <h2 id={id} className="outcome-label">
        {o.label}
      </h2>
      {o.text && <p className="outcome-text">{o.text}</p>}
      <p className="meta">
        {o.total.readings === 0
          ? "No readings match with these filters."
          : `${o.total.readings} matching ${o.total.readings === 1 ? "reading" : "readings"}${more > 0 ? `; the top ${o.readings.length} are below` : ""}.`}
      </p>
      {o.readings.length > 0 && (
        <ul className="result-list compact">
          {o.readings.map((r) => (
            <MatchCard key={r.id} item={r} heading="h3">
              <AddToCourse item={r} ctx={ctx} />
            </MatchCard>
          ))}
        </ul>
      )}
      {o.assignments.length > 0 && (
        <details className="outcome-assignments">
          <summary>
            Assignments ({o.assignments.length}
            {o.total.assignments > o.assignments.length ? ` of ${o.total.assignments}` : ""})
          </summary>
          <ul className="result-list compact">
            {o.assignments.map((a) => (
              <MatchCard key={a.id} item={a} heading="h3">
                <AddToCourse item={a} ctx={ctx} />
              </MatchCard>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function AddToCourse({ item, ctx }: { item: ItemMatch; ctx: AddContext }) {
  const [open, setOpen] = useState(false);
  const [units, setUnits] = useState<{ key: string; title: string }[]>([]);
  const [unitKey, setUnitKey] = useState("");
  const [msg, setMsg] = useState<{ text: string; link: string } | null>(null);
  const id = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);

  const add = (key?: string) => {
    const r = addToDraft(item, { unitKey: key, term: ctx.term, focus: ctx.focus, outcomeSetId: ctx.setId || undefined });
    lastUnitKey = r.unitKey;
    setMsg({
      text: r.created
        ? `Added to a new draft course, in a unit called ${r.unitTitle}.`
        : r.already
          ? `Already checked in ${r.unitTitle}.`
          : `Added to ${r.unitTitle}.`,
      link: draftLink(r.draft),
    });
    setOpen(false);
    buttonRef.current?.focus();
    ctx.onAdded();
  };

  const start = () => {
    if (open) {
      setOpen(false);
      return;
    }
    const d = currentDraft();
    if (!d || d.state.units.length === 0) {
      add();
      return;
    }
    const us = d.state.units.map((u, i) => ({ key: u.key, title: u.title || `Unit ${i + 1}` }));
    setUnits(us);
    setUnitKey(us.some((u) => u.key === lastUnitKey) ? lastUnitKey : us[0].key);
    setOpen(true);
  };

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    }
  };

  return (
    <div className="add-to-course">
      <button
        ref={buttonRef}
        type="button"
        className="btn btn-small"
        aria-expanded={open}
        aria-controls={open ? `${id}-form` : undefined}
        onClick={start}
      >
        Add to course<span className="visually-hidden">: {item.title}</span>
      </button>
      {open && (
        <form
          id={`${id}-form`}
          className="add-form"
          onKeyDown={onKey}
          onSubmit={(e) => {
            e.preventDefault();
            add(unitKey);
          }}
        >
          <label htmlFor={`${id}-unit`}>Unit</label>
          <select id={`${id}-unit`} value={unitKey} onChange={(e) => setUnitKey(e.target.value)} autoFocus>
            {units.map((u) => (
              <option key={u.key} value={u.key}>
                {u.title}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn-small btn-primary">
            Add
          </button>
          <button type="button" className="btn btn-small btn-quiet" onClick={() => setOpen(false)}>
            Cancel
          </button>
        </form>
      )}
      <p role="status" className="add-status">
        {msg && (
          <>
            {msg.text} <Link to={msg.link}>Open the draft</Link>
          </>
        )}
      </p>
    </div>
  );
}
