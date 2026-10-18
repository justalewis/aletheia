import { useEffect, useId, useState } from "react";
import { api, TIERS, type AssignmentBrief, type AssignmentRole, type ReadingBrief, type StateUnit } from "../api";
import { Drawer } from "../components/Drawer";
import { Empty, ErrorMessage, Loading } from "../components/Status";
import { TierBadge, TIER_LABELS } from "../components/TierBadge";
import { TopicChips } from "../components/TopicChips";
import {
  assignmentByline,
  kindLabel,
  KINDS,
  levelLabel,
  LEVELS,
  readingByline,
  roleForKind,
  ROLE_LABELS,
  SHORT_LEVELS,
  SHORT_LEVELS_LABEL,
} from "../components/meta";
import { focusesOf, useDebounced, usePack } from "../hooks";

export interface SearchTarget {
  unitKey: string;
  kind: "reading" | "assignment";
}

interface Props {
  target: SearchTarget | null;
  unit: StateUnit | null;
  focus: string;
  onClose: () => void;
  onAddReading: (r: ReadingBrief) => void;
  onAddAssignment: (a: AssignmentBrief, role: AssignmentRole) => void;
}

const PAGE = 20;

export function SearchDrawer({ target, unit, focus, onClose, onAddReading, onAddAssignment }: Props) {
  const open = Boolean(target && unit);
  const kindWord = target?.kind === "assignment" ? "assignment" : "reading";
  return (
    <Drawer open={open} onClose={onClose} title={unit ? `Add ${kindWord} to ${unit.title}` : `Add ${kindWord}`} wide>
      {target && unit && (
        <SearchBody
          key={`${target.unitKey}:${target.kind}`}
          kind={target.kind}
          unit={unit}
          focus={focus}
          onAddReading={onAddReading}
          onAddAssignment={onAddAssignment}
        />
      )}
    </Drawer>
  );
}

function SearchBody({
  kind,
  unit,
  focus: buildFocus,
  onAddReading,
  onAddAssignment,
}: {
  kind: "reading" | "assignment";
  unit: StateUnit;
  focus: string;
  onAddReading: (r: ReadingBrief) => void;
  onAddAssignment: (a: AssignmentBrief, role: AssignmentRole) => void;
}) {
  const id = useId();
  const pack = usePack();
  const unitTopics = unit.topics ?? [];
  const [q, setQ] = useState("");
  const [topics, setTopics] = useState<string[]>(unitTopics.slice(0, 1));
  const [focus, setFocus] = useState(buildFocus);
  const [tiers, setTiers] = useState<string[]>([]);
  const [level, setLevel] = useState(SHORT_LEVELS);
  const [akind, setAkind] = useState("");
  const [role, setRole] = useState<AssignmentRole | "auto">("auto");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<(ReadingBrief | AssignmentBrief)[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [nonce, setNonce] = useState(0);
  const [announce, setAnnounce] = useState("");
  const dq = useDebounced(q, 300);

  const filterKey = JSON.stringify([kind, dq, topics, focus, tiers, level, akind]);
  useEffect(() => {
    setOffset(0);
  }, [filterKey]);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    const common = { q: dq, topic: topics.join(","), focus, tier: tiers.join(","), limit: PAGE, offset };
    const req =
      kind === "reading" ? api.readings({ ...common, level }, ctrl.signal) : api.assignments({ ...common, kind: akind }, ctrl.signal);
    req
      .then((page) => {
        if (ctrl.signal.aborted) return;
        setTotal(page.total);
        setItems((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
        setLoading(false);
      })
      .catch((e: Error) => {
        if (ctrl.signal.aborted || e.name === "AbortError") return;
        setError(e);
        setLoading(false);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, offset, nonce]);

  const existing = new Set(kind === "reading" ? unit.readings.map((r) => r.id) : unit.assignments.map((a) => a.id));
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const focuses = focusesOf(pack.data);

  const add = (item: ReadingBrief | AssignmentBrief) => {
    if (kind === "reading") {
      onAddReading(item as ReadingBrief);
      setAnnounce(`Added ${item.title}.`);
    } else {
      const a = item as AssignmentBrief;
      const r = role === "auto" ? roleForKind(a.kind) : role;
      onAddAssignment(a, r);
      setAnnounce(`Added ${a.title} under ${ROLE_LABELS[r]}.`);
    }
  };

  return (
    <div className="search-body">
      <div className="search-filters">
        <div className="field">
          <label htmlFor={`${id}-q`}>Search {kind === "reading" ? "readings" : "assignments"}</label>
          <input
            id={`${id}-q`}
            type="search"
            data-autofocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={kind === "reading" ? "Title, author, or topic" : "Title, source, or topic"}
          />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor={`${id}-focus`}>Focus</label>
            <select id={`${id}-focus`} value={focus} onChange={(e) => setFocus(e.target.value)}>
              <option value="">All focuses</option>
              {focuses.map((f) => (
                <option key={f.code} value={f.code}>
                  {f.label}
                </option>
              ))}
            </select>
          </div>
          {kind === "reading" ? (
            <div className="field">
              <label htmlFor={`${id}-level`}>Type</label>
              <select id={`${id}-level`} value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value={SHORT_LEVELS}>{SHORT_LEVELS_LABEL}</option>
                <option value="">Any type, including whole books</option>
                {LEVELS.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <>
              <div className="field">
                <label htmlFor={`${id}-kind`}>Kind</label>
                <select id={`${id}-kind`} value={akind} onChange={(e) => setAkind(e.target.value)}>
                  <option value="">Any kind</option>
                  {KINDS.map((k) => (
                    <option key={k.value} value={k.value}>
                      {k.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor={`${id}-role`}>Add as</label>
                <select id={`${id}-role`} value={role} onChange={(e) => setRole(e.target.value as AssignmentRole | "auto")}>
                  <option value="auto">Match its kind</option>
                  <option value="major">{ROLE_LABELS.major}</option>
                  <option value="short_term">{ROLE_LABELS.short_term}</option>
                  <option value="activity">Activity</option>
                </select>
              </div>
            </>
          )}
        </div>
        <fieldset className="field-checks inline">
          <legend>Reuse</legend>
          {TIERS.map((t) => (
            <label key={t} className="check">
              <input type="checkbox" checked={tiers.includes(t)} onChange={() => setTiers((x) => toggle(x, t))} />
              {TIER_LABELS[t]}
            </label>
          ))}
        </fieldset>
        {unitTopics.length > 0 && (
          <div className="field">
            <p className="label">Unit topics{topics.length > 1 ? " (items must match all)" : ""}</p>
            <TopicChips
              label="Unit topics"
              topics={unitTopics.map((t) => ({ topic: t }))}
              selected={topics}
              onToggle={(t) => setTopics((x) => toggle(x, t))}
            />
          </div>
        )}
      </div>

      <p className="visually-hidden" aria-live="polite">
        {announce}
      </p>
      <p className="result-count" aria-live="polite">
        {total === null ? "" : total === 0 ? "Nothing matches." : `${total} ${total === 1 ? "match" : "matches"}`}
      </p>
      {error && <ErrorMessage error={error} onRetry={() => setNonce((n) => n + 1)} />}
      {!loading && total === 0 && !error && (
        <Empty>
          <p>Try another topic, all focuses, or a shorter search.</p>
        </Empty>
      )}
      <ul className="result-list compact">
        {items.map((item) => (
          <SearchResult key={item.id} item={item} kind={kind} added={existing.has(item.id)} onAdd={() => add(item)} />
        ))}
      </ul>
      {loading && <Loading label="Searching…" />}
      {!loading && total !== null && items.length < total && (
        <button type="button" className="btn" onClick={() => setOffset(items.length)}>
          Show more
        </button>
      )}
    </div>
  );
}

function SearchResult({
  item,
  kind,
  added,
  onAdd,
}: {
  item: ReadingBrief | AssignmentBrief;
  kind: "reading" | "assignment";
  added: boolean;
  onAdd: () => void;
}) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const r = kind === "reading" ? (item as ReadingBrief) : null;
  const a = kind === "assignment" ? (item as AssignmentBrief) : null;
  const byline = r ? readingByline(r) : a ? assignmentByline(a) : "";
  const extra = r ? `${levelLabel(r.level)} · ~${r.est_pages} pp.` : a?.kind ? kindLabel(a.kind) : "";
  return (
    <li className="result">
      <div className="result-row">
        <div className="result-main">
          <p className="result-title">{item.title}</p>
          <p className="meta">
            {byline}
            {byline && extra ? " · " : ""}
            {extra}
          </p>
          <p className="result-badges">
            <TierBadge tier={item.reuse_tier} verified={item.license_verified} />
            {item.url && (
              <a className="row-action" href={item.url} target="_blank" rel="noopener noreferrer">
                Open<span className="visually-hidden"> {item.title} (opens in a new tab)</span>
                <span aria-hidden="true"> ↗</span>
              </a>
            )}
            {item.summary && (
              <button
                type="button"
                className="row-action"
                aria-expanded={open}
                aria-controls={detailsId}
                onClick={() => setOpen((o) => !o)}
              >
                {open ? "Hide details" : "Details"}
                <span className="visually-hidden"> for {item.title}</span>
              </button>
            )}
          </p>
        </div>
        <button type="button" className={`btn btn-small${added ? "" : " btn-primary"}`} disabled={added} onClick={onAdd}>
          {added ? "Added" : "Add"}
          <span className="visually-hidden"> {item.title}</span>
        </button>
      </div>
      {open && item.summary && (
        <p id={detailsId} className="row-details">
          {item.summary}
        </p>
      )}
    </li>
  );
}
