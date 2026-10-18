import type { Tier } from "../api";
import { Icon, tierIcon } from "./Icon";

export const TIER_LABELS: Record<Tier, string> = {
  adapt: "Adapt",
  "adapt-nc": "Adapt (NC)",
  verbatim: "Use as is",
  "link-only": "Link only",
};

export const TIER_HELP: Record<Tier, string> = {
  adapt: "You can copy, edit, and remix it with credit.",
  "adapt-nc": "You can copy and edit it with credit, for noncommercial use only.",
  verbatim: "You can copy and share it whole, but not edit or excerpt it.",
  "link-only": "Free to read online. The reader links to it instead of copying it.",
};

export function tierLabel(t: string): string {
  return TIER_LABELS[t as Tier] ?? t;
}

/** Reuse-tier badge: a fill glyph plus the text label. The label carries the meaning; the glyph only reinforces it. */
export function TierBadge({ tier, verified = true }: { tier: Tier | string; verified?: boolean }) {
  const t = (tier in TIER_LABELS ? tier : "link-only") as Tier;
  const help = verified || t !== "link-only" ? TIER_HELP[t] : "License not yet verified, so it's treated as link only.";
  return (
    <span className={`tier tier-${t}`} title={help}>
      <Icon name={tierIcon(t)} size={16} />
      <span className="visually-hidden">Reuse: </span>
      {TIER_LABELS[t]}
    </span>
  );
}
