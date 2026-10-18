import { useEffect, useMemo, useState } from "react";
import { api, ApiError, TIERS, type BuildState, type LicenseReport } from "../api";
import { ErrorMessage, Loading } from "../components/Status";
import { TierBadge } from "../components/TierBadge";
import { selectedIds, unitBudget, unitPages } from "./model";

type ExportKind = "html" | "pdf" | "imscc-canvas" | "imscc-standard";

const EXPORTS: { kind: ExportKind; label: string; help: string }[] = [
  { kind: "html", label: "Reader (HTML)", help: "One web page with every reading and its credit line." },
  { kind: "pdf", label: "Reader (PDF)", help: "The same reader as a PDF for printing or posting." },
  {
    kind: "imscc-canvas",
    label: "Canvas package (.imscc)",
    help: "Modules, pages, and assignment shells. In Canvas: Settings, then Import Course Content.",
  },
  {
    kind: "imscc-standard",
    label: "Other LMS package (.imscc)",
    help: "Plain Common Cartridge for Moodle, Brightspace, Blackboard, and others.",
  },
];

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export function SidePanel({ state, briefVersion }: { state: BuildState; briefVersion: number }) {
  const sel = selectedIds(state);
  const selKey = JSON.stringify(sel);

  const totals = useMemo(() => {
    const pages = state.units.reduce((n, u) => n + unitPages(u), 0);
    const heavy = state.units.filter((u) => unitPages(u) > unitBudget(u, state.term)).length;
    return { pages, heavy };
    // briefVersion: page estimates change as briefs arrive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.units, state.term, briefVersion]);

  /* License report, debounced so ticking boxes doesn't fire a request per click. */
  const [report, setReport] = useState<LicenseReport | null>(null);
  const [reportError, setReportError] = useState<Error | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportNonce, setReportNonce] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setReportLoading(true);
    const t = window.setTimeout(() => {
      api
        .licenseReport(state, ctrl.signal)
        .then((r) => {
          setReport(r);
          setReportError(null);
          setReportLoading(false);
        })
        .catch((e: Error) => {
          if (ctrl.signal.aborted || e.name === "AbortError") return;
          setReportError(e);
          setReportLoading(false);
        });
    }, 600);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
    // Only the selected items matter to the report.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selKey, reportNonce]);

  /* Exports */
  const [busy, setBusy] = useState<ExportKind | null>(null);
  const [exportMsg, setExportMsg] = useState<Partial<Record<ExportKind, { text: string; tone: "ok" | "warn" | "error" }>>>({});
  const nothing = sel.readings.length + sel.assignments.length === 0;

  const runExport = async (kind: ExportKind) => {
    setBusy(kind);
    setExportMsg((m) => ({ ...m, [kind]: undefined }));
    try {
      const { blob, filename } = await api.exportFile(kind, state);
      download(blob, filename);
      setExportMsg((m) => ({ ...m, [kind]: { text: `Downloaded ${filename}.`, tone: "ok" } }));
    } catch (e) {
      const err = e as ApiError;
      const msg =
        kind === "pdf" && err.status === 501
          ? {
              text: "PDF export isn't set up on this server yet. Download the HTML reader and use your browser's Print to PDF instead.",
              tone: "warn" as const,
            }
          : { text: err.message || "The export didn't work. Try again in a moment.", tone: "error" as const };
      setExportMsg((m) => ({ ...m, [kind]: msg }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="side-panel">
      <section className="panel" aria-labelledby="totals-title">
        <h2 id="totals-title" className="h-small">
          This course
        </h2>
        <dl className="totals">
          <div>
            <dt>Units</dt>
            <dd>{state.units.length}</dd>
          </div>
          <div>
            <dt>Readings</dt>
            <dd>
              {sel.readings.length} checked, about {totals.pages} pages
            </dd>
          </div>
          <div>
            <dt>Assignments</dt>
            <dd>{sel.assignments.length} checked</dd>
          </div>
        </dl>
        {totals.heavy > 0 && (
          <p className="notice notice-warn small">
            {totals.heavy} {totals.heavy === 1 ? "unit is" : "units are"} over the reading budget.
          </p>
        )}
      </section>

      <section className="panel" aria-labelledby="license-title">
        <h2 id="license-title" className="h-small">
          Licenses
        </h2>
        {nothing ? (
          <p className="hint">Check some readings or assignments to see what their licenses allow.</p>
        ) : (
          <>
            {report && report.total > 0 && (
              <ul className="tier-counts" aria-label="Items by reuse label">
                {TIERS.filter((t) => report.tiers[t]).map((t) => (
                  <li key={t}>
                    <TierBadge tier={t} /> <span>{report.tiers[t]}</span>
                  </li>
                ))}
              </ul>
            )}
            {reportLoading && !report && <Loading label="Checking licenses…" />}
            {reportError && <ErrorMessage error={reportError} onRetry={() => setReportNonce((n) => n + 1)} />}
            {report && (
              <ul className={`statements${reportLoading ? " is-stale" : ""}`} aria-busy={reportLoading}>
                {report.statements.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <section className="panel" aria-labelledby="export-title">
        <h2 id="export-title" className="h-small">
          Export
        </h2>
        {nothing && <p className="hint">Nothing is checked yet, so there's nothing to export.</p>}
        <ul className="export-list">
          {EXPORTS.map((x) => (
            <li key={x.kind}>
              <button
                type="button"
                className="btn btn-block"
                disabled={nothing || busy !== null}
                aria-describedby={`export-help-${x.kind}`}
                onClick={() => runExport(x.kind)}
              >
                {busy === x.kind ? "Preparing…" : x.label}
              </button>
              <p id={`export-help-${x.kind}`} className="hint">
                {x.help}
              </p>
              <div role="status" aria-live="polite">
                {exportMsg[x.kind] && (
                  <p className={`notice notice-${exportMsg[x.kind]!.tone} small`}>{exportMsg[x.kind]!.text}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
        <p className="hint">Exports use what's on screen now, saved or not. Only checked items are included.</p>
      </section>
    </div>
  );
}
