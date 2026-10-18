import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { ErrorMessage } from "../components/Status";
import { useAsync } from "../hooks";
import { programs, type ProgramArcBrief } from "./client";

interface Props {
  programId: string;
  token: string;
  arcs: ProgramArcBrief[];
  reload: () => void;
}

function plural(n: number, one: string): string {
  return `${n} ${one}${n === 1 ? "" : "s"}`;
}

export function AdminCourses({ programId, token, arcs, reload }: Props) {
  const id = useId();
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>, done: string): Promise<boolean> => {
    setBusy(true);
    setError(null);
    setStatus("");
    try {
      await fn();
      setStatus(done);
      reload();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const move = (index: number, dir: -1 | 1) => {
    const j = index + dir;
    if (j < 0 || j >= arcs.length) return;
    const next = [...arcs];
    [next[index], next[j]] = [next[j], next[index]];
    const moved = arcs[index];
    run(
      () =>
        Promise.all(
          next.map((a, i) => (a.order !== i + 1 ? programs.updateArc(programId, token, a.id, { order: i + 1 }) : null)),
        ),
      `Moved "${moved.title}" ${dir < 0 ? "up" : "down"}.`,
    ).then(() => {
      // Keep focus on the moved course once the list redraws; at either end, its other move button.
      window.setTimeout(() => {
        const btn = (d: string) => document.getElementById(`${id}-move-${moved.id}-${d}`) as HTMLButtonElement | null;
        const first = btn(dir < 0 ? "up" : "down");
        (first && !first.disabled ? first : btn(dir < 0 ? "down" : "up"))?.focus();
      }, 300);
    });
  };

  return (
    <section className="panel" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} tabIndex={-1}>
        Program courses
      </h2>
      <p className="hint">
        Instructors see published courses on the program page and start their own copies from them. Changing a course here
        doesn't change copies instructors already made.
      </p>
      <div aria-live="polite">{status && <p className="notice notice-ok">{status}</p>}</div>
      {error && <ErrorMessage title="That didn't work." error={error} />}

      {arcs.length === 0 ? (
        <p className="empty">No courses yet. Add one from a saved course, or start a new one below.</p>
      ) : (
        <ol className="arc-admin-list">
          {arcs.map((a, i) => (
            <li key={a.id} className="arc-admin-item">
              {editing === a.id ? (
                <ArcEditForm
                  arc={a}
                  onCancel={() => setEditing(null)}
                  onSave={(title, summary) =>
                    run(() => programs.updateArc(programId, token, a.id, { title, summary }), `Saved "${title}".`).then((ok) => {
                      if (ok) setEditing(null);
                    })
                  }
                />
              ) : (
                <>
                  <div className="arc-admin-head">
                    <h3 className="arc-admin-title">{a.title}</h3>
                    <span className={`status-tag ${a.published ? "is-published" : ""}`}>
                      {a.published ? "Published" : "Not published"}
                    </span>
                  </div>
                  <p className="meta">
                    {plural(a.unit_count, "unit")} · {plural(a.readings, "reading")} · {plural(a.assignments, "assignment")}
                  </p>
                  {a.summary && <p className="arc-admin-summary">{a.summary}</p>}
                  <div className="row-buttons">
                    <button
                      type="button"
                      className="btn btn-small"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () => programs.updateArc(programId, token, a.id, { published: !a.published }),
                          a.published ? `"${a.title}" is no longer published.` : `"${a.title}" is published.`,
                        )
                      }
                    >
                      {a.published ? "Unpublish" : "Publish"}
                      <span className="visually-hidden"> {a.title}</span>
                    </button>
                    <button type="button" className="btn btn-small" disabled={busy} onClick={() => setEditing(a.id)}>
                      Edit title and summary<span className="visually-hidden"> for {a.title}</span>
                    </button>
                    <Link className="btn btn-small" to={`/build/new?program=${programId}&course=${a.id}&as=template`}>
                      Open in the builder<span className="visually-hidden">: {a.title}</span>
                    </Link>
                    <button
                      type="button"
                      id={`${id}-move-${a.id}-up`}
                      className="btn btn-small"
                      disabled={busy || i === 0}
                      onClick={() => move(i, -1)}
                    >
                      Move up<span className="visually-hidden"> {a.title}</span>
                    </button>
                    <button
                      type="button"
                      id={`${id}-move-${a.id}-down`}
                      className="btn btn-small"
                      disabled={busy || i === arcs.length - 1}
                      onClick={() => move(i, 1)}
                    >
                      Move down<span className="visually-hidden"> {a.title}</span>
                    </button>
                    <button
                      type="button"
                      className="btn btn-small btn-danger"
                      disabled={busy}
                      onClick={() => {
                        if (!window.confirm(`Delete "${a.title}" from the program? Copies instructors already made aren't affected.`)) return;
                        run(() => programs.deleteArc(programId, token, a.id), `Deleted "${a.title}".`).then(() =>
                          document.getElementById(`${id}-h`)?.focus(),
                        );
                      }}
                    >
                      Delete<span className="visually-hidden"> {a.title}</span>
                    </button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ol>
      )}

      <div className="add-grid">
        <AddFromBuild programId={programId} token={token} onAdded={(t) => { setStatus(`Added "${t}". It isn't published yet.`); reload(); }} />
        <StartNew programId={programId} />
      </div>
    </section>
  );
}

function ArcEditForm({ arc, onSave, onCancel }: { arc: ProgramArcBrief; onSave: (title: string, summary: string) => void; onCancel: () => void }) {
  const id = useId();
  const [title, setTitle] = useState(arc.title);
  const [summary, setSummary] = useState(arc.summary);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (title.trim()) onSave(title.trim(), summary.trim());
      }}
    >
      <div className="field">
        <label htmlFor={`${id}-t`}>Title</label>
        <input id={`${id}-t`} value={title} autoFocus onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-s`}>Summary for instructors</label>
        <textarea id={`${id}-s`} rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} />
      </div>
      <div className="row-buttons">
        <button type="submit" className="btn btn-small btn-primary" disabled={!title.trim()}>
          Save
        </button>
        <button type="button" className="btn btn-small btn-quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function AddFromBuild({ programId, token, onAdded }: { programId: string; token: string; onAdded: (title: string) => void }) {
  const id = useId();
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!link.trim()) return setError("Paste the link to a saved course.");
    setBusy(true);
    setError(null);
    try {
      const arc = await programs.addArc(programId, token, { build_id: link.trim() });
      setLink("");
      onAdded(arc.title);
    } catch (err) {
      const e2 = err as ApiError;
      setError(e2.status === 404 ? "We couldn't find a saved course at that link. Check that it's the whole link." : e2.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate className="add-box">
      <h3 className="h-small">Add a course from a saved course</h3>
      <div className="field">
        <label htmlFor={`${id}-link`}>Saved course link</label>
        <input
          id={`${id}-link`}
          value={link}
          autoComplete="off"
          placeholder={`${window.location.origin}/build/…`}
          aria-describedby={`${id}-help`}
          onChange={(e) => setLink(e.target.value)}
        />
        <p id={`${id}-help`} className="hint">
          The view link or edit link from "Share this course" in the builder. The program gets a copy as it is now.
        </p>
      </div>
      {error && <ErrorMessage error={error} />}
      <button type="submit" className="btn" disabled={busy}>
        {busy ? "Adding…" : "Add course"}
      </button>
    </form>
  );
}

function StartNew({ programId }: { programId: string }) {
  const id = useId();
  const navigate = useNavigate();
  const arcs = useAsync((signal) => api.arcs("", signal), []);
  const [from, setFrom] = useState("");
  return (
    <form
      className="add-box"
      onSubmit={(e) => {
        e.preventDefault();
        navigate(`/build/new?program=${programId}&as=template${from ? `&arc=${encodeURIComponent(from)}` : ""}`);
      }}
    >
      <h3 className="h-small">Start a new program course</h3>
      <div className="field">
        <label htmlFor={`${id}-from`}>Start from</label>
        <select id={`${id}-from`} value={from} onChange={(e) => setFrom(e.target.value)} aria-describedby={`${id}-help`}>
          <option value="">A blank course</option>
          {(arcs.data ?? []).map((a) => (
            <option key={a.id} value={a.id}>
              Sample course: {a.title}
            </option>
          ))}
        </select>
        <p id={`${id}-help`} className="hint">
          Opens the builder. When the course is ready, use "Add to the program" at the top of the builder.
        </p>
      </div>
      <button type="submit" className="btn">
        Open the builder
      </button>
    </form>
  );
}
