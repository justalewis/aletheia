import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, TIERS, type AssignmentBrief, type Paged, type ReadingBrief } from "../api";
import { Drawer } from "../components/Drawer";
import { ItemDetail, type DetailRef } from "../components/ItemDetail";
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
  SHORT_LEVELS,
  SHORT_LEVELS_LABEL,
} from "../components/meta";
import { focusesOf, useAsync, useDocumentTitle, usePack } from "../hooks";

const PAGE_SIZE = 25;
const fmt = new Intl.NumberFormat("en-US");

type Tab = "readings" | "assignments";

function list(v: string | null): string[] {
  return (v ?? "").split(",").filter(Boolean);
}

export default function Catalog() {
  useDocumentTitle("Catalog");
  const pack = usePack();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "assignments" ? "assignments" : "readings";
  const focus = params.get("focus") ?? "";
  const topics = list(params.get("topic"));
  const tiers = list(params.get("tier"));
  const level = params.get("level") ?? "";
  const kind = params.get("kind") ?? "";
  const q = params.get("q") ?? "";
  const page = Math.max(1, parseInt(params.get("page") ?? "1", 10) || 1);

  const [qText, setQText] = useState(q);
  useEffect(() => setQText(q), [q]);

  const [stack, setStack] = useState<DetailRef[]>([]);
  const current = stack[stack.length - 1];
  // On phones the filters start folded up so results are visible without scrolling past them.
  const [showFilters, setShowFilters] = useState(() => {
    try {
      return window.matchMedia("(min-width: 900px)").matches;
    } catch {
      return true;
    }
  });
  const resultsRef = useRef<HTMLHeadingElement>(null);

  const update = (changes: Record<string, string | null>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    if (!keepPage) next.delete("page");
    setParams(next);
  };

  const toggleIn = (key: string, values: string[], v: string) => {
    const set = new Set(values);
    if (set.has(v)) set.delete(v);
    else set.add(v);
    update({ [key]: [...set].join(",") || null });
  };

  const topicList = useAsync((signal) => api.topics(focus, tab, signal), [focus, tab]);

  const results = useAsync<Paged<ReadingBrief | AssignmentBrief>>(
    (signal) => {
      const common = { focus, topic: topics.join(","), tier: tiers.join(","), q, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE };
      return tab === "readings" ? api.readings({ ...common, level }, signal) : api.assignments({ ...common, kind }, signal);
    },
    [tab, focus, topics.join(","), tiers.join(","), level, kind, q, page],
  );

  const focuses = focusesOf(pack.data);
  const courseFocuses = focuses.filter((f) => !f.tag_only);
  const tagFocuses = focuses.filter((f) => f.tag_only);
  const totalPages = results.data ? Math.max(1, Math.ceil(results.data.total / PAGE_SIZE)) : 1;
  const hasFilters = Boolean(focus || topics.length || tiers.length || level || kind || q);

  // Tabs: arrow keys move between them (WAI-ARIA tabs pattern).
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const tabs: { id: Tab; label: string }[] = [
    { id: "readings", label: "Readings" },
    { id: "assignments", label: "Assignments" },
  ];
  const selectTab = (t: Tab) => {
    const next = new URLSearchParams();
    if (focus) next.set("focus", focus);
    if (q) next.set("q", q);
    if (tiers.length) next.set("tier", tiers.join(","));
    if (t === "assignments") next.set("tab", "assignments");
    setParams(next);
  };
  const onTabKey = (e: KeyboardEvent, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const j = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
    tabRefs.current[j]?.focus();
    selectTab(tabs[j].id);
  };

  const goToPage = (p: number) => {
    update({ page: String(p) }, true);
    resultsRef.current?.closest("section")?.scrollIntoView({ block: "start" });
    resultsRef.current?.focus({ preventScroll: true });
  };
  const filterCount = (focus ? 1 : 0) + topics.length + tiers.length + (level ? 1 : 0) + (kind ? 1 : 0) + (q ? 1 : 0);

  const start = results.data && results.data.total > 0 ? (page - 1) * PAGE_SIZE + 1 : 0;
  const end = results.data ? Math.min(page * PAGE_SIZE, results.data.total) : 0;

  return (
    <div className="container page">
      <h1>Catalog</h1>
      <p className="prose">
        Openly licensed readings and assignments for writing courses. Filter by focus, topic, or how you're allowed to
        reuse them.
      </p>

      <div className="tabs" role="tablist" aria-label="What to browse">
        {tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="catalog-panel"
            tabIndex={tab === t.id ? 0 : -1}
            className="tab"
            onClick={() => selectTab(t.id)}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div id="catalog-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="catalog-layout">
        <button
          type="button"
          className="btn filters-toggle"
          aria-expanded={showFilters}
          aria-controls="catalog-filters"
          onClick={() => setShowFilters((s) => !s)}
        >
          {showFilters ? "Hide filters" : "Show filters"}
          {filterCount > 0 ? ` (${filterCount} on)` : ""}
        </button>
        <aside id="catalog-filters" className={`filters${showFilters ? "" : " is-collapsed"}`} aria-label="Filters">
          <form
            role="search"
            className="field"
            onSubmit={(e) => {
              e.preventDefault();
              update({ q: qText.trim() || null });
            }}
          >
            <label htmlFor="catalog-q">Search {tab}</label>
            <div className="input-row">
              <input id="catalog-q" type="search" value={qText} onChange={(e) => setQText(e.target.value)} />
              <button type="submit" className="btn">
                Search
              </button>
            </div>
          </form>

          <div className="field">
            <label htmlFor="catalog-focus">Focus</label>
            <select id="catalog-focus" value={focus} onChange={(e) => update({ focus: e.target.value || null, topic: null })}>
              <option value="">All focuses</option>
              <optgroup label="Courses">
                {courseFocuses.map((f) => (
                  <option key={f.code} value={f.code}>
                    {f.label}
                  </option>
                ))}
              </optgroup>
              {tagFocuses.length > 0 && (
                <optgroup label="Themes">
                  {tagFocuses.map((f) => (
                    <option key={f.code} value={f.code}>
                      {f.label}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
          </div>

          {tab === "readings" ? (
            <div className="field">
              <label htmlFor="catalog-level">Type</label>
              <select id="catalog-level" value={level} onChange={(e) => update({ level: e.target.value || null })}>
                <option value="">Any type</option>
                <option value={SHORT_LEVELS}>{SHORT_LEVELS_LABEL}</option>
                {LEVELS.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="field">
              <label htmlFor="catalog-kind">Kind</label>
              <select id="catalog-kind" value={kind} onChange={(e) => update({ kind: e.target.value || null })}>
                <option value="">Any kind</option>
                {KINDS.map((k) => (
                  <option key={k.value} value={k.value}>
                    {k.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          <fieldset className="field-group">
            <legend>Reuse</legend>
            {TIERS.map((t) => (
              <label key={t} className="check">
                <input type="checkbox" checked={tiers.includes(t)} onChange={() => toggleIn("tier", tiers, t)} />
                {TIER_LABELS[t]}
              </label>
            ))}
          </fieldset>

          <div className="field">
            <p className="label" id="topic-label">
              Topics{topics.length > 1 ? " (items must match all)" : ""}
            </p>
            {topicList.loading && <Loading label="Loading topics…" />}
            {topicList.error && <ErrorMessage error={topicList.error} onRetry={topicList.reload} />}
            {topicList.data && (
              <TopicChips
                label="Topics"
                topics={topicList.data}
                selected={topics}
                onToggle={(t) => toggleIn("topic", topics, t)}
              />
            )}
          </div>

          {hasFilters && (
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => setParams(tab === "assignments" ? new URLSearchParams({ tab }) : new URLSearchParams())}
            >
              Clear filters
            </button>
          )}
        </aside>

        <section className="results" aria-labelledby="results-title">
          <h2 id="results-title" className="visually-hidden" ref={resultsRef} tabIndex={-1}>
            Results
          </h2>
          <p className="result-count" aria-live="polite">
            {results.data
              ? results.data.total === 0
                ? `No ${tab} match these filters.`
                : `${fmt.format(results.data.total)} ${tab}${results.data.total > PAGE_SIZE ? `, showing ${start}–${end}` : ""}`
              : ""}
          </p>
          {results.loading && <Loading label={`Loading ${tab}…`} />}
          {results.error && <ErrorMessage error={results.error} onRetry={results.reload} />}
          {results.data && !results.loading && results.data.total === 0 && (
            <Empty>
              <p>Try fewer topics, a different focus, or a broader search.</p>
              {hasFilters && (
                <button
                  type="button"
                  className="btn btn-small"
                  onClick={() => setParams(tab === "assignments" ? new URLSearchParams({ tab }) : new URLSearchParams())}
                >
                  Clear filters
                </button>
              )}
            </Empty>
          )}
          {results.data && !results.loading && results.data.items.length > 0 && (
            <ul className="result-list">
              {results.data.items.map((item) =>
                tab === "readings" ? (
                  <ReadingResult key={item.id} r={item as ReadingBrief} onOpen={() => setStack([{ kind: "reading", id: item.id }])} />
                ) : (
                  <AssignmentResult key={item.id} a={item as AssignmentBrief} onOpen={() => setStack([{ kind: "assignment", id: item.id }])} />
                ),
              )}
            </ul>
          )}
          {results.data && totalPages > 1 && (
            <nav className="pager" aria-label="Pages">
              <button type="button" className="btn" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
                Previous
              </button>
              <span>
                Page {page} of {fmt.format(totalPages)}
              </span>
              <button
                type="button"
                className="btn"
                disabled={page >= totalPages}
                onClick={() => goToPage(page + 1)}
              >
                Next
              </button>
            </nav>
          )}
        </section>
      </div>

      <Drawer
        open={Boolean(current)}
        onClose={() => setStack([])}
        title={current?.kind === "assignment" ? "Assignment" : "Reading"}
        toolbar={
          stack.length > 1 ? (
            <button type="button" className="btn btn-small" onClick={() => setStack((s) => s.slice(0, -1))}>
              <span aria-hidden="true">← </span>Back
            </button>
          ) : undefined
        }
      >
        {current && <ItemDetail key={`${current.kind}:${current.id}`} item={current} onOpen={(ref) => setStack((s) => [...s, ref])} />}
      </Drawer>
    </div>
  );
}

function ReadingResult({ r, onOpen }: { r: ReadingBrief; onOpen: () => void }) {
  return (
    <li className="result">
      <h3 className="result-title">
        <button type="button" className="link-button" onClick={onOpen}>
          {r.title}
        </button>
      </h3>
      <p className="meta">
        {readingByline(r)}
        {readingByline(r) ? " · " : ""}
        {levelLabel(r.level)}
        {r.level === "chapter" || r.level === "article" || r.level === "primary-text" || r.level === "website"
          ? ` · ~${r.est_pages} pp.`
          : ""}
      </p>
      <p className="result-badges">
        <TierBadge tier={r.reuse_tier} verified={r.license_verified} />
        {r.license && <span className="meta">{r.license}</span>}
      </p>
      {r.summary && <p className="result-summary">{r.summary}</p>}
    </li>
  );
}

function AssignmentResult({ a, onOpen }: { a: AssignmentBrief; onOpen: () => void }) {
  return (
    <li className="result">
      <h3 className="result-title">
        <button type="button" className="link-button" onClick={onOpen}>
          {a.title}
        </button>
      </h3>
      <p className="meta">
        {assignmentByline(a)}
        {a.kind ? `${assignmentByline(a) ? " · " : ""}${kindLabel(a.kind)}` : ""}
      </p>
      <p className="result-badges">
        <TierBadge tier={a.reuse_tier} verified={a.license_verified} />
        {a.license && <span className="meta">{a.license}</span>}
      </p>
      {a.summary && <p className="result-summary">{a.summary}</p>}
    </li>
  );
}
