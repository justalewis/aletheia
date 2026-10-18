import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ErrorMessage } from "../components/Status";
import { useDocumentTitle } from "../hooks";
import { adminUrl, programs, rememberAdmin } from "./client";
import { CopyField } from "./CopyField";
import "./programs.css";

export default function NewProgram() {
  const id = useId();
  const [name, setName] = useState("");
  const [institution, setInstitution] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ id: string; token: string; name: string } | null>(null);
  useDocumentTitle(created ? "Program created" : "Set up a program");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Give the program a name.");
      document.getElementById(`${id}-name`)?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await programs.create({ name: name.trim(), institution: institution.trim(), description: description.trim() });
      rememberAdmin(r.id, r.admin_token, name.trim());
      setCreated({ id: r.id, token: r.admin_token, name: name.trim() });
      window.setTimeout(() => document.getElementById(`${id}-done`)?.focus(), 0);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (created) {
    const admin = adminUrl(created.id, created.token);
    return (
      <div className="container page narrow program-page">
        <h1 id={`${id}-done`} tabIndex={-1}>
          {created.name} is set up
        </h1>
        <div className="notice notice-warn">
          <p className="notice-title">Save your admin link now.</p>
          <p>
            This is the only time Aletheia shows it, and there's no way to recover it. Anyone with the link can change the
            program and see its sections. Keep it private; share it only with a co-coordinator.
          </p>
        </div>
        <CopyField
          label="Private admin link"
          value={admin}
          help="Opens the program's admin page. This browser remembers it, but other browsers need the link."
        />
        <CopyField
          label="Link for instructors"
          value={`${window.location.origin}/programs/${created.id}`}
          help="Instructors open this to start from the program's courses."
        />
        <p className="actions">
          <Link className="btn btn-primary" to={`/programs/${created.id}/admin#token=${encodeURIComponent(created.token)}`}>
            Go to the admin page
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="container page narrow program-page">
      <h1>Set up a program</h1>
      <p className="lead">
        A program shares starting courses with the instructors who teach a course, and reports which sections cost students
        nothing for textbooks.
      </p>
      <p>
        There are no accounts. You get a private admin link for managing the program, and a public link for instructors.
      </p>
      <form onSubmit={submit} noValidate className="panel">
        <div className="field">
          <label htmlFor={`${id}-name`}>Program name</label>
          <input
            id={`${id}-name`}
            value={name}
            required
            aria-required="true"
            placeholder="Olympic College English: ENGL&101"
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-inst`}>Institution</label>
          <input id={`${id}-inst`} value={institution} autoComplete="organization" onChange={(e) => setInstitution(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-desc`}>Description for instructors</label>
          <textarea id={`${id}-desc`} rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
          <p className="hint">Shown at the top of the program page. You can change it later.</p>
        </div>
        {error && <ErrorMessage title="The program wasn't created." error={error} />}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Creating…" : "Create the program"}
        </button>
      </form>
    </div>
  );
}
