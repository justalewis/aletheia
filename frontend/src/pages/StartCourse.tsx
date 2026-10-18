import { useId, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type AutoBuildResult, type Term } from "../api";
import { ErrorMessage, Loading } from "../components/Status";
import { presetToTerm, TermPicker } from "../components/TermPicker";
import { focusesOf, presetsOf, useAsync, useDocumentTitle, usePack } from "../hooks";
import { rememberBuild } from "../storage";

const STATUS_LABEL = { ok: "Covered", weak: "Thin", none: "Not covered" } as const;

export default function StartCourse() {
  useDocumentTitle("Start from your course");
  const pack = usePack();
  const navigate = useNavigate();
  const formId = useId();
  const resultRef = useRef<HTMLHeadingElement>(null);

  const presets = presetsOf(pack.data);
  const focuses = focusesOf(pack.data).filter((f) => !f.tag_only);
  const sets = useAsync((signal) => api.outcomeSets(signal), []);

  const [courseName, setCourseName] = useState("");
  const [focus, setFocus] = useState("");
  const [theme, setTheme] = useState("");
  const [outcomesText, setOutcomesText] = useState("");
  const [setIds, setSetIds] = useState<string[]>([]);
  const [term, setTerm] = useState<Term | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<AutoBuildResult | null>(null);
  const [opening, setOpening] = useState(false);

  const termValue = term ?? presetToTerm(presets[0]);
  const hasInput = Boolean(theme.trim() || outcomesText.trim() || setIds.length || focus);

  const run = async (arcId = "") => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.autoBuild({
        theme,
        outcomes_text: outcomesText,
        outcome_set_ids: setIds,
        course_name: courseName,
        focus,
        arc_id: arcId,
        ...termValue,
        weeks: termValue.weeks,
      });
      setResult(r);
      window.setTimeout(() => resultRef.current?.focus(), 0);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run();
  };

  const open = async () => {
    if (!result) return;
    setOpening(true);
    setError(null);
    try {
      const r = await api.createBuild(result.state);
      rememberBuild(r.id, r.edit_token, result.state.title, new Date().toISOString());
      navigate(`/build/${r.id}`);
    } catch (e) {
      setError(e as Error);
      setOpening(false);
    }
  };

  const toggleSet = (id: string) => setSetIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  const courseSets = (sets.data ?? []).filter((s) => s.kind !== "framework");
  const frameworkSets = (sets.data ?? []).filter((s) => s.kind === "framework");
  const addedCount = result
    ? result.theme_added.length + result.outcomes.reduce((n, o) => n + o.added.length, 0)
    : 0;

  return (
    <div className="container page start-course">
      <h1>Start from your course</h1>
      <p className="prose">
        Tell Aletheia what your course is about, what it has to cover, or both. It picks the sample course that fits
        best, fits it to your term, and adds readings for your theme and for any outcome the sample course leaves thin.
        Nothing is saved until you open it in the builder, and you can change everything there.
      </p>

      <form className="panel" onSubmit={submit} aria-label="Describe your course">
        <div className="field">
          <label htmlFor={`${formId}-name`}>Course name</label>
          <input id={`${formId}-name`} type="text" value={courseName} maxLength={200} placeholder="ENGL& 102: Composition II"
            onChange={(e) => setCourseName(e.target.value)} />
        </div>

        <div className="field">
          <label htmlFor={`${formId}-focus`}>Focus</label>
          <select id={`${formId}-focus`} value={focus} onChange={(e) => setFocus(e.target.value)}>
            <option value="">Let Aletheia choose</option>
            {focuses.map((f) => (
              <option key={f.code} value={f.code}>
                {f.label}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor={`${formId}-theme`}>Theme or topic of inquiry</label>
          <input id={`${formId}-theme`} type="text" value={theme} maxLength={300} placeholder="food justice, or artificial intelligence and labor"
            onChange={(e) => setTheme(e.target.value)} aria-describedby={`${formId}-theme-hint`} />
          <p id={`${formId}-theme-hint`} className="hint">
            Readings about this theme join the units they fit. Leave it blank if the course has no theme.
          </p>
        </div>

        <div className="field">
          <label htmlFor={`${formId}-outcomes`}>Course outcomes</label>
          <textarea id={`${formId}-outcomes`} rows={7} value={outcomesText} maxLength={8000}
            placeholder={"Paste your outcomes, one per line.\nAnalyze audience and purpose to write for specific readers.\nUse sources ethically and cite them."}
            onChange={(e) => setOutcomesText(e.target.value)} aria-describedby={`${formId}-outcomes-hint`} />
          <p id={`${formId}-outcomes-hint`} className="hint">
            Aletheia matches each line to the catalog's topics. Plain statements work better than long paragraphs.
          </p>
        </div>

        {(frameworkSets.length > 0 || courseSets.length > 0) && (
          <fieldset className="field-checks">
            <legend>Or use outcomes already in Aletheia</legend>
            {[...courseSets, ...frameworkSets].map((s) => (
              <label key={s.id} className="check">
                <input type="checkbox" checked={setIds.includes(s.id)} onChange={() => toggleSet(s.id)} />
                {s.title}
                {s.course && s.kind !== "framework" ? ` (${s.institution} ${s.course})` : ""}
              </label>
            ))}
          </fieldset>
        )}

        <TermPicker value={termValue} onChange={setTerm} presets={presets} disabled={busy} />

        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy || !hasInput}>
            {busy ? "Building…" : "Build my course"}
          </button>
          {!hasInput && <span className="hint">Add a theme, outcomes, or a focus to start.</span>}
        </div>
      </form>

      {busy && <Loading label="Matching your course to the catalog…" />}
      {error && <ErrorMessage error={error} onRetry={() => void run(result?.arc.id)} title="That didn't work." />}

      {result && !busy && (
        <section className="section" aria-labelledby={`${formId}-result`}>
          <h2 id={`${formId}-result`} ref={resultRef} tabIndex={-1}>
            {result.arc.title}
          </h2>
          <p className="lead">{result.arc.tagline}</p>
          <p>
            {result.why.join(" ")} {addedCount > 0 ? `${addedCount} readings and activities were added to the sample course.` : ""}{" "}
            <Link to={`/arcs/${result.arc.id}`}>Read about this sample course</Link>.
          </p>

          {result.theme_added.length > 0 && (
            <>
              <h3>Added for your theme</h3>
              <ul className="link-list">
                {result.theme_added.map((t) => (
                  <li key={t.id}>
                    {t.title} <span className="meta">· {t.unit}</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {result.outcomes.length > 0 && (
            <>
              <h3>Your outcomes</h3>
              <ul className="link-list">
                {result.outcomes.map((o) => (
                  <li key={`${o.set_id}:${o.id}`}>
                    <strong>{STATUS_LABEL[o.status]}.</strong> {o.text.length > 160 ? `${o.text.slice(0, 157)}…` : o.text}
                    {o.matched_topics.length > 0 && <span className="meta"> · matched {o.matched_topics.slice(0, 4).join(", ")}</span>}
                    {o.unmatched && (
                      <span className="meta"> · Aletheia couldn't match this to a catalog topic. Try plainer wording.</span>
                    )}
                    {o.added.length > 0 && (
                      <ul>
                        {o.added.map((a) => (
                          <li key={a.id}>
                            Added {a.title} <span className="meta">· {a.unit}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
              <p className="hint">Custom outcomes shape the build but aren't stored with the course. Stored sets are.</p>
            </>
          )}

          <div className="actions">
            <button type="button" className="btn btn-primary" onClick={() => void open()} disabled={opening}>
              {opening ? "Opening…" : "Open in the builder"}
            </button>
          </div>

          {result.alternatives.length > 0 && (
            <>
              <h3>Other sample courses that came close</h3>
              <ul className="link-list">
                {result.alternatives.map((a) => (
                  <li key={a.id}>
                    <button type="button" className="link-button" onClick={() => void run(a.id)} disabled={busy}>
                      Build from {a.title} instead
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}
