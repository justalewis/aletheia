/**
 * Shown in the builder when a course belongs to a program: the instructor's section form, or,
 * for a coordinator drafting a program course, the action that adds it to the program.
 */
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, type BuildState, type SectionInfo } from "../api";
import { useAsync } from "../hooks";
import { adminToken, programs, shareableState } from "./client";
import { emptySection } from "./start";
import "./programs.css";

interface Props {
  state: BuildState;
  readOnly: boolean;
  onEdit: (fn: (s: BuildState) => BuildState) => void;
}

export function ProgramBar({ state, readOnly, onEdit }: Props) {
  const link = state.program;
  const info = useAsync(
    (signal) => (link ? programs.get(link.id, signal) : Promise.resolve(null)),
    [link?.id],
  );
  if (!link) return null;
  const name = info.data?.name ?? link.name;
  const missing = info.error instanceof ApiError && info.error.status === 404;
  const detach = () => {
    if (!window.confirm(`Remove this course from ${name}? It stays saved as your own course.`)) return;
    onEdit((s) => ({ ...s, program: null, section: null }));
  };

  if (missing) {
    return (
      <div className="notice notice-warn">
        <p>This course was linked to a program that no longer exists.</p>
        {!readOnly && (
          <button type="button" className="btn btn-small" onClick={detach}>
            Remove the link
          </button>
        )}
      </div>
    );
  }
  if (link.mode === "template") return <TemplateBar state={state} name={name} onEdit={onEdit} readOnly={readOnly} />;
  return (
    <SectionForm
      state={state}
      name={name}
      terms={info.data?.settings.terms ?? []}
      readOnly={readOnly}
      onEdit={onEdit}
      onDetach={detach}
    />
  );
}

function SectionForm({
  state,
  name,
  terms,
  readOnly,
  onEdit,
  onDetach,
}: {
  state: BuildState;
  name: string;
  terms: string[];
  readOnly: boolean;
  onEdit: Props["onEdit"];
  onDetach: () => void;
}) {
  const id = useId();
  const sec = state.section ?? emptySection();
  const set = (k: keyof SectionInfo, v: string | number | null) =>
    onEdit((s) => ({ ...s, section: { ...(s.section ?? emptySection()), [k]: v } }));
  const programUrl = `/programs/${state.program?.id}`;

  if (readOnly) {
    const parts = [sec.course_code, sec.section && `section ${sec.section}`, sec.term].filter(Boolean);
    return (
      <div className="notice notice-info program-bar">
        <p>
          Part of <Link to={programUrl}>{name}</Link>
          {parts.length ? `: ${parts.join(", ")}` : ""}.
        </p>
      </div>
    );
  }

  return (
    <section className="panel program-bar" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="h-small">
        Part of <Link to={programUrl}>{name}</Link>
      </h2>
      <p className="hint">
        When you save, this course is linked to the program. Your coordinator sees these section details and your reading
        list in the program's zero-textbook-cost report.
      </p>
      <fieldset className="section-grid">
        <legend className="visually-hidden">Your section</legend>
        <div className="field">
          <label htmlFor={`${id}-code`}>Course code</label>
          <input id={`${id}-code`} value={sec.course_code} placeholder="ENGL&101" onChange={(e) => set("course_code", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-sec`}>Section</label>
          <input id={`${id}-sec`} value={sec.section} placeholder="03" onChange={(e) => set("section", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-term`}>Term</label>
          <input
            id={`${id}-term`}
            value={sec.term}
            placeholder={terms[0] ?? "Fall 2026"}
            list={terms.length ? `${id}-terms` : undefined}
            onChange={(e) => set("term", e.target.value)}
          />
          {terms.length > 0 && (
            <datalist id={`${id}-terms`}>
              {terms.map((t) => (
                <option key={t} value={t} />
              ))}
            </datalist>
          )}
        </div>
        <div className="field">
          <label htmlFor={`${id}-enr`}>Enrollment</label>
          <input
            id={`${id}-enr`}
            type="number"
            inputMode="numeric"
            min={0}
            max={10000}
            step={1}
            value={sec.enrollment ?? ""}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              set("enrollment", Number.isNaN(n) ? null : Math.max(0, Math.min(10000, n)));
            }}
          />
        </div>
      </fieldset>
      <button type="button" className="link-button small" onClick={onDetach}>
        Remove this course from the program
      </button>
    </section>
  );
}

function TemplateBar({ state, name, readOnly, onEdit }: { state: BuildState; name: string; readOnly: boolean; onEdit: Props["onEdit"] }) {
  const id = useId();
  const link = state.program!;
  const token = adminToken(link.id);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const adminPath = `/programs/${link.id}/admin`;

  const share = async () => {
    if (!token) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      if (link.course) {
        await programs.updateArc(link.id, token, link.course, { state: shareableState(state) });
        setDone("Saved to the program course. Instructors who start from it now get this version.");
      } else {
        const arc = await programs.addArc(link.id, token, { state: shareableState(state), title: state.title || undefined });
        onEdit((s) => (s.program ? { ...s, program: { ...s.program, course: arc.id } } : s));
        setDone("Added to the program. It isn't published yet; publish it on the admin page when it's ready.");
      }
    } catch (e) {
      const err = e as ApiError;
      setError(err.status === 403 ? "This browser's admin link for the program no longer works." : err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel program-bar" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="h-small">
        Course for <Link to={adminPath}>{name}</Link>
      </h2>
      <p className="hint">
        {link.course
          ? "You're editing a program course. Save to the program to change what instructors start from. The Save course button below keeps a separate copy for you."
          : "You're drafting a course for the program. Instructors see it once you add it to the program and publish it."}
      </p>
      {!readOnly && token && (
        <div className="actions">
          <button type="button" className="btn btn-primary" onClick={share} disabled={busy}>
            {busy ? "Saving…" : link.course ? "Save to the program course" : "Add to the program"}
          </button>
          <Link to={adminPath}>Back to the admin page</Link>
        </div>
      )}
      {!token && (
        <p className="notice notice-warn">
          This browser doesn't have the program's admin link, so it can't add courses. Open your admin link in this browser
          first.
        </p>
      )}
      <div aria-live="polite">
        {done && <p className="notice notice-ok">{done}</p>}
      </div>
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
