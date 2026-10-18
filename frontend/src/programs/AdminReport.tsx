import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { ErrorMessage, Loading } from "../components/Status";
import { useAsync } from "../hooks";
import { money, programs, saveBlob, type ReportRow } from "./client";

function dateText(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString();
}

export function AdminSections({ programId, token }: { programId: string; token: string }) {
  const id = useId();
  const sections = useAsync((signal) => programs.sections(programId, token, signal), [programId, token]);
  return (
    <section className="panel" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Sections</h2>
      <p className="hint">Courses instructors saved in this program, most recently saved first.</p>
      {sections.loading && !sections.data && <Loading label="Loading sections…" />}
      {sections.error && <ErrorMessage title="Sections didn't load." error={sections.error} onRetry={sections.reload} />}
      {sections.data && sections.data.length === 0 && (
        <p className="empty">No sections yet. When an instructor saves a course from the program page, it shows up here.</p>
      )}
      {sections.data && sections.data.length > 0 && (
        <div className="table-wrap" role="region" aria-labelledby={`${id}-cap`} tabIndex={0}>
          <table className="data-table">
            <caption id={`${id}-cap`} className="visually-hidden">
              Sections in this program
            </caption>
            <thead>
              <tr>
                <th scope="col">Course</th>
                <th scope="col">Section</th>
                <th scope="col">Term</th>
                <th scope="col">Instructor</th>
                <th scope="col" className="num">Enrollment</th>
                <th scope="col" className="num">Readings</th>
                <th scope="col" className="num">Assignments</th>
                <th scope="col">Saved</th>
              </tr>
            </thead>
            <tbody>
              {sections.data.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link to={`/build/${s.id}`}>{s.section.course_code || s.title}</Link>
                    {s.section.course_code && <span className="cell-sub">{s.title}</span>}
                  </td>
                  <td>{s.section.section || "–"}</td>
                  <td>{s.section.term || "–"}</td>
                  <td>{s.instructor || "–"}</td>
                  <td className="num">{s.section.enrollment ?? 0}</td>
                  <td className="num">{s.readings}</td>
                  <td className="num">{s.assignments}</td>
                  <td>{dateText(s.updated)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function whyNot(r: ReportRow): string {
  if (r.zero_cost) return "";
  if (r.note) return r.note;
  const ids = r.not_free.map((i) => i.id);
  return `${ids.length === 1 ? "1 item isn't" : `${ids.length} items aren't`} in the catalog or not free: ${ids.join(", ")}.`;
}

export function AdminReport({ programId, token, nonce }: { programId: string; token: string; nonce: number }) {
  const id = useId();
  const [term, setTerm] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [csvError, setCsvError] = useState<string | null>(null);
  const report = useAsync((signal) => programs.report(programId, token, term, signal), [programId, token, term, nonce]);
  const r = report.data;

  const download = async () => {
    setDownloading(true);
    setCsvError(null);
    try {
      const { blob, filename } = await programs.reportCsv(programId, token, term);
      saveBlob(blob, filename);
    } catch (e) {
      setCsvError((e as Error).message);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section className="panel" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Zero-textbook-cost report</h2>
      <p className="hint">
        A section counts as zero cost when every reading and assignment it uses comes from the Aletheia catalog, which
        students can read free. Savings are estimated at {r ? money(r.textbook_cost_per_student, r.currency) : "the textbook cost"} per
        student in each zero-cost section; change the amount under Program details.
      </p>
      <div className="report-tools">
        <div className="field">
          <label htmlFor={`${id}-term`}>Term</label>
          <select id={`${id}-term`} value={term} onChange={(e) => setTerm(e.target.value)}>
            <option value="">All terms</option>
            {(r?.terms ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
            {term && r && !r.terms.includes(term) && <option value={term}>{term}</option>}
          </select>
        </div>
        <button type="button" className="btn" onClick={download} disabled={downloading || !r}>
          {downloading ? "Preparing…" : "Download CSV"}
        </button>
      </div>
      {csvError && <ErrorMessage title="The CSV didn't download." error={csvError} />}
      {report.loading && !r && <Loading label="Building the report…" />}
      {report.error && <ErrorMessage title="The report didn't load." error={report.error} onRetry={report.reload} />}
      {r && (
        <div aria-busy={report.loading}>
          <dl className="stat-grid">
            <div>
              <dt>Sections</dt>
              <dd>{r.totals.sections}</dd>
            </div>
            <div>
              <dt>Students</dt>
              <dd>{r.totals.students}</dd>
            </div>
            <div>
              <dt>Sections at zero cost</dt>
              <dd>
                {r.totals.zero_cost_sections} of {r.totals.sections}
              </dd>
            </div>
            <div>
              <dt>Estimated student savings</dt>
              <dd>{money(r.totals.savings, r.currency)}</dd>
            </div>
          </dl>
          {r.warnings.length > 0 && (
            <div className="notice notice-warn">
              {r.warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          )}
          {r.rows.length === 0 ? (
            <p className="empty">{term ? `No sections for ${term}.` : "No sections yet."}</p>
          ) : (
            <div className="table-wrap" role="region" aria-labelledby={`${id}-cap`} tabIndex={0}>
              <table className="data-table">
                <caption id={`${id}-cap`} className="visually-hidden">
                  Zero-textbook-cost report{term ? `, ${term}` : ""}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Course</th>
                    <th scope="col">Section</th>
                    <th scope="col">Term</th>
                    <th scope="col">Instructor</th>
                    <th scope="col" className="num">Enrollment</th>
                    <th scope="col" className="num">Readings</th>
                    <th scope="col" className="num">Assignments</th>
                    <th scope="col" className="num">Open / link-only</th>
                    <th scope="col">Zero cost</th>
                    <th scope="col" className="num">Savings</th>
                  </tr>
                </thead>
                <tbody>
                  {r.rows.map((row) => (
                    <tr key={row.build_id}>
                      <td>
                        <Link to={`/build/${row.build_id}`}>{row.course || row.title}</Link>
                      </td>
                      <td>{row.section || "–"}</td>
                      <td>{row.term || "–"}</td>
                      <td>{row.instructor || "–"}</td>
                      <td className="num">{row.enrollment}</td>
                      <td className="num">{row.readings}</td>
                      <td className="num">{row.assignments}</td>
                      <td className="num">
                        {row.open} / {row.link_only}
                      </td>
                      <td>
                        {row.zero_cost ? "Yes" : "No"}
                        {!row.zero_cost && <span className="cell-sub">{whyNot(row)}</span>}
                      </td>
                      <td className="num">{money(row.savings, r.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
