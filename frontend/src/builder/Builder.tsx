import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  api,
  ApiError,
  briefCache,
  ensureBriefs,
  rememberBriefs,
  type ArcFull,
  type AssignmentBrief,
  type BuildState,
  type ReadingBrief,
  type StateUnit,
  type Term,
  type TermPreset,
} from "../api";
import { ErrorMessage, Loading } from "../components/Status";
import { presetToTerm, termForWeeks, TermPicker } from "../components/TermPicker";
import { focusesOf, presetsOf, useDocumentTitle, usePack } from "../hooks";
import { clearDraft, editToken, loadDraft, rememberBuild, saveDraft, type Draft } from "../storage";
import {
  addAssignment,
  addReading,
  allIds,
  blankState,
  blankUnit,
  mapUnit,
  mergePlan,
  moveReading,
  normalizeState,
  relayout,
  removeAssignment,
  removeReading,
  stateFromPlan,
  teachingWeeks,
  toggleAssignment,
  toggleReading,
} from "./model";
import { ProgramBar } from "../programs/ProgramBar";
import { draftFits, programStart, withProgram } from "../programs/start";
import { SearchDrawer, type SearchTarget } from "./SearchDrawer";
import { SidePanel } from "./SidePanel";
import { TermBar } from "./TermBar";
import { UnitCard, type UnitActions } from "./UnitCard";

type Banner =
  | { kind: "restored-new"; savedAt: string }
  | { kind: "restored-saved"; savedAt: string }
  | { kind: "forked" }
  | { kind: "saved-first" };

function termFromParams(p: URLSearchParams, presets: TermPreset[]): Term {
  const weeks = parseInt(p.get("weeks") ?? "", 10);
  const base = !Number.isNaN(weeks) && weeks >= 1 && weeks <= 30 ? termForWeeks(weeks, presets) : presetToTerm(presets[0]);
  const num = (k: string, min: number, max: number, fallback: number) => {
    const n = parseInt(p.get(k) ?? "", 10);
    return Number.isNaN(n) ? fallback : Math.min(max, Math.max(min, n));
  };
  return {
    weeks: base.weeks,
    fixed_start: num("fs", 0, 2, base.fixed_start),
    fixed_end: num("fe", 0, 2, base.fixed_end),
    pages_per_week: num("ppw", 5, 300, base.pages_per_week),
  };
}

function briefsFor(state: BuildState): { readings: ReadingBrief[]; assignments: AssignmentBrief[] } {
  const ids = allIds(state);
  return {
    readings: ids.readings.map((id) => briefCache.readings.get(id)).filter((x): x is ReadingBrief => Boolean(x)),
    assignments: ids.assignments.map((id) => briefCache.assignments.get(id)).filter((x): x is AssignmentBrief => Boolean(x)),
  };
}

function timeText(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : d.toLocaleString();
}

export default function Builder() {
  const { id = "new" } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const pack = usePack();
  const presets = presetsOf(pack.data);
  const presetsRef = useRef(presets);
  presetsRef.current = presets;
  const isNewRoute = id === "new";

  const [state, setState] = useState<BuildState | null>(null);
  const [buildId, setBuildId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [arc, setArc] = useState<ArcFull | null>(null);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [loadNonce, setLoadNonce] = useState(0);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [briefVersion, setBriefVersion] = useState(0);
  const [termDraft, setTermDraft] = useState<Term | null>(null);
  const [replanning, setReplanning] = useState(false);
  const [replanError, setReplanError] = useState<Error | null>(null);
  const [searchTarget, setSearchTarget] = useState<SearchTarget | null>(null);
  const revRef = useRef(0);
  const skipLoad = useRef<string | null>(null);
  const forceFresh = useRef(false);
  const formId = useId();

  const readOnly = Boolean(buildId) && !token;
  const blank = Boolean(state && !state.arc_id);
  useDocumentTitle(state ? state.title || "Untitled course" : "Course builder");

  /* ---------------------------------------------------------------- loading */

  const loadKey = isNewRoute ? `new?${search.toString()}` : `id:${id}`;
  useEffect(() => {
    if (!isNewRoute && skipLoad.current === id) {
      skipLoad.current = null;
      return;
    }
    const ctrl = new AbortController();
    const { signal } = ctrl;
    setState(null);
    setLoadError(null);
    setBanner(null);
    setDirty(false);
    setArc(null);
    setSaveError(null);
    setReplanError(null);

    const run = async () => {
      const presetList = presetsRef.current;
      if (isNewRoute) {
        const arcId = search.get("arc");
        const focusParam = search.get("focus") ?? "";
        const term = termFromParams(search, presetList);
        const start = await programStart(search, signal); // ?program=…: see programs/start.ts
        const draft = forceFresh.current ? null : loadDraft(null);
        forceFresh.current = false;
        if (draft && draftFits(draft.state, arcId, start)) {
          rememberBriefs(draft.readings, draft.assignments);
          const s = normalizeState(draft.state);
          const arcFull = s.arc_id ? await api.arc(s.arc_id, signal).catch(() => null) : null;
          const ids = allIds(s);
          await ensureBriefs(ids.readings, ids.assignments, signal);
          if (signal.aborted) return;
          setArc(arcFull);
          setBuildId(null);
          setToken(null);
          setSavedAt(null);
          setState(s);
          setDirty(true);
          setBanner({ kind: "restored-new", savedAt: draft.savedAt });
          return;
        }
        if (start?.state) {
          setArc(start.arc);
          setState(start.state);
        } else if (arcId) {
          const [plan, arcFull] = await Promise.all([api.plan(arcId, term, signal), api.arc(arcId, signal).catch(() => null)]);
          if (signal.aborted) return;
          setArc(arcFull);
          setState(withProgram(stateFromPlan(plan, focusParam), start));
        } else {
          setState(withProgram(blankState(term, focusParam), start));
        }
        setBuildId(null);
        setToken(null);
        setSavedAt(null);
        return;
      }

      const b = await api.getBuild(id, signal);
      // A private edit link carries the token in the URL fragment, which never reaches the server.
      const m = /(?:^#|&)edit=([^&]+)/.exec(window.location.hash);
      if (m) {
        rememberBuild(id, decodeURIComponent(m[1]), b.title, b.updated);
        window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
      }
      const tok = editToken(id);
      let s = normalizeState(b.state);
      let restored: Draft | null = null;
      if (tok) {
        const d = loadDraft(id);
        if (d && Date.parse(d.savedAt) > Date.parse(b.updated)) {
          restored = d;
          s = normalizeState(d.state);
          rememberBriefs(d.readings, d.assignments);
        }
        rememberBuild(id, tok, b.title, b.updated);
      }
      const arcFull = s.arc_id ? await api.arc(s.arc_id, signal).catch(() => null) : null;
      const ids = allIds(s);
      await ensureBriefs(ids.readings, ids.assignments, signal);
      if (signal.aborted) return;
      setArc(arcFull);
      setBuildId(id);
      setToken(tok);
      setSavedAt(b.updated);
      setState(s);
      setDirty(Boolean(restored));
      if (restored) setBanner({ kind: "restored-saved", savedAt: restored.savedAt });
    };

    run()
      .then(() => setBriefVersion((v) => v + 1))
      .catch((e: Error) => {
        if (signal.aborted || e.name === "AbortError") return;
        setLoadError(e);
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadKey, loadNonce]);

  useEffect(() => {
    if (state) setTermDraft(state.term);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.term]);

  /* ---------------------------------------------------------------- editing */

  const edit = useCallback((fn: (s: BuildState) => BuildState) => {
    setState((s) => (s ? fn(s) : s));
    revRef.current += 1;
    setDirty(true);
  }, []);

  // Keep an unsaved draft in this browser so a refresh doesn't lose work.
  useEffect(() => {
    if (!state || !dirty || readOnly) return;
    const t = window.setTimeout(() => {
      saveDraft(buildId, { state, savedAt: new Date().toISOString(), ...briefsFor(state) });
    }, 400);
    return () => window.clearTimeout(t);
  }, [state, dirty, buildId, readOnly]);

  const applyTerm = async (t: Term) => {
    if (!state) return;
    if (!state.arc_id) {
      edit((s) => relayout({ ...s, term: t }));
      return;
    }
    setReplanning(true);
    setReplanError(null);
    try {
      const plan = await api.plan(state.arc_id, t);
      const { state: next, lost } = mergePlan(state, plan);
      if (lost.length) {
        const names = lost.map((u) => `"${u.title}"`).join(", ");
        const ok = window.confirm(
          `A ${t.weeks}-week term leaves out ${lost.length === 1 ? "a unit" : "units"} you changed: ${names}. ` +
            `Your changes to ${lost.length === 1 ? "it" : "them"} will be lost. Refit the course anyway?`,
        );
        if (!ok) {
          setTermDraft(state.term);
          return;
        }
      }
      edit(() => next);
      setBriefVersion((v) => v + 1);
    } catch (e) {
      setReplanError(e as Error);
    } finally {
      setReplanning(false);
    }
  };

  const actionsFor = (u: StateUnit, index: number): UnitActions => ({
    toggleReading: (rid) => edit((s) => mapUnit(s, u.key, (x) => toggleReading(x, rid))),
    moveReading: (i, dir) => edit((s) => mapUnit(s, u.key, (x) => moveReading(x, i, dir))),
    removeReading: (rid) => edit((s) => mapUnit(s, u.key, (x) => removeReading(x, rid))),
    toggleAssignment: (aid) => edit((s) => mapUnit(s, u.key, (x) => toggleAssignment(x, aid))),
    removeAssignment: (aid) => edit((s) => mapUnit(s, u.key, (x) => removeAssignment(x, aid))),
    rename: (title) => edit((s) => mapUnit(s, u.key, (x) => ({ ...x, title, renamed: true }))),
    openSearch: (kind) => setSearchTarget({ unitKey: u.key, kind }),
    setWeeks: (n) => edit((s) => relayout(mapUnit(s, u.key, (x) => ({ ...x, weeks: n })))),
    move: (dir) =>
      edit((s) => {
        const j = index + dir;
        if (j < 0 || j >= s.units.length) return s;
        const units = [...s.units];
        [units[index], units[j]] = [units[j], units[index]];
        return relayout({ ...s, units });
      }),
    remove: () => {
      const count = u.readings.length + u.assignments.length;
      if (count > 0 && !window.confirm(`Delete "${u.title}" and its ${count} ${count === 1 ? "item" : "items"}?`)) return;
      edit((s) => relayout({ ...s, units: s.units.filter((x) => x.key !== u.key) }));
      window.setTimeout(() => document.getElementById("add-unit")?.focus(), 0);
    },
  });

  const addUnit = () =>
    edit((s) => {
      const used = s.units.reduce((n, u) => n + u.weeks, 0);
      const left = teachingWeeks(s.term) - used;
      let units = s.units;
      let weeks = Math.min(Math.max(left, 1), 3);
      if (left <= 0) {
        // Borrow weeks from the longest unit so the course still fits the term.
        const longest = units.reduce((best, u, i) => (u.weeks > units[best].weeks ? i : best), 0);
        const give = Math.min(2, units[longest].weeks - 1);
        if (give > 0) {
          units = units.map((u, i) => (i === longest ? { ...u, weeks: u.weeks - give } : u));
          weeks = give;
        }
      }
      return relayout({ ...s, units: [...units, blankUnit(units.length + 1, weeks)] });
    });

  /* ---------------------------------------------------------------- saving */

  const save = async () => {
    if (!state || readOnly) return;
    setSaving(true);
    setSaveError(null);
    const rev = revRef.current;
    const title = state.title || "Untitled course";
    try {
      if (buildId && token) {
        const r = await api.updateBuild(buildId, state, token);
        rememberBuild(buildId, token, title, r.updated);
        setSavedAt(r.updated);
        if (revRef.current === rev) {
          clearDraft(buildId);
          setDirty(false);
        }
      } else {
        const r = await api.createBuild(state);
        const now = new Date().toISOString();
        rememberBuild(r.id, r.edit_token, title, now);
        clearDraft(null);
        skipLoad.current = r.id;
        setBuildId(r.id);
        setToken(r.edit_token);
        setSavedAt(now);
        setBanner({ kind: "saved-first" });
        if (revRef.current === rev) setDirty(false);
        navigate(`/build/${r.id}`, { replace: true, state: { preserveFocus: true } });
      }
    } catch (e) {
      const err = e as ApiError;
      setSaveError(
        err.status === 403
          ? "This browser doesn't have edit access to this course. Make your own copy to keep your changes."
          : err.message,
      );
    } finally {
      setSaving(false);
    }
  };

  const fork = async () => {
    if (!state) return;
    setSaving(true);
    setSaveError(null);
    try {
      const r = await api.createBuild(state, buildId ?? undefined);
      const now = new Date().toISOString();
      rememberBuild(r.id, r.edit_token, state.title || "Untitled course", now);
      skipLoad.current = r.id;
      setBuildId(r.id);
      setToken(r.edit_token);
      setSavedAt(now);
      setDirty(false);
      setBanner({ kind: "forked" });
      navigate(`/build/${r.id}`, { state: { preserveFocus: true } });
    } catch (e) {
      setSaveError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const startOver = () => {
    if (isNewRoute) {
      if (!window.confirm("Start over? This discards the draft saved in this browser.")) return;
      clearDraft(null);
      forceFresh.current = true;
      setLoadNonce((n) => n + 1);
    } else if (buildId) {
      if (!window.confirm("Discard the changes you haven't saved?")) return;
      clearDraft(buildId);
      setLoadNonce((n) => n + 1);
    }
  };

  /* ---------------------------------------------------------------- render */

  if (loadError) {
    const notFound = loadError instanceof ApiError && loadError.status === 404;
    return (
      <div className="container page narrow">
        <h1>{isNewRoute ? "New course" : "Course"}</h1>
        <ErrorMessage
          title={notFound ? (isNewRoute ? "We couldn't find that sample course." : "We couldn't find that course.") : "The course didn't load."}
          error={notFound ? "The link may be mistyped, or the course may have been removed." : loadError}
          onRetry={notFound ? undefined : () => setLoadNonce((n) => n + 1)}
        />
        <p className="actions">
          <Link className="btn" to="/build/new">
            Start from a blank course
          </Link>
          <Link className="btn btn-quiet" to="/">
            Home
          </Link>
        </p>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="container page">
        <Loading label={isNewRoute ? "Laying out your course…" : "Loading the course…"} />
      </div>
    );
  }

  const searchUnit = searchTarget ? state.units.find((u) => u.key === searchTarget.unitKey) ?? null : null;
  const arcTitles: Record<string, string> = Object.fromEntries((arc?.units ?? []).map((u) => [u.key, u.title]));
  const focuses = focusesOf(pack.data).filter((f) => !f.tag_only);
  const viewUrl = buildId ? `${window.location.origin}/build/${buildId}` : "";
  const editUrl = buildId && token ? `${viewUrl}#edit=${encodeURIComponent(token)}` : "";
  const termChanged =
    termDraft &&
    (termDraft.weeks !== state.term.weeks ||
      termDraft.fixed_start !== state.term.fixed_start ||
      termDraft.fixed_end !== state.term.fixed_end ||
      termDraft.pages_per_week !== state.term.pages_per_week);

  const setField = (k: "title" | "instructor" | "institution" | "term_label" | "focus", v: string) =>
    edit((s) => ({ ...s, [k]: v }));

  return (
    <div className="container page builder">
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/">Home</Link> <span aria-hidden="true">/</span>{" "}
        {state.arc_id && (
          <>
            <Link to={`/arcs/${state.arc_id}`}>{arc?.title ?? "Sample course"}</Link> <span aria-hidden="true">/</span>{" "}
          </>
        )}
        <span aria-current="page">{buildId ? "Saved course" : "New course"}</span>
      </nav>

      <header className="builder-header">
        <h1>{state.title || "Untitled course"}</h1>
        <p className="meta">
          {state.arc_id ? (
            <>
              Based on the sample course <Link to={`/arcs/${state.arc_id}`}>{arc?.title ?? state.arc_id}</Link>.
            </>
          ) : (
            "A blank course. Add units, then fill them from the catalog."
          )}
        </p>
      </header>

      {banner?.kind === "restored-new" && (
        <div className="notice notice-info" role="status">
          <p>
            We restored the draft you were working on ({timeText(banner.savedAt)}).{" "}
            <button type="button" className="link-button" onClick={startOver}>
              Start over instead
            </button>
          </p>
        </div>
      )}
      {banner?.kind === "restored-saved" && (
        <div className="notice notice-info" role="status">
          <p>
            You had unsaved changes in this browser from {timeText(banner.savedAt)}. They're shown below.{" "}
            <button type="button" className="link-button" onClick={startOver}>
              Discard them
            </button>
          </p>
        </div>
      )}
      {banner?.kind === "forked" && (
        <div className="notice notice-ok" role="status">
          <p>This is your own copy. Changes you save here don't affect the original.</p>
        </div>
      )}
      {readOnly && (
        <div className="notice notice-info">
          <p>
            <strong>You're viewing a shared course.</strong> You can export it as is, or make your own copy to change it.
          </p>
          <button type="button" className="btn btn-primary" onClick={fork} disabled={saving}>
            {saving ? "Copying…" : "Make my own copy"}
          </button>
        </div>
      )}

      <ProgramBar state={state} readOnly={readOnly} onEdit={edit} />
      {!readOnly && (
        <div className="save-bar" role="region" aria-label="Save">
          <p className="save-status" aria-live="polite">
            {saving
              ? "Saving…"
              : !buildId
                ? "Not saved yet. Your draft is kept in this browser."
                : dirty
                  ? "Unsaved changes."
                  : `Saved${savedAt ? ` at ${timeText(savedAt)}` : ""}.`}
          </p>
          <button type="button" className="btn btn-primary" onClick={save} disabled={saving || (Boolean(buildId) && !dirty)}>
            {buildId ? "Save changes" : "Save course"}
          </button>
        </div>
      )}
      {saveError && (
        <ErrorMessage title="That didn't save." error={saveError} onRetry={readOnly ? undefined : save} />
      )}
      {banner?.kind === "saved-first" && buildId && (
        <div className="notice notice-ok" role="status">
          <p>
            Saved. This browser can edit the course. To edit it somewhere else, keep the private edit link under
            "Share this course."
          </p>
        </div>
      )}

      <div className="builder-layout">
        <div className="builder-main">
          <section className="panel" aria-labelledby={`${formId}-details`}>
            <h2 id={`${formId}-details`} className="h-small">
              Course details
            </h2>
            <fieldset className="details-grid" disabled={readOnly}>
              <div className="field field-wide">
                <label htmlFor={`${formId}-title`}>Course title</label>
                <input
                  id={`${formId}-title`}
                  value={state.title}
                  onChange={(e) => setField("title", e.target.value)}
                  placeholder="ENGL 101: Writing and Inquiry"
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-instructor`}>Instructor</label>
                <input
                  id={`${formId}-instructor`}
                  value={state.instructor}
                  autoComplete="name"
                  onChange={(e) => setField("instructor", e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-institution`}>Institution</label>
                <input
                  id={`${formId}-institution`}
                  value={state.institution}
                  autoComplete="organization"
                  onChange={(e) => setField("institution", e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor={`${formId}-term`}>Term</label>
                <input
                  id={`${formId}-term`}
                  value={state.term_label}
                  placeholder="Spring 2027"
                  onChange={(e) => setField("term_label", e.target.value)}
                />
              </div>
              {blank && (
                <div className="field">
                  <label htmlFor={`${formId}-focus`}>Focus</label>
                  <select id={`${formId}-focus`} value={state.focus} onChange={(e) => setField("focus", e.target.value)}>
                    <option value="">None</option>
                    {focuses.map((f) => (
                      <option key={f.code} value={f.code}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </fieldset>
          </section>

          <section className="panel" aria-labelledby={`${formId}-term-h`}>
            <h2 id={`${formId}-term-h`} className="h-small">
              Term and schedule
            </h2>
            {!readOnly && termDraft && (
              <details className="term-edit">
                <summary>Change the term</summary>
                <TermPicker
                  value={termDraft}
                  presets={presets}
                  disabled={replanning}
                  onChange={(t) => {
                    setTermDraft(t);
                    if (blank) applyTerm(t);
                  }}
                />
                {!blank && (
                  <div className="actions">
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={!termChanged || replanning}
                      onClick={() => termDraft && applyTerm(termDraft)}
                    >
                      {replanning ? "Refitting…" : `Refit the course to ${termDraft.weeks} weeks`}
                    </button>
                    {termChanged && !replanning && (
                      <button type="button" className="btn btn-quiet" onClick={() => setTermDraft(state.term)}>
                        Cancel
                      </button>
                    )}
                    <p className="hint">
                      Refitting keeps your checked boxes and added items in units that stay in the course.
                    </p>
                  </div>
                )}
              </details>
            )}
            {replanError && <ErrorMessage title="The course couldn't be refit." error={replanError} />}
            <p className="term-summary">
              {state.term.weeks} weeks
              {state.term_label ? ` · ${state.term_label}` : ""} · about {state.term.pages_per_week} pages of reading a week
            </p>
            <TermBar state={state} arcTitles={arcTitles} />
          </section>

          <section aria-labelledby={`${formId}-units`} className="units">
            <h2 id={`${formId}-units`}>Units</h2>
            {state.units.length === 0 && (
              <p className="empty">This course has no units yet.</p>
            )}
            {state.units.map((u, i) => (
              <UnitCard
                key={u.key}
                unit={u}
                index={i}
                count={state.units.length}
                term={state.term}
                readOnly={readOnly}
                blank={blank}
                actions={actionsFor(u, i)}
              />
            ))}
            {blank && !readOnly && (
              <button type="button" id="add-unit" className="btn" onClick={addUnit}>
                Add a unit
              </button>
            )}
          </section>
        </div>

        <aside className="builder-side" aria-label="Course summary, licenses, and export">
          <div className="sticky">
            <SidePanel state={state} briefVersion={briefVersion} />
            {buildId && (
              <ShareBox viewUrl={viewUrl} editUrl={editUrl} />
            )}
          </div>
        </aside>
      </div>

      <SearchDrawer
        target={searchTarget}
        unit={searchUnit}
        focus={state.focus}
        onClose={() => setSearchTarget(null)}
        onAddReading={(r) => {
          if (!searchTarget) return;
          edit((s) => mapUnit(s, searchTarget.unitKey, (x) => addReading(x, r.id)));
          setBriefVersion((v) => v + 1);
        }}
        onAddAssignment={(a, role) => {
          if (!searchTarget) return;
          edit((s) => mapUnit(s, searchTarget.unitKey, (x) => addAssignment(x, a.id, role)));
          setBriefVersion((v) => v + 1);
        }}
      />
    </div>
  );
}

function CopyField({ label, value, help }: { label: string; value: string; help: string }) {
  const id = useId();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      const el = document.getElementById(id) as HTMLInputElement | null;
      el?.select();
    }
  };
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="input-row">
        <input id={id} readOnly value={value} onFocus={(e) => e.currentTarget.select()} aria-describedby={`${id}-help`} />
        <button type="button" className="btn btn-small" onClick={copy}>
          {copied ? "Copied" : "Copy"}
          <span className="visually-hidden"> {label}</span>
        </button>
      </div>
      <p id={`${id}-help`} className="hint">
        {help}
      </p>
    </div>
  );
}

function ShareBox({ viewUrl, editUrl }: { viewUrl: string; editUrl: string }) {
  return (
    <section className="panel" aria-labelledby="share-title">
      <h2 id="share-title" className="h-small">
        Share this course
      </h2>
      <CopyField
        label="View link"
        value={viewUrl}
        help="Anyone with this link can see the course, export it, and make their own copy."
      />
      {editUrl && (
        <CopyField
          label="Private edit link"
          value={editUrl}
          help="Opens the course with editing turned on. Keep it to yourself, or share it with a co-teacher."
        />
      )}
    </section>
  );
}
