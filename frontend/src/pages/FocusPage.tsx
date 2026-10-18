import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorMessage, Loading } from "../components/Status";
import { focusLabel, useAsync, useDocumentTitle, usePack } from "../hooks";

export default function FocusPage() {
  const { focus = "" } = useParams();
  const pack = usePack();
  const label = focusLabel(pack.data, focus);
  useDocumentTitle(label);
  const arcs = useAsync((signal) => api.arcs(focus, signal), [focus]);

  return (
    <div className="container page">
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/">Home</Link> <span aria-hidden="true">/</span> <span aria-current="page">{label}</span>
      </nav>
      <h1>{label}</h1>
      <p className="lead">Sample courses</p>
      <p className="prose">
        Pick one to see its units and readings, then fit it to your term. Or start with a blank course and build it
        from the catalog.
      </p>

      {arcs.loading && <Loading label="Loading sample courses…" />}
      {arcs.error && <ErrorMessage error={arcs.error} onRetry={arcs.reload} />}
      {arcs.data && arcs.data.length === 0 && (
        <Empty>
          <p>There are no sample courses for {label} yet.</p>
          <p>You can start from a blank course, or browse the catalog for this focus.</p>
        </Empty>
      )}
      {arcs.data && arcs.data.length > 0 && (
        <ul className="card-grid">
          {arcs.data.map((a) => (
            <li key={a.id} className="card arc-card">
              <h2 className="card-title">
                <Link to={`/arcs/${a.id}`}>{a.title}</Link>
              </h2>
              <p>{a.tagline}</p>
              <p className="meta">
                {a.unit_count} {a.unit_count === 1 ? "unit" : "units"} over about {a.ideal_weeks} weeks
                {a.course_level && a.course_level !== "mixed" ? ` · ${a.course_level}-level` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}

      <div className="actions section">
        <Link className="btn" to={`/build/new?focus=${encodeURIComponent(focus)}`}>
          Start from a blank course
        </Link>
        <Link className="btn btn-quiet" to={`/catalog?focus=${encodeURIComponent(focus)}`}>
          Browse the {label} catalog
        </Link>
      </div>
    </div>
  );
}
