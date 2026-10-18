import { useEffect, useId, useRef, useState } from "react";
import {
  api,
  briefCache,
  type BuildState,
  type Coverage,
  type CoverageCell,
  type CoverageRow,
  type CoverageStatus,
  type ItemMatch,
  type OutcomeSetBrief,
  type StateUnit,
} from "../api";
import { MatchCard } from "../components/MatchCard";
import { ErrorMessage, Loading } from "../components/Status";
import { useAsync } from "../hooks";
import { addToUnit, setLabel } from "../outcomes";
import "../outcomes.css";

interface Props {
  state: BuildState;
  readOnly: boolean;
  onEdit: (fn: (s: BuildState) => BuildState) => void;
}

const STATUS_TEXT: Record<CoverageStatus, string> = { ok: "Covered", weak: "Thin", none: "Gap" };

/** Which outcomes the checked items address, unit by unit, with suggestions for the gaps. */
export function OutcomesPanel({ state, readOnly, onEdit }: Props) {
  const headingId = useId();
  const sets = useAsync((signal) => api.outcomeSets(signal), []);
  // A shared (view-only) course can still be checked against other outcome sets; that choice isn't saved.
  const [localSets, setLocalSets] = useState<string[] | null>(null);
  const chosen = readOnly ? localSets ?? state.outcome_set_ids ?? [] : state.outcome_set_ids ?? [];

  const toggle = (id: string) => {
    const next = chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id];
    if (readOnly) setLocalSets(next);
    else onEdit((s) => ({ ...s, outcome_set_ids: next }));
  };

  /* Coverage, debounced so ticking boxes doesn't send a request per click. */
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const stateRef = useRef(state);
  stateRef.current = state;
  const statusRef = useRef<HTMLParagraphElement>(null);
  const refocus = useRef(false);
  const key = JSON.stringify({
    sets: chosen,
    focus: state.focus,
    units: state.units.map((u) => [
      u.key,
      u.title,
      u.topics ?? [],
      u.outcomes,
      u.readings.filter((r) => r.selected).map((r) => r.id),
      u.assignments.filter((a) => a.selected).map((a) => a.id),
    ]),
  });

  useEffect(() => {
    if (!chosen.length) {
      setCoverage(null);
      setError(null);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    const t = window.setTimeout(() => {
      api
        .coverage(stateRef.current, chosen, ctrl.signal)
        .then((c) => {
          setCoverage(c);
          setError(null);
          setLoading(false);
          // If the refresh removed the card (or gap) that had focus, land on the status line instead.
          window.setTimeout(() => {
            if (refocus.current && document.activeElement === document.body) statusRef.current?.focus({ preventScroll: true });
            refocus.current = false;
          }, 0);
        })
        .catch((e: Error) => {
          if (ctrl.signal.aborted || e.name === "AbortError") return;
          setError(e);
          setLoading(false);
        });
    }, 700);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
    // key covers everything coverage depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  /* Adding a suggestion. AddToUnit moves focus to its gap's summary, since the card itself goes
     away once coverage refreshes. */
  const [added, setAdded] = useState("");
  const addSuggestion = (item: ItemMatch, unitKey: string) => {
    const unit = state.units.find((u) => u.key === unitKey);
    if (!unit) return;
    onEdit((s) => addToUnit(s, unitKey, item));
    setAdded(`Added “${item.title}” to ${unit.title}. Coverage updates in a moment.`);
    refocus.current = true;
  };

  // Frameworks first, then course outcome sets.
  const available = [...(sets.data ?? [])].sort(
    (a, b) => Number(a.kind !== "framework") - Number(b.kind !== "framework"),
  );

  return (
    <section className="panel outcomes-panel" aria-labelledby={headingId}>
      <h2 id={headingId} className="h-small">
        Outcomes coverage
      </h2>
      <p className="hint">See which outcomes your checked readings and assignments address in each unit, and where the gaps are.</p>

      <fieldset className="field-checks inline outcome-set-checks">
        <legend>Check this course against</legend>
        {sets.loading && <Loading label="Loading outcome sets…" />}
        {sets.error && <ErrorMessage error={sets.error} onRetry={sets.reload} />}
        {available.map((s) => (
          <SetCheck key={s.id} set={s} checked={chosen.includes(s.id)} onToggle={() => toggle(s.id)} />
        ))}
      </fieldset>
      {readOnly && localSets && <p className="hint">Your choice here isn’t saved with the shared course.</p>}

      {chosen.length === 0 && <p className="hint">Choose one or more outcome sets to see coverage.</p>}
      {chosen.length > 0 && loading && !coverage && <Loading label="Checking coverage…" />}
      {error && <ErrorMessage title="Coverage didn’t load." error={error} onRetry={() => setNonce((n) => n + 1)} />}
      {chosen.length > 0 && coverage && (
        <div className={loading ? "is-stale" : undefined} aria-busy={loading}>
          <CoverageView coverage={coverage} sets={available} />
          <Gaps coverage={coverage} units={state.units} readOnly={readOnly} onAdd={addSuggestion} />
        </div>
      )}
      <p ref={statusRef} tabIndex={-1} role="status" className="add-status">
        {added}
      </p>
    </section>
  );
}

function SetCheck({ set, checked, onToggle }: { set: OutcomeSetBrief; checked: boolean; onToggle: () => void }) {
  const empty = set.count === 0;
  return (
    <label className={`check${empty ? " is-disabled" : ""}`}>
      <input type="checkbox" checked={checked && !empty} disabled={empty} onChange={onToggle} />
      <span>
        {setLabel(set)}
        {empty && <span className="set-hint"> (outcomes not loaded yet)</span>}
      </span>
    </label>
  );
}

function StatusBadge({ row }: { row: CoverageRow }) {
  return (
    <span className={`cov-status cov-status-${row.status}`}>
      {STATUS_TEXT[row.status]}
      {row.status !== "none" ? ` (${row.total})` : ""}
    </span>
  );
}

function cellLevel(n: number): number {
  return n === 0 ? 0 : n === 1 ? 1 : n <= 3 ? 2 : 3;
}

function cellTitles(c: CoverageCell): string {
  const names = [
    ...c.reading_ids.map((id) => briefCache.readings.get(id)?.title ?? id),
    ...c.assignment_ids.map((id) => briefCache.assignments.get(id)?.title ?? id),
  ];
  return names.join("\n");
}

function CoverageView({ coverage, sets }: { coverage: Coverage; sets: OutcomeSetBrief[] }) {
  const { units, outcomes: rows } = coverage;
  const multi = new Set(rows.map((r) => r.set_id)).size > 1;
  const setName = (id: string) => {
    const s = sets.find((x) => x.id === id) ?? coverage.sets.find((x) => x.id === id);
    return s ? setLabel(s) : id;
  };

  if (coverage.unknown_set_ids.length > 0 && rows.length === 0) {
    return <p className="hint">The outcome sets saved with this course aren’t in the catalog anymore.</p>;
  }
  if (rows.length === 0) {
    return <p className="hint">These outcome sets don’t have outcomes loaded yet, so there’s nothing to check.</p>;
  }
  if (units.length === 0) {
    return <p className="hint">This course has no units yet. Add a unit and check some items to see coverage.</p>;
  }

  return (
    <>
      <div className="coverage-wide table-scroll" role="region" aria-label="Coverage by unit" tabIndex={0}>
        <table className="coverage-table">
          <caption className="visually-hidden">Checked items that address each outcome, by unit</caption>
          <thead>
            <tr>
              <th scope="col">Outcome</th>
              {units.map((u, i) => (
                <th scope="col" key={u.key} className="cov-unit">
                  <span className="cov-unit-num">Unit {i + 1}</span>
                  <span className="cov-unit-title">{u.title}</span>
                </th>
              ))}
              <th scope="col">Overall</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.set_id}:${r.id}`}>
                <th scope="row" className="cov-outcome">
                  <span title={r.text}>{r.label}</span>
                  {multi && <span className="cov-set">{setName(r.set_id)}</span>}
                </th>
                {r.cells.map((c) => (
                  <td key={c.unit_key} className={`cov-cell cov-${cellLevel(c.count)}`} title={cellTitles(c) || undefined}>
                    {c.count}
                    <span className="visually-hidden"> {c.count === 1 ? "item" : "items"}</span>
                  </td>
                ))}
                <td className="cov-total">
                  <StatusBadge row={r} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="coverage-narrow">
        {rows.map((r) => (
          <li key={`${r.set_id}:${r.id}`}>
            <p className="cov-narrow-head">
              <strong>{r.label}</strong> <StatusBadge row={r} />
              {multi && <span className="cov-set">{setName(r.set_id)}</span>}
            </p>
            {r.total > 0 ? (
              <ul className="cov-narrow-units">
                {r.cells
                  .map((c, i) => ({ c, u: units[i] }))
                  .filter(({ c }) => c.count > 0)
                  .map(({ c, u }) => (
                    <li key={c.unit_key}>
                      {u?.title ?? c.unit_key}: {c.count} {c.count === 1 ? "item" : "items"}
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="hint">No checked items address this yet.</p>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

function Gaps({
  coverage,
  units,
  readOnly,
  onAdd,
}: {
  coverage: Coverage;
  units: StateUnit[];
  readOnly: boolean;
  onAdd: (item: ItemMatch, unitKey: string) => void;
}) {
  const id = useId();
  if (!coverage.gaps.length) {
    return coverage.outcomes.length > 0 && coverage.units.length > 0 ? (
      <p className="notice notice-ok small">Every outcome has at least two checked items.</p>
    ) : null;
  }
  return (
    <div className="gaps">
      <h3 id={id} className="h-tiny">
        Gaps and thin spots
      </h3>
      {coverage.gaps.map((g) => (
        <details key={`${g.set_id}:${g.outcome_id}`} className="gap">
          <summary>
            <span className="gap-label">{g.label}</span>:{" "}
            {g.status === "none" ? "nothing checked addresses it yet" : `only ${g.total} checked ${g.total === 1 ? "item" : "items"}`}
            {g.unit_title ? `. Suggestions for the ${g.unit_title} unit` : ""}
          </summary>
          {g.suggestions.length === 0 ? (
            <p className="hint">Nothing in the catalog is left to suggest for this one.</p>
          ) : (
            <ul className="result-list compact">
              {g.suggestions.map((s) => (
                <MatchCard key={`${s.item_type}:${s.id}`} item={s} heading="h4">
                  {!readOnly && units.length > 0 && (
                    <AddToUnit item={s} units={units} defaultKey={g.unit_key} onAdd={onAdd} />
                  )}
                </MatchCard>
              ))}
            </ul>
          )}
          {!readOnly && units.length === 0 && <p className="hint">Add a unit to put suggestions in.</p>}
        </details>
      ))}
    </div>
  );
}

function AddToUnit({
  item,
  units,
  defaultKey,
  onAdd,
}: {
  item: ItemMatch;
  units: StateUnit[];
  defaultKey: string | null;
  onAdd: (item: ItemMatch, unitKey: string) => void;
}) {
  const id = useId();
  const [unitKey, setUnitKey] = useState(
    defaultKey && units.some((u) => u.key === defaultKey) ? defaultKey : units[0].key,
  );
  return (
    <form
      className="add-form"
      onSubmit={(e) => {
        e.preventDefault();
        const summary = e.currentTarget.closest("details")?.querySelector("summary");
        onAdd(item, unitKey);
        summary?.focus();
      }}
    >
      <label htmlFor={id}>Add to</label>
      <select id={id} value={unitKey} onChange={(e) => setUnitKey(e.target.value)}>
        {units.map((u) => (
          <option key={u.key} value={u.key}>
            {u.title}
          </option>
        ))}
      </select>
      <button type="submit" className="btn btn-small">
        Add<span className="visually-hidden">: {item.title}</span>
      </button>
    </form>
  );
}
