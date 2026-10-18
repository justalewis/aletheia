import { useEffect, useId, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api";
import { ErrorMessage, Loading } from "../components/Status";
import { useAsync, useDocumentTitle } from "../hooks";
import { AdminCourses } from "./AdminCourses";
import { AdminReport, AdminSections } from "./AdminReport";
import { adminToken, adminUrl, forgetAdmin, programs, rememberAdmin, type ProgramInfo } from "./client";
import { CopyField } from "./CopyField";
import "./programs.css";

function tokenFromHash(): string | null {
  const m = /(?:^#|&)token=([^&]+)/.exec(window.location.hash);
  return m ? decodeURIComponent(m[1]) : null;
}

export default function ProgramAdmin() {
  const { id = "" } = useParams();
  const [token, setToken] = useState<string | null>(() => tokenFromHash() ?? adminToken(id));
  const info = useAsync((signal) => programs.get(id, signal), [id]);
  // The arcs call doubles as the token check: it answers 403 to a wrong token.
  const arcs = useAsync((signal) => (token ? programs.arcs(id, token, signal) : Promise.resolve(null)), [id, token]);
  const [reportNonce, setReportNonce] = useState(0);
  useDocumentTitle(info.data ? `Manage ${info.data.name}` : "Manage program");

  const badToken = arcs.error instanceof ApiError && arcs.error.status === 403;

  // The first visit with the private link stores the token here and takes it out of the address bar.
  useEffect(() => {
    if (!token || !arcs.data || !info.data) return;
    rememberAdmin(id, token, info.data.name);
    if (tokenFromHash()) {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    }
  }, [arcs.data, info.data, token, id]);

  if (info.loading && !info.data) return <div className="container page"><Loading label="Loading the program…" /></div>;
  if (!info.data) {
    const notFound = info.error instanceof ApiError && info.error.status === 404;
    return (
      <div className="container page narrow">
        <h1>Manage program</h1>
        <ErrorMessage
          title={notFound ? "We couldn't find that program." : "The program didn't load."}
          error={notFound ? "The link may be mistyped." : info.error ?? "Not found"}
          onRetry={notFound ? undefined : info.reload}
        />
      </div>
    );
  }
  const p = info.data;

  if (!token || badToken) {
    return (
      <div className="container page narrow program-page">
        <h1>Manage {p.name}</h1>
        {badToken && (
          <ErrorMessage title="That admin link didn't work." error="Check that you copied the whole link, or paste it again below." />
        )}
        <TokenForm
          onToken={(t) => {
            if (badToken) forgetAdmin(id);
            setToken(t);
          }}
        />
        <p>
          <Link to={`/programs/${p.id}`}>See the program page instructors use</Link>
        </p>
      </div>
    );
  }

  if (arcs.loading && !arcs.data) return <div className="container page"><Loading label="Checking the admin link…" /></div>;
  if (arcs.error) {
    return (
      <div className="container page narrow">
        <h1>Manage {p.name}</h1>
        <ErrorMessage title="The program's courses didn't load." error={arcs.error} onRetry={arcs.reload} />
      </div>
    );
  }

  return (
    <div className="container page program-page program-admin">
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/">Home</Link> <span aria-hidden="true">/</span> <Link to={`/programs/${p.id}`}>{p.name}</Link>{" "}
        <span aria-hidden="true">/</span> <span aria-current="page">Manage</span>
      </nav>
      <h1>Manage {p.name}</h1>
      <p className="lead">
        Share courses with your instructors, see the sections that use them, and report on textbook costs.
      </p>

      <div className="admin-links panel">
        <CopyField
          label="Link for instructors"
          value={`${window.location.origin}/programs/${p.id}`}
          help="Shows the program's description and published courses."
        />
        <CopyField
          label="Private admin link"
          value={adminUrl(p.id, token)}
          help="Opens this page. Keep it private; anyone with it can change the program."
        />
        <button
          type="button"
          className="link-button small"
          onClick={() => {
            if (!window.confirm("Remove admin access from this browser? You'll need the admin link to get back in.")) return;
            forgetAdmin(id);
            setToken(null);
          }}
        >
          Remove admin access from this browser
        </button>
      </div>

      <DetailsForm
        program={p}
        token={token}
        onSaved={() => {
          info.reload();
          setReportNonce((n) => n + 1);
        }}
      />
      <AdminCourses programId={p.id} token={token} arcs={arcs.data ?? []} reload={arcs.reload} />
      <AdminSections programId={p.id} token={token} />
      <AdminReport programId={p.id} token={token} nonce={reportNonce} />
    </div>
  );
}

function TokenForm({ onToken }: { onToken: (token: string) => void }) {
  const id = useId();
  const [value, setValue] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = value.trim();
    const m = /token=([^&\s]+)/.exec(v);
    const t = m ? decodeURIComponent(m[1]) : v;
    if (t) onToken(t);
  };
  return (
    <form className="panel" onSubmit={submit}>
      <p>This page needs the program's private admin link, the one shown when the program was set up.</p>
      <div className="field">
        <label htmlFor={`${id}-link`}>Admin link</label>
        <input id={`${id}-link`} value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" />
      </div>
      <button type="submit" className="btn btn-primary">
        Open the admin page
      </button>
    </form>
  );
}

function DetailsForm({ program, token, onSaved }: { program: ProgramInfo; token: string; onSaved: () => void }) {
  const id = useId();
  const [name, setName] = useState(program.name);
  const [institution, setInstitution] = useState(program.institution);
  const [description, setDescription] = useState(program.description);
  const [cost, setCost] = useState(String(program.settings.textbook_cost_per_student));
  const [terms, setTerms] = useState(program.settings.terms.join("\n"));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const n = Number(cost);
    if (!name.trim()) return setError("Give the program a name.");
    if (cost.trim() === "" || Number.isNaN(n) || n < 0) return setError("Enter the textbook cost per student as a number, like 100 or 85.50.");
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await programs.update(program.id, token, {
        name: name.trim(),
        institution: institution.trim(),
        description: description.trim(),
        settings: {
          textbook_cost_per_student: n,
          terms: terms.split("\n").map((t) => t.trim()).filter(Boolean),
        },
      });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Program details</h2>
      <form onSubmit={submit} noValidate>
        <div className="details-grid">
          <div className="field">
            <label htmlFor={`${id}-name`}>Program name</label>
            <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`${id}-inst`}>Institution</label>
            <input id={`${id}-inst`} value={institution} onChange={(e) => setInstitution(e.target.value)} />
          </div>
          <div className="field field-wide">
            <label htmlFor={`${id}-desc`}>Description for instructors</label>
            <textarea id={`${id}-desc`} rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`${id}-cost`}>Textbook cost per student ({program.settings.currency})</label>
            <input
              id={`${id}-cost`}
              type="number"
              min={0}
              step="0.01"
              inputMode="decimal"
              value={cost}
              aria-describedby={`${id}-cost-help`}
              onChange={(e) => setCost(e.target.value)}
            />
            <p id={`${id}-cost-help`} className="hint">
              What a student in this course would otherwise spend on books. The report multiplies it by enrollment in each
              zero-cost section.
            </p>
          </div>
          <div className="field">
            <label htmlFor={`${id}-terms`}>Terms</label>
            <textarea
              id={`${id}-terms`}
              rows={3}
              value={terms}
              aria-describedby={`${id}-terms-help`}
              placeholder={"Fall 2026\nWinter 2027"}
              onChange={(e) => setTerms(e.target.value)}
            />
            <p id={`${id}-terms-help`} className="hint">
              One per line. Instructors pick from these when they fill in their section.
            </p>
          </div>
        </div>
        {error && <ErrorMessage title="That didn't save." error={error} />}
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Saving…" : "Save details"}
          </button>
          <span aria-live="polite" className="hint">
            {saved ? "Saved." : ""}
          </span>
        </div>
      </form>
    </section>
  );
}
