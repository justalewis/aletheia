import type { ReactNode } from "react";
import type { ItemMatch } from "../api";
import { assignmentByline, kindLabel, levelLabel, readingByline } from "./meta";
import { TierBadge } from "./TierBadge";

const SHORT = new Set(["chapter", "article", "primary-text", "website"]);

/** A reading or assignment matched to an outcome: title (links out), byline, reuse badge, and why it matched. */
export function MatchCard({ item, heading: H = "h4", children }: { item: ItemMatch; heading?: "h3" | "h4" | "h5"; children?: ReactNode }) {
  const meta =
    item.item_type === "reading"
      ? [readingByline(item), levelLabel(item.level), SHORT.has(item.level) ? `~${item.est_pages} pp.` : ""]
      : [assignmentByline(item), kindLabel(item.kind)];
  return (
    <li className="result match-card">
      <H className="result-title">
        {item.url ? (
          <a href={item.url} target="_blank" rel="noopener noreferrer">
            {item.title}
            <span className="visually-hidden"> (opens in a new tab)</span>
          </a>
        ) : (
          item.title
        )}
      </H>
      <p className="meta">{meta.filter(Boolean).join(" · ")}</p>
      <p className="result-badges">
        <TierBadge tier={item.reuse_tier} verified={item.license_verified} />
        {item.license && <span className="meta">{item.license}</span>}
      </p>
      {item.why && <p className="match-why">{item.why}</p>}
      {children}
    </li>
  );
}
