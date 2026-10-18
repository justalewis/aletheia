import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ArcFull, type OutcomeSetBrief, type Term } from "../api";
import { ErrorMessage, Loading } from "../components/Status";
import { TermPicker, presetToTerm } from "../components/TermPicker";
import { TierBadge } from "../components/TierBadge";
import { focusLabel, presetsOf, useAsync, useDocumentTitle, usePack } from "../hooks";

function buildUrl(arcId: string, t: Term): string {
  const p = new URLSearchParams({
    arc: arcId,
    weeks: String(t.weeks),
    fs: String(t.fixed_start),
    fe: String(t.fixed_end),
    ppw: String(t.pages_per_week),
  });
  return `/build/new?${p.toString()}`;
}

function courseName(id: string, sets: OutcomeSetBrief[] | null): string {
  const s = sets?.find((x) => x.id === id);
  if (!s) return id;
  const head = [s.institution, s.course].filter(Boolean).join(" ");
  return head && s.title ? `${head}: ${s.title}` : head || s.title || id;
}

export default function ArcDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const pack = usePack();
  const presets = presetsOf(pack.data);
  const arc = useAsync((signal) => api.arc(id, signal), [id]);
  const sets = useAsync((signal) => api.outcomeSets(signal), []);
  const [term, setTerm] = useState<Term>(() => presetToTerm(presets[0]));
  useDocumentTitle(arc.data?.title ?? "Sample course");

  // When the pack finishes loading, start from its first preset.
  useEffect(() => {
    if (pack.data) setTerm(presetToTerm(presetsOf(pack.data)[0]));
  }, [pack.data]);

  if (arc.loading) return <div className="container page"><Loading label="Loading the sample course…" /></div>;
  if (arc.error || !arc.data) {
    return (
      <div className="container page">
        <h1>Sample course</h1>
        <ErrorMessage error={arc.error ?? "Not found"} onRetry={arc.reload} title="This sample course didn't load." />
        <p>
          <Link to="/">Back to the home page</Link>
        </p>
      </div>
    );
  }
  const a: ArcFull = arc.data;
  const label = focusLabel(pack.data, a.focus);

  return (
    <div className="container page">
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/">Home</Link> <span aria-hidden="true">/</span> <Link to={`/focus/${a.focus}`}>{label}</Link>{" "}
        <span aria-hidden="true">/</span> <span aria-current="page">{a.title}</span>
      </nav>

      <div className="two-col">
        <div className="two-col-main">
          <h1>{a.title}</h1>
          <p className="lead">{a.tagline}</p>
          <p className="jump-link">
            <a href="#fit-title">Fit it to your term and build it</a>
          </p>
          {a.rationale && (
            <section aria-labelledby="rationale-title" className="section-tight">
              <h2 id="rationale-title" className="h-small">
                Why this course
              </h2>
              <p className="prose">{a.rationale}</p>
            </section>
          )}
          {a.fits_courses.length > 0 && (
            <section aria-labelledby="fits-title" className="section-tight">
              <h2 id="fits-title" className="h-small">
                Written to fit
              </h2>
              <ul className="plain-list">
                {a.fits_courses.map((c) => (
                  <li key={c}>{courseName(c, sets.data)}</li>
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="units-title" className="section">
            <h2 id="units-title">Units</h2>
            <p className="hint">
              Week counts are for a 16-week semester. Shorter terms trim optional units first, then combine units that
              share a project.
            </p>
            <ol className="unit-outline">
              {a.units.map((u, i) => {
                const core = u.readings.core ?? [];
                const optional = u.readings.optional ?? [];
                const major = (u.assignments.major ?? []).map((x) => a.assignments[x]).filter(Boolean);
                return (
                  <li key={u.key} className="unit-outline-item">
                    <h3>
                      <span className="unit-num">Unit {i + 1}</span> {u.title}
                    </h3>
                    <p className="meta">
                      About {u.ideal_weeks} {u.ideal_weeks === 1 ? "week" : "weeks"} (at least {u.min_weeks})
                      {u.priority === "optional" ? " · optional unit, dropped first in short terms" : ""}
                    </p>
                    {u.summary && <p className="prose">{u.summary}</p>}
                    {u.goals.length > 0 && (
                      <>
                        <h4 className="h-tiny">Goals</h4>
                        <ul className="goals">
                          {u.goals.map((g, gi) => (
                            <li key={gi}>{g}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {core.length > 0 && (
                      <>
                        <h4 className="h-tiny">Core readings</h4>
                        <ul className="mini-list">
                          {core.map((rid) => {
                            const r = a.readings[rid];
                            if (!r) return null;
                            return (
                              <li key={rid}>
                                <span className="mini-title">{r.title}</span>
                                <span className="meta">
                                  {r.parent_title ? ` · ${r.parent_title}` : r.authors ? ` · ${r.authors}` : ""} · ~{r.est_pages}{" "}
                                  pp.
                                </span>{" "}
                                <TierBadge tier={r.reuse_tier} verified={r.license_verified} />
                              </li>
                            );
                          })}
                        </ul>
                      </>
                    )}
                    {optional.length > 0 && (
                      <p className="meta">
                        Plus {optional.length} optional {optional.length === 1 ? "reading" : "readings"}, added as your
                        reading load allows.
                      </p>
                    )}
                    {major.length > 0 && (
                      <p className="meta">
                        Major project: {major.map((m) => m.title).join("; ")}
                      </p>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>
        </div>

        <aside className="two-col-side" aria-labelledby="fit-title">
          <div className="panel sticky">
            <h2 id="fit-title" className="h-small">
              Fit it to your term
            </h2>
            <TermPicker value={term} onChange={setTerm} presets={presets} compact />
            <button type="button" className="btn btn-primary btn-block" onClick={() => navigate(buildUrl(a.id, term))}>
              Build this course
            </button>
            <p className="hint">You can change the term, readings, and assignments in the next step.</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
