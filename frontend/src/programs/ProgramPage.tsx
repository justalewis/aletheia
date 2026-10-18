import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api";
import { Empty, ErrorMessage, Loading } from "../components/Status";
import { focusLabel, useAsync, useDocumentTitle, usePack } from "../hooks";
import { adminToken, programs } from "./client";
import "./programs.css";

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export default function ProgramPage() {
  const { id = "" } = useParams();
  const pack = usePack();
  const info = useAsync((signal) => programs.get(id, signal), [id]);
  useDocumentTitle(info.data?.name ?? "Program");

  if (info.loading) return <div className="container page"><Loading label="Loading the program…" /></div>;
  if (info.error || !info.data) {
    const notFound = info.error instanceof ApiError && info.error.status === 404;
    return (
      <div className="container page narrow">
        <h1>Program</h1>
        <ErrorMessage
          title={notFound ? "We couldn't find that program." : "The program didn't load."}
          error={notFound ? "The link may be mistyped." : info.error ?? "Not found"}
          onRetry={notFound ? undefined : info.reload}
        />
      </div>
    );
  }
  const p = info.data;

  return (
    <div className="container page program-page">
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/">Home</Link> <span aria-hidden="true">/</span> <span aria-current="page">Program</span>
      </nav>
      <h1>{p.name}</h1>
      {p.institution && <p className="lead">{p.institution}</p>}
      {p.description && <p className="prose program-description">{p.description}</p>}
      {adminToken(p.id) && (
        <p>
          <Link to={`/programs/${p.id}/admin`}>Manage this program</Link>
        </p>
      )}

      <section className="section" aria-labelledby="program-courses">
        <h2 id="program-courses">Courses to start from</h2>
        {p.arcs.length === 0 ? (
          <Empty>
            <p>The program hasn't published any courses yet.</p>
          </Empty>
        ) : (
          <ul className="card-grid program-arcs">
            {p.arcs.map((a) => (
              <li key={a.id} className="card">
                <h3 className="card-title">{a.title}</h3>
                <p className="meta">
                  {[
                    a.focus ? focusLabel(pack.data, a.focus) : "",
                    plural(a.unit_count, "unit"),
                    plural(a.readings, "reading"),
                    plural(a.assignments, "assignment"),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {a.summary && <p>{a.summary}</p>}
                <p>
                  <Link className="btn btn-primary" to={`/build/new?program=${p.id}&course=${a.id}`}>
                    Use this course<span className="visually-hidden">: {a.title}</span>
                  </Link>
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="section-tight" aria-labelledby="program-how">
        <h2 id="program-how" className="h-small">
          How it works
        </h2>
        <p className="prose">
          Using a course opens your own copy in the builder. Change what you like, fill in your section details, and save.
          Saving links your course to the program so it can count the sections that cost students nothing for textbooks.
        </p>
        <p className="prose">
          Want to build your own instead? <Link to={`/build/new?program=${p.id}`}>Start a blank course in this program</Link>.
        </p>
      </section>
    </div>
  );
}
