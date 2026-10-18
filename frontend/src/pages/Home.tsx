import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import { api } from "../api";
import { ErrorMessage, Loading } from "../components/Status";
import { focusesOf, useAsync, useDocumentTitle, usePack } from "../hooks";
import { myBuilds } from "../storage";

const fmt = new Intl.NumberFormat("en-US");

function plural(n: number, one: string, many = `${one}s`) {
  return `${fmt.format(n)} ${n === 1 ? one : many}`;
}

export default function Home() {
  useDocumentTitle(null);
  const pack = usePack();
  const arcs = useAsync((signal) => api.arcs("", signal), []);
  const saved = Object.entries(myBuilds()).sort((a, b) => (b[1].updated || "").localeCompare(a[1].updated || ""));

  const focuses = focusesOf(pack.data).filter((f) => !f.tag_only);
  const arcCount = (code: string) => arcs.data?.filter((a) => a.focus === code).length ?? 0;
  // Every focus is listed until the sample courses load; then a focus without any moves to "Other focuses".
  const withArcs = arcs.data ? focuses.filter((f) => arcCount(f.code) > 0) : focuses;
  const others = arcs.data ? focuses.filter((f) => arcCount(f.code) === 0) : [];

  return (
    <div className="container page">
      <section className="hero" aria-labelledby="home-title">
        <h1 id="home-title">Aletheia</h1>
        <p className="lead">Build an open course reader for your writing class.</p>
        <p className="prose">
          Pick a focus and a sample course, fit it to your term, and check off the readings and assignments you want.
          Aletheia gives you a course reader (HTML or PDF) and a package you can import into Canvas or another LMS.
          Everything in it is openly licensed, or linked when it isn't.
        </p>
        {pack.loading && <Loading label="Loading the catalog…" />}
        {pack.error && <ErrorMessage error={pack.error} onRetry={pack.reload} title="The catalog didn't load." />}
        {pack.data && (
          <p className="counts">
            The catalog has {plural(pack.data.counts.readings, "reading")}, {plural(pack.data.counts.assignments, "assignment")}
            , and {plural(pack.data.counts.arcs, "sample course")}.
          </p>
        )}
        <div className="actions">
          <Link className="btn btn-primary" to="/start">
            Start from your course
          </Link>
          <Link className="btn" to="/catalog">
            Browse the catalog
          </Link>
          <Link className="btn" to="/build/new">
            Start from a blank course
          </Link>
        </div>
      </section>

      {saved.length > 0 && (
        <section className="section" aria-labelledby="saved-title">
          <h2 id="saved-title">Your saved courses</h2>
          <p className="hint">Saved in this browser. Keep the links somewhere safe; they're how you get back to edit.</p>
          <ul className="link-list">
            {saved.map(([id, b]) => (
              <li key={id}>
                <Link to={`/build/${id}`}>{b.title || "Untitled course"}</Link>
                {b.updated && <span className="meta"> · saved {new Date(b.updated).toLocaleDateString()}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section" aria-labelledby="focus-title">
        <h2 id="focus-title">Start from a sample course</h2>
        <p className="prose">
          Each sample course lays out units, goals, and readings for a full semester. You can shorten it to a quarter or
          a summer term and change anything in it.
        </p>
        {pack.data && (
          <ul className="index-list">
            {withArcs.map((f) => (
              <li key={f.code}>
                <Link className="index-row" to={`/focus/${f.code}`}>
                  <span className="index-title">{f.label}</span>
                  <span className="index-meta">
                    {arcs.loading ? "Counting…" : arcs.error ? "" : plural(arcCount(f.code), "sample course")}
                  </span>
                  <Icon name="arrow" size={24} />
                </Link>
                <p className="index-sub">
                  <Link to={`/catalog?focus=${f.code}`}>Browse {f.label} readings</Link>
                </p>
              </li>
            ))}
          </ul>
        )}
        {arcs.error && <ErrorMessage error={arcs.error} onRetry={arcs.reload} title="Sample courses didn't load." />}
      </section>

      {others.length > 0 && (
        <section className="section" aria-labelledby="other-title">
          <h2 id="other-title">Other focuses</h2>
          <p className="prose">
            There are no sample courses for these yet. You can browse their readings and assignments, or start a blank
            course and add them yourself.
          </p>
          <ul className="chip-list">
            {others.map((f) => (
              <li key={f.code}>
                <Link className="chip-link" to={`/catalog?focus=${f.code}`}>
                  {f.label}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
