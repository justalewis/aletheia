import { useEffect, useRef } from "react";
import { api, type AssignmentFull, type ReadingFull } from "../api";
import { useAsync } from "../hooks";
import { ErrorMessage, Loading } from "./Status";
import { TierBadge, TIER_HELP } from "./TierBadge";
import { kindLabel, levelLabel } from "./meta";

export type DetailRef = { kind: "reading" | "assignment"; id: string };

interface Props {
  item: DetailRef;
  onOpen: (ref: DetailRef) => void;
}

/** Full record for one reading or assignment, for the catalog's detail drawer. */
export function ItemDetail({ item, onOpen }: Props) {
  return item.kind === "reading" ? <ReadingDetail id={item.id} onOpen={onOpen} /> : <AssignmentDetail id={item.id} />;
}

/** Move focus to the record's title once it loads, so moving between records doesn't strand keyboard users. */
function DetailTitle({ children }: { children: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    ref.current?.focus({ preventScroll: false });
  }, [children]);
  return (
    <h3 className="detail-title" ref={ref} tabIndex={-1}>
      {children}
    </h3>
  );
}

function LicenseBlock({ tier, verified, license, licenseUrl }: { tier: string; verified: boolean; license: string; licenseUrl?: string }) {
  return (
    <div className="license-block">
      <p>
        <TierBadge tier={tier} verified={verified} />{" "}
        <span>
          {license || "License unknown"}
          {licenseUrl && (
            <>
              {" "}
              (<a href={licenseUrl} target="_blank" rel="noopener noreferrer">
                license text<span className="visually-hidden"> (opens in a new tab)</span>
              </a>
              )
            </>
          )}
        </span>
      </p>
      <p className="hint">
        {verified ? TIER_HELP[tier as keyof typeof TIER_HELP] : "We haven't verified this license yet, so it's treated as link only."}
      </p>
    </div>
  );
}

function ExtLink({ href, children }: { href: string; children: string }) {
  return (
    <a className="btn btn-small" href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <span className="visually-hidden"> (opens in a new tab)</span>
      <span aria-hidden="true"> ↗</span>
    </a>
  );
}

function Topics({ topics }: { topics: string[] }) {
  if (!topics.length) return null;
  return (
    <>
      <h3 className="h-tiny">Topics</h3>
      <ul className="tag-list">
        {topics.map((t) => (
          <li key={t} className="tag">
            {t}
          </li>
        ))}
      </ul>
    </>
  );
}

function ReadingDetail({ id, onOpen }: { id: string; onOpen: (r: DetailRef) => void }) {
  const res = useAsync((signal) => api.reading(id, signal), [id]);
  if (res.loading) return <Loading />;
  if (res.error || !res.data) return <ErrorMessage error={res.error ?? "Not found"} onRetry={res.reload} />;
  const r: ReadingFull = res.data;
  const links: [string, string][] = [];
  if (r.url) links.push([r.url, "Read online"]);
  if (r.pdf_url && r.pdf_url !== r.url) links.push([r.pdf_url, "PDF"]);
  if (r.epub_url && r.epub_url !== r.url) links.push([r.epub_url, "EPUB"]);

  return (
    <article className="detail">
      <DetailTitle>{r.title}</DetailTitle>
      <dl className="facts">
        {r.authors && (
          <>
            <dt>By</dt>
            <dd>{r.authors}</dd>
          </>
        )}
        {r.parent_title && (
          <>
            <dt>In</dt>
            <dd>
              {r.parent_id ? (
                <button type="button" className="link-button" onClick={() => onOpen({ kind: "reading", id: r.parent_id! })}>
                  {r.parent_title}
                </button>
              ) : (
                r.parent_title
              )}
            </dd>
          </>
        )}
        {r.year && (
          <>
            <dt>Year</dt>
            <dd>{r.year}</dd>
          </>
        )}
        <dt>Type</dt>
        <dd>
          {levelLabel(r.level)}
          {r.level !== "book" && r.level !== "collection" && r.level !== "journal" ? ` · about ${r.est_pages} pages` : ""}
        </dd>
        {r.publisher_platform && (
          <>
            <dt>Publisher</dt>
            <dd>{r.publisher_platform}</dd>
          </>
        )}
        {r.audience && (
          <>
            <dt>Written for</dt>
            <dd>{r.audience === "both" ? "Students and instructors" : r.audience === "instructor" ? "Instructors" : "Students"}</dd>
          </>
        )}
      </dl>
      <LicenseBlock tier={r.reuse_tier} verified={r.license_verified} license={r.license} licenseUrl={r.license_url} />
      {links.length > 0 && (
        <p className="link-row">
          {links.map(([href, label]) => (
            <ExtLink key={href} href={href}>
              {label}
            </ExtLink>
          ))}
        </p>
      )}
      {r.summary && <p className="prose">{r.summary}</p>}
      {r.notes && (
        <>
          <h3 className="h-tiny">Notes</h3>
          <p className="prose">{r.notes}</p>
        </>
      )}
      <Topics topics={r.topics} />
      {r.children.length > 0 && (
        <section aria-labelledby={`ch-${r.id}`}>
          <h3 id={`ch-${r.id}`} className="h-tiny">
            {r.level === "journal" ? "Articles" : "Chapters"} ({r.children.length}
            {r.children.length >= 300 ? "+" : ""})
          </h3>
          <ol className="chapter-list">
            {r.children.map((c) => (
              <li key={c.id}>
                <button type="button" className="link-button" onClick={() => onOpen({ kind: "reading", id: c.id })}>
                  {c.title}
                </button>{" "}
                <span className="meta">~{c.est_pages} pp.</span> <TierBadge tier={c.reuse_tier} verified={c.license_verified} />
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

function weeksText(w: string): string {
  if (w === "1") return "1 week";
  return /^\d+(-\d+)?$/.test(w) ? `${w.replace("-", "–")} weeks` : w;
}

function AssignmentDetail({ id }: { id: string }) {
  const res = useAsync((signal) => api.assignment(id, signal), [id]);
  if (res.loading) return <Loading />;
  if (res.error || !res.data) return <ErrorMessage error={res.error ?? "Not found"} onRetry={res.reload} />;
  const a: AssignmentFull = res.data;
  return (
    <article className="detail">
      <DetailTitle>{a.title}</DetailTitle>
      <dl className="facts">
        {a.authors && (
          <>
            <dt>By</dt>
            <dd>{a.authors}</dd>
          </>
        )}
        {a.source_repository && (
          <>
            <dt>From</dt>
            <dd>{a.source_repository}</dd>
          </>
        )}
        {a.year && (
          <>
            <dt>Year</dt>
            <dd>{a.year}</dd>
          </>
        )}
        {a.kind && (
          <>
            <dt>Kind</dt>
            <dd>
              {kindLabel(a.kind)}
              {a.assignment_type ? ` · ${a.assignment_type}` : ""}
            </dd>
          </>
        )}
        {a.estimated_weeks && (
          <>
            <dt>Length</dt>
            <dd>{weeksText(a.estimated_weeks)}</dd>
          </>
        )}
      </dl>
      <LicenseBlock tier={a.reuse_tier} verified={a.license_verified} license={a.license} licenseUrl={a.license_url} />
      <p className="link-row">
        {a.url && <ExtLink href={a.url}>Open the assignment</ExtLink>}
        {a.file_url && a.file_url !== a.url && <ExtLink href={a.file_url}>Download file</ExtLink>}
      </p>
      {a.summary && <p className="prose">{a.summary}</p>}
      {a.notes && (
        <>
          <h3 className="h-tiny">Notes</h3>
          <p className="prose">{a.notes}</p>
        </>
      )}
      <Topics topics={a.topics} />
      {a.pairs_with_readings.length > 0 && (
        <>
          <h3 className="h-tiny">Pairs with</h3>
          <ul className="plain-list">
            {a.pairs_with_readings.map((title) => (
              <li key={title}>{title}</li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}
