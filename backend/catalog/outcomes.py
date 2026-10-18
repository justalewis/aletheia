"""Outcomes in, reading list out.

Deterministic outcome matching. No model or external API calls; everything comes from catalog tags
and the sample arcs.

1. Crosswalk. corpus/<pack>/outcomes/crosswalk.yaml maps catalog topics to framework codes (wpa:,
   acrl:, tc:) in weighted tiers. `load_crosswalk` parses and checks it; load_pack stores the result
   on the Pack row.
2. Item scores. At pack-load time `score_items` gives every reading and assignment a score per code,
   from its topics (through the crosswalk) and from arc units that use it (through each unit's
   `outcomes`), and stores scores, evidence, and a flattened code list on the row.
3. Ranking. At request time `rank` scores candidates against one outcome. Relevance signals add up;
   quality factors (audience, level, license, length) scale the sum; fixed keys break ties.
   `diversify` caps how many items one book or one repository contributes to a list.
4. Recommendations and coverage. `recommend` builds per-outcome reading lists. `coverage` maps a
   build's checked items to outcomes unit by unit, finds gaps, and suggests items to fill them.

A semantic layer (embeddings) slots in as one more relevance signal: write a function with the
`RelevanceSignal` signature and append it to RELEVANCE_SIGNALS. Ranking, coverage, and the API pick
it up without other changes.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml
from django.db.models import Q

from builder.planning import estimate_pages
from catalog.models import (
    Assignment, Outcome, OutcomeSet, Pack, Reading, UnitAssignment, UnitReading,
)
from catalog.serializers import assignment_brief, reading_brief

Item = Reading | Assignment

DEFAULT_WEIGHTS = {
    "core": 0.8, "strong": 0.5, "related": 0.25,
    "arc_core": 1.5, "arc_optional": 1.2, "arc_focus": 3, "arc_extra": 0.15, "arc_unconfirmed": 0.5,
    "map_threshold": 0.5,
}
TIERS = ("core", "strong", "related")
# Arc roles that carry a unit (core readings, the major project) count for more than the rest.
ARC_ROLE_WEIGHT = {"core": "arc_core", "major": "arc_core",
                   "optional": "arc_optional", "short_term": "arc_optional", "activity": "arc_optional"}
MAX_EXTRA_UNITS = 2

# Ranking constants.
SECONDARY_CODE_SHARE = 0.3   # an outcome with several codes: the best code counts fully, others at 30%
COURSE_TOPIC_STEP = 0.5      # course-outcome topic overlap: 1 - 0.5^matches
READING_GROUP_CAP = 2        # readings from one book per outcome list
ASSIGNMENT_GROUP_CAP = 3     # assignments from one repository per outcome list
SUGGESTION_GROUP_CAP = 2
SUGGESTIONS_PER_GAP = 5
WEAK_COUNT = 2               # fewer mapped items than this is thin coverage
WEAK_STRENGTH = 1.0          # ...and so is a summed score below this
UNIT_CODE_FIT = 2.0          # a unit that already lists one of the outcome's codes


# ---------------------------------------------------------------------------- crosswalk

def load_crosswalk(path: Path, known_codes: Iterable[str] | None = None,
                   known_topics: Iterable[str] | None = None) -> tuple[dict, list[str]]:
    """Parse crosswalk.yaml into {"weights": {...}, "codes": {code: {topic: weight}}}.

    Returns the crosswalk and a list of warnings (unknown codes, topics no item uses, bad tiers).
    A missing file gives an empty crosswalk, so items are scored from arcs alone.
    """
    problems: list[str] = []
    if not path.exists():
        return {"weights": dict(DEFAULT_WEIGHTS), "codes": {}}, [f"no crosswalk at {path}; outcome scores use arcs only"]
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    weights = dict(DEFAULT_WEIGHTS)
    for k, v in (doc.get("weights") or {}).items():
        try:
            weights[k] = float(v)
        except (TypeError, ValueError):
            problems.append(f"crosswalk: weight {k} is not a number")
    codes_known = set(known_codes) if known_codes is not None else None
    topics_known = {t.lower() for t in known_topics} if known_topics is not None else None
    codes: dict[str, dict[str, float]] = {}
    for code, tiers in (doc.get("codes") or {}).items():
        if codes_known is not None and code not in codes_known:
            problems.append(f"crosswalk: {code} is not defined in frameworks.yaml")
        tmap: dict[str, float] = {}
        for tier, topics in (tiers or {}).items():
            if tier not in TIERS:
                problems.append(f"crosswalk: {code} has unknown tier {tier!r}")
                continue
            for t in topics or []:
                t = str(t).strip().lower()
                if topics_known is not None and t not in topics_known:
                    problems.append(f"crosswalk: {code} lists {t!r}, which no reading or assignment uses")
                tmap[t] = max(tmap.get(t, 0.0), weights[tier])
        codes[code] = tmap
    return {"weights": weights, "codes": codes}, problems


def weights_of(crosswalk: dict | None) -> dict:
    return {**DEFAULT_WEIGHTS, **((crosswalk or {}).get("weights") or {})}


def topic_index(crosswalk: dict) -> dict[str, list[tuple[str, float]]]:
    """topic -> [(code, weight)]"""
    idx: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for code, tmap in (crosswalk.get("codes") or {}).items():
        for t, w in tmap.items():
            idx[t].append((code, w))
    return idx


def code_profile(crosswalk: dict, codes: Iterable[str]) -> dict[str, float]:
    """Topic weights for a set of codes (the strongest tier wins)."""
    out: dict[str, float] = {}
    for c in codes:
        for t, w in ((crosswalk.get("codes") or {}).get(c) or {}).items():
            out[t] = max(out.get(t, 0.0), w)
    return out


def noisy_or(weights: Iterable[float]) -> float:
    """1 - prod(1 - w): each extra piece of evidence adds less than the one before."""
    return 1.0 - math.prod(1.0 - min(max(w, 0.0), 1.0) for w in weights)


# ---------------------------------------------------------------------------- load-time item scores

def arc_evidence(pack: Pack, weights: dict) -> dict[tuple[str, str], dict[str, list[dict]]]:
    """(kind, item id) -> {code: [unit dicts]} for every arc unit that uses the item."""
    out: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    def add(kind: str, item_id: str, unit, role: str):
        codes = list(dict.fromkeys(unit.outcomes or []))
        # A unit that claims many outcomes says less about each one.
        share = min(1.0, weights["arc_focus"] / len(codes)) if codes else 1.0
        w = round(weights[ARC_ROLE_WEIGHT.get(role, "arc_optional")] * share, 3)
        for code in codes:
            out[(kind, item_id)][code].append({
                "arc": unit.arc.id, "arc_title": unit.arc.title, "unit": unit.order + 1,
                "unit_title": unit.title, "role": role, "w": w})

    for ur in UnitReading.objects.filter(unit__arc__pack=pack).select_related("unit__arc"):
        add("r", ur.reading_id, ur.unit, ur.role)
    for ua in UnitAssignment.objects.filter(unit__arc__pack=pack).select_related("unit__arc"):
        add("a", ua.assignment_id, ua.unit, ua.role)
    return out


def item_scores(topics: Iterable[str], arc_units: dict[str, list[dict]], idx: dict, weights: dict) -> tuple[dict, dict]:
    """Per-code scores and evidence for one item: topic score + arc score."""
    hits: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for t in {str(t).strip().lower() for t in topics or []}:
        for code, w in idx.get(t, ()):
            hits[code].append((w, t))
    scores: dict[str, float] = {}
    evidence: dict[str, dict] = {}
    for code in sorted(set(hits) | set(arc_units)):
        th = sorted(hits.get(code, []), key=lambda x: (-x[0], x[1]))
        t_score = noisy_or(w for w, _ in th)
        units = sorted(arc_units.get(code, []), key=lambda u: (-u["w"], u["arc_title"], u["unit"]))
        distinct: list[dict] = []
        for u in units:
            if not any(d["arc"] == u["arc"] and d["unit"] == u["unit"] for d in distinct):
                distinct.append(u)
        a_score = 0.0
        if distinct:
            a_score = distinct[0]["w"] + weights["arc_extra"] * min(MAX_EXTRA_UNITS, len(distinct) - 1)
            # Arc evidence counts fully once the item's own topics agree (one strong topic is enough).
            agree = min(1.0, t_score / weights["strong"]) if weights["strong"] > 0 else 1.0
            a_score *= weights["arc_unconfirmed"] + (1 - weights["arc_unconfirmed"]) * agree
        total = round(t_score + a_score, 3)
        if total <= 0:
            continue
        scores[code] = total
        ev: dict = {"topic_score": round(t_score, 3), "arc_score": round(a_score, 3)}
        if th:
            ev["topics"] = [t for _, t in th]
        if distinct:
            ev["units"] = [{k: u[k] for k in ("arc", "arc_title", "unit", "unit_title", "role")} for u in distinct[:3]]
        evidence[code] = ev
    return scores, evidence


def outcomes_text(scores: dict) -> str:
    return "|" + "|".join(sorted(scores)) + "|" if scores else ""


def score_items(pack: Pack, crosswalk: dict) -> int:
    """Compute and store outcome scores for every reading and assignment in the pack."""
    weights = weights_of(crosswalk)
    idx = topic_index(crosswalk)
    arcs = arc_evidence(pack, weights)
    fields = ["outcome_scores", "outcome_evidence", "outcomes_text"]
    count = 0
    for model, kind in ((Reading, "r"), (Assignment, "a")):
        rows = list(model.objects.filter(pack=pack).only("id", "topics"))
        for row in rows:
            row.outcome_scores, row.outcome_evidence = item_scores(row.topics, arcs.get((kind, row.id), {}), idx, weights)
            row.outcomes_text = outcomes_text(row.outcome_scores)
        model.objects.bulk_update(rows, fields, batch_size=500)
        count += len(rows)
    return count


# ---------------------------------------------------------------------------- outcomes

@dataclass
class OutcomeSpec:
    """One outcome as the ranker sees it: framework codes plus (for course outcomes) its own topics."""
    id: str
    set_id: str
    label: str
    text: str
    codes: list[str]
    topics: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"id": self.id, "set_id": self.set_id, "label": self.label, "text": self.text,
                "codes": self.codes, "topics": self.topics}


def spec_for(o: Outcome, s: OutcomeSet) -> OutcomeSpec:
    label, text = f"Outcome {o.order + 1}", o.text
    if s.kind == "framework":
        entries = s.data.get("categories") or s.data.get("frames") or s.data.get("outcomes") or []
        name = next((e.get("name") for e in entries if e.get("code") == o.id), "") or ""
        if name:
            label = name
            if text.startswith(f"{name}: "):
                text = text[len(name) + 2:]
    codes = list(o.frameworks or ([o.id] if s.kind == "framework" else []))
    topics = [str(t).strip().lower() for t in (o.topics or [])]
    return OutcomeSpec(o.id, s.id, label, text, codes, topics)


def set_brief(s: OutcomeSet, count: int) -> dict:
    urls = s.data.get("source_urls") or []
    source = s.data.get("source_url") or (urls[0] if urls else "")
    return {"id": s.id, "kind": s.kind, "title": s.title, "institution": s.institution, "course": s.course,
            "count": count, "source_url": source}


def specs_for_sets(pack: Pack, set_ids: Iterable[str]) -> tuple[list[dict], list[OutcomeSpec], list[str]]:
    """Outcome sets (in the order asked), their outcome specs, and any ids that don't exist."""
    ids = list(dict.fromkeys(set_ids))
    found = {s.id: s for s in OutcomeSet.objects.filter(pack=pack, id__in=ids).prefetch_related("outcomes")}
    sets, specs, unknown = [], [], []
    for sid in ids:
        s = found.get(sid)
        if s is None:
            unknown.append(sid)
            continue
        outs = list(s.outcomes.all())
        sets.append(set_brief(s, len(outs)))
        specs.extend(spec_for(o, s) for o in outs)
    return sets, specs, unknown


def specs_for_ids(pack: Pack, outcome_ids: Iterable[str]) -> tuple[list[OutcomeSpec], list[str]]:
    ids = list(dict.fromkeys(outcome_ids))
    found = {o.id: o for o in Outcome.objects.filter(outcome_set__pack=pack, id__in=ids).select_related("outcome_set")}
    specs = [spec_for(found[i], found[i].outcome_set) for i in ids if i in found]
    return specs, [i for i in ids if i not in found]


# ---------------------------------------------------------------------------- ranking

@dataclass
class Evidence:
    units: list[dict] = field(default_factory=list)   # arc units that used the item for this outcome
    topics: list[str] = field(default_factory=list)   # item topics that point at the outcome
    fit_unit: str = ""                                # coverage: the unit a suggestion is meant for
    fit_topics: list[str] = field(default_factory=list)

    def merge(self, other: "Evidence") -> None:
        for u in other.units:
            if not any(x["arc"] == u["arc"] and x["unit"] == u["unit"] for x in self.units):
                self.units.append(u)
        self.topics.extend(t for t in other.topics if t not in self.topics)
        if other.fit_topics:
            self.fit_unit = other.fit_unit
            self.fit_topics.extend(t for t in other.fit_topics if t not in self.fit_topics)

    def why(self) -> str:
        parts = []
        if self.units:
            u = self.units[0]
            parts.append(f"Used for this outcome in {u['arc_title']}, unit {u['unit']}")
        if self.topics:
            parts.append("Topics: " + ", ".join(self.topics[:3]))
        if self.fit_topics:
            parts.append(f"Fits the {self.fit_unit} unit: " + ", ".join(self.fit_topics[:2]))
        return ". ".join(parts)


@dataclass
class Context:
    """Request-level settings the signals and factors can read."""
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    focus: str = ""                                    # soft focus boost (coverage); hard filter elsewhere
    fit_unit: str = ""
    fit_topics: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RelevanceSignal:
    """One source of evidence that an item serves an outcome.

    `score` runs for every candidate, so keep it cheap. `explain` runs only for the items that make
    a list and returns what the "why" line should say. A semantic signal would score by embedding
    similarity between the outcome text and the item's title and summary, and explain with nothing
    (or with the matching passage).
    """
    name: str
    score: Callable[[Item, "OutcomeSpec", Context], float]
    explain: Callable[[Item, "OutcomeSpec", Context], Evidence]


QualityFactor = Callable[[Item, Context], float]


def item_topics(item: Item) -> list[str]:
    cached = getattr(item, "_outcome_topics", None)
    if cached is None:
        cached = [str(t).strip().lower() for t in item.topics or []]
        item._outcome_topics = cached
    return cached


def framework_score(item: Item, spec: OutcomeSpec, ctx: Context) -> float:
    """The item's stored score for the outcome's codes (topics through the crosswalk, plus arcs).
    With several codes, the best counts in full and the others at SECONDARY_CODE_SHARE."""
    scores = item.outcome_scores or {}
    vals = [scores[c] for c in spec.codes if scores.get(c, 0.0) > 0]
    if not vals:
        return 0.0
    best = max(vals)
    return best + SECONDARY_CODE_SHARE * (sum(vals) - best)


def framework_evidence(item: Item, spec: OutcomeSpec, ctx: Context) -> Evidence:
    scores, stored = item.outcome_scores or {}, item.outcome_evidence or {}
    ev = Evidence()
    for c in sorted((c for c in spec.codes if scores.get(c, 0.0) > 0), key=lambda c: (-scores[c], c)):
        e = stored.get(c) or {}
        ev.merge(Evidence(units=list(e.get("units", [])), topics=list(e.get("topics", []))))
    return ev


def course_topic_matches(item: Item, spec: OutcomeSpec) -> list[str]:
    if not spec.topics:
        return []
    return [t for t in item_topics(item) if t in spec.topics]


def course_topic_score(item: Item, spec: OutcomeSpec, ctx: Context) -> float:
    """Overlap between the item's topics and a course outcome's own topic list."""
    n = len(course_topic_matches(item, spec))
    return 1.0 - COURSE_TOPIC_STEP ** n if n else 0.0


def course_topic_evidence(item: Item, spec: OutcomeSpec, ctx: Context) -> Evidence:
    return Evidence(topics=course_topic_matches(item, spec))


# Course topics come first so the "why" line leads with the outcome's own words. Add a semantic
# signal here when there is one.
RELEVANCE_SIGNALS: list[RelevanceSignal] = [
    RelevanceSignal("course_topics", course_topic_score, course_topic_evidence),
    RelevanceSignal("framework", framework_score, framework_evidence),
]

LEVEL_FACTORS = {"chapter": 1.1, "article": 1.1, "primary-text": 1.1, "website": 1.0,
                 "book": 0.85, "journal": 0.7, "collection": 0.7}
AUDIENCE_FACTORS = {"student": 1.1, "both": 1.0, "instructor": 0.8}


def audience_factor(item: Item, ctx: Context) -> float:
    return AUDIENCE_FACTORS.get(getattr(item, "audience", "") or "", 1.0)


def level_factor(item: Item, ctx: Context) -> float:
    return LEVEL_FACTORS.get(item.level, 1.0) if isinstance(item, Reading) else 1.0


def license_factor(item: Item, ctx: Context) -> float:
    """Redistributable items with a verified license can go in the reader itself."""
    return 1.15 if item.redistributable else 1.0


def item_pages(item: Item) -> int:
    return estimate_pages({"pages": item.pages, "level": item.level}) if isinstance(item, Reading) else 0


def length_factor(item: Item, ctx: Context) -> float:
    pages = item_pages(item)
    return 0.8 if pages > 80 else 0.9 if pages > 40 else 1.0


def focus_factor(item: Item, ctx: Context) -> float:
    return 1.15 if ctx.focus and ctx.focus in (item.skins or []) else 1.0


def unit_fit_factor(item: Item, ctx: Context) -> float:
    if not ctx.fit_topics:
        return 1.0
    shared = sum(1 for t in set(item_topics(item)) if t in ctx.fit_topics)
    return 1.0 + 0.15 * min(3, shared)


QUALITY_FACTORS: list[QualityFactor] = [audience_factor, level_factor, license_factor, length_factor,
                                        focus_factor, unit_fit_factor]


@dataclass
class Scored:
    item: Item
    score: float
    relevance: float
    spec: OutcomeSpec = field(repr=False, compare=False)
    ctx: Context = field(repr=False, compare=False)

    @property
    def item_type(self) -> str:
        return "reading" if isinstance(self.item, Reading) else "assignment"

    @property
    def evidence(self) -> Evidence:
        return explain(self.item, self.spec, self.ctx)


def relevance_of(item: Item, spec: OutcomeSpec, ctx: Context) -> float:
    return sum(sig.score(item, spec, ctx) for sig in RELEVANCE_SIGNALS)


def explain(item: Item, spec: OutcomeSpec, ctx: Context) -> Evidence:
    ev = Evidence()
    for sig in RELEVANCE_SIGNALS:
        if sig.score(item, spec, ctx) > 0:
            ev.merge(sig.explain(item, spec, ctx))
    if ctx.fit_topics:
        shared = [t for t in item_topics(item) if t in ctx.fit_topics]
        if shared:
            ev.merge(Evidence(fit_unit=ctx.fit_unit, fit_topics=shared))
    return ev


def score_one(item: Item, spec: OutcomeSpec, ctx: Context, floor: float = 0.0) -> Scored | None:
    """Relevance (summed signals) times quality (multiplied factors); None below the floor."""
    rel = relevance_of(item, spec, ctx)
    if rel <= 0 or rel < floor:
        return None
    quality = math.prod(f(item, ctx) for f in QUALITY_FACTORS)
    return Scored(item, round(rel * quality, 4), round(rel, 4), spec, ctx)


def sort_key(s: Scored):
    """Highest score first; then stronger raw relevance, shorter reading, title, id."""
    return (-s.score, -s.relevance, item_pages(s.item), (s.item.title or "").lower(), s.item.id)


def rank(items: Iterable[Item], spec: OutcomeSpec, ctx: Context | None = None,
         exclude: Iterable[str] = (), min_relevance: float | None = None) -> list[Scored]:
    """Every item that matches the outcome at or above the threshold, best first."""
    ctx = ctx or Context()
    floor = ctx.weights["map_threshold"] if min_relevance is None else min_relevance
    skip = set(exclude)
    out = []
    for item in items:
        if item.id in skip:
            continue
        s = score_one(item, spec, ctx, floor)
        if s:
            out.append(s)
    out.sort(key=sort_key)
    return out


VOLUME_SUFFIX = re.compile(r",?\s*\b(?:volume|vol\.|issue|no\.|number)\s*\d+\b.*$", re.IGNORECASE)


def series_title(title: str) -> str:
    """"Writing Spaces: Readings on Writing, Volume 4" -> "writing spaces: readings on writing";
    journal issues ("..., Issue 4") collapse the same way."""
    return VOLUME_SUFFIX.sub("", (title or "").strip()).strip().lower()


def group_key(item: Item) -> str:
    """Readings group by parent book, treating numbered volumes of a series as one book (a book groups
    with its own chapters); assignments group by repository."""
    if isinstance(item, Reading):
        if item.parent_title:
            return "r:" + series_title(item.parent_title)
        if item.level in ("book", "collection", "journal"):
            return "r:" + series_title(item.title)
        return "r:" + (item.parent_id or item.id)
    return "a:" + ((item.source_repository or "").strip().lower() or item.id)


def diversify(ranked: list[Scored], limit: int, cap: int) -> list[Scored]:
    """Take the top `limit`, at most `cap` per book or repository. If that leaves the list short,
    fill it from the skipped items in rank order."""
    picked, skipped, per = [], [], defaultdict(int)
    for s in ranked:
        if len(picked) >= limit:
            break
        g = group_key(s.item)
        if per[g] < cap:
            picked.append(s)
            per[g] += 1
        else:
            skipped.append(s)
    if len(picked) < limit:
        picked.extend(skipped[: limit - len(picked)])
        picked.sort(key=sort_key)
    return picked


# ---------------------------------------------------------------------------- candidates

def _tier_filter(qs, tiers: list[str]):
    from catalog.api import _filter  # shared tier semantics: unverified licenses count as link-only
    return _filter(qs, "", "", ",".join(tiers), "") if tiers else qs


def candidates(pack: Pack, specs: list[OutcomeSpec], *, focus: str = "", tiers: Iterable[str] = (),
               levels: Iterable[str] = ()) -> tuple[list[Reading], list[Assignment]]:
    """Readings and assignments that could match any of the specs, after the hard filters."""
    cond = Q()
    for c in sorted({c for s in specs for c in s.codes}):
        cond |= Q(outcomes_text__contains=f"|{c}|")
    for t in sorted({t for s in specs for t in s.topics}):
        cond |= Q(topics_text__contains=f"|{t}|")
    if not cond.children:
        return [], []
    tiers, levels = [t for t in tiers if t], [lv for lv in levels if lv]
    out = []
    for model in (Reading, Assignment):
        qs = model.objects.filter(pack=pack, withdrawn=False).filter(cond).defer("notes", "search_text")
        if focus:
            qs = qs.filter(skins_text__contains=f"|{focus}|")
        qs = _tier_filter(qs, tiers)
        if levels and model is Reading:
            qs = qs.filter(level__in=levels)
        out.append(list(qs))
    return out[0], out[1]


def scored_dict(s: Scored) -> dict:
    d =reading_brief(s.item) if isinstance(s.item, Reading) else assignment_brief(s.item)
    # item_type, not kind: assignment briefs already use "kind" for the assignment's kind.
    d.update({"item_type": s.item_type, "score": s.score, "why": s.evidence.why()})
    return d


# ---------------------------------------------------------------------------- recommend

def recommend(pack: Pack, specs: list[OutcomeSpec], *, focus: str = "", tiers: Iterable[str] = (),
              levels: Iterable[str] = (), per_outcome: int = 8, exclude_ids: Iterable[str] = ()) -> list[dict]:
    ctx = Context(weights=weights_of(pack.crosswalk))
    readings, assignments = candidates(pack, specs, focus=focus, tiers=tiers, levels=levels)
    exclude = set(exclude_ids)
    out = []
    for spec in specs:
        rs = rank(readings, spec, ctx, exclude)
        az = rank(assignments, spec, ctx, exclude)
        out.append({
            **spec.as_dict(),
            "readings": [scored_dict(s) for s in diversify(rs, per_outcome, READING_GROUP_CAP)],
            "assignments": [scored_dict(s) for s in diversify(az, per_outcome, ASSIGNMENT_GROUP_CAP)],
            "total": {"readings": len(rs), "assignments": len(az)},
        })
    return out


# ---------------------------------------------------------------------------- coverage

@dataclass
class StateUnit:
    key: str
    title: str
    topics: list[str]
    outcomes: list[str]
    readings: list[str]       # selected reading ids, in order
    assignments: list[str]    # selected assignment ids, in order


def state_units(state: dict) -> list[StateUnit]:
    out = []
    for i, u in enumerate(state.get("units") or []):
        if not isinstance(u, dict):
            continue
        out.append(StateUnit(
            key=str(u.get("key") or f"unit-{i + 1}"),
            title=str(u.get("title") or f"Unit {i + 1}"),
            topics=[str(t).strip().lower() for t in u.get("topics") or []],
            outcomes=[str(c) for c in u.get("outcomes") or []],
            readings=list(dict.fromkeys(r["id"] for r in u.get("readings") or [] if isinstance(r, dict) and r.get("id") and r.get("selected"))),
            assignments=list(dict.fromkeys(a["id"] for a in u.get("assignments") or [] if isinstance(a, dict) and a.get("id") and a.get("selected"))),
        ))
    return out


def best_unit(spec: OutcomeSpec, units: list[StateUnit], items: dict[str, Item], crosswalk: dict) -> tuple[StateUnit | None, frozenset[str]]:
    """The unit where a gap could best be filled, and the topics that describe that unit.

    Fit: the unit already lists one of the outcome's codes (arc units do), its topics match the
    outcome's topic profile, and so do the topics of its checked items (at a quarter weight). Ties
    go to the unit with fewer checked items, then to the earlier unit.
    """
    if not units:
        return None, frozenset()
    profile = code_profile(crosswalk, spec.codes)
    for t in spec.topics:
        profile[t] = max(profile.get(t, 0.0), 1.0)

    def item_topic_set(u: StateUnit) -> set[str]:
        return {t for i in u.readings + u.assignments if i in items for t in item_topics(items[i])}

    best, best_key = None, None
    for i, u in enumerate(units):
        fit = UNIT_CODE_FIT if set(u.outcomes) & set(spec.codes) else 0.0
        fit += sum(profile.get(t, 0.0) for t in set(u.topics))
        fit += 0.25 * sum(profile.get(t, 0.0) for t in item_topic_set(u))
        key = (-round(fit, 6), len(u.readings) + len(u.assignments), i)
        if best_key is None or key < best_key:
            best, best_key = u, key
    return best, frozenset(best.topics) if best.topics else frozenset(item_topic_set(best))


def coverage(pack: Pack, state: dict, set_ids: Iterable[str]) -> dict:
    crosswalk = pack.crosswalk or {}
    ctx = Context(weights=weights_of(crosswalk))
    threshold = ctx.weights["map_threshold"]
    units = state_units(state)
    r_ids = {i for u in units for i in u.readings}
    a_ids = {i for u in units for i in u.assignments}
    items: dict[str, Item] = {r.id: r for r in Reading.objects.filter(pack=pack, id__in=r_ids)}
    items.update({a.id: a for a in Assignment.objects.filter(pack=pack, id__in=a_ids)})
    sets, specs, unknown = specs_for_sets(pack, set_ids)

    rows = []
    for spec in specs:
        cells, mapped = [], {}
        for u in units:
            hit_r, hit_a = [], []
            for ids, hits in ((u.readings, hit_r), (u.assignments, hit_a)):
                for i in ids:
                    item = items.get(i)
                    if item is None:
                        continue
                    rel = relevance_of(item, spec, ctx)
                    if rel >= threshold:
                        hits.append(i)
                        mapped[i] = max(mapped.get(i, 0.0), rel)
            cells.append({"unit_key": u.key, "count": len(hit_r) + len(hit_a),
                          "reading_ids": hit_r, "assignment_ids": hit_a})
        total, strength = len(mapped), round(sum(mapped.values()), 3)
        status = "none" if total == 0 else "weak" if total < WEAK_COUNT or strength < WEAK_STRENGTH else "ok"
        rows.append({**spec.as_dict(), "cells": cells, "total": total, "strength": strength, "status": status})

    gaps = []
    gap_specs = [s for s, row in zip(specs, rows) if row["status"] != "ok"]
    if gap_specs:
        readings, assignments = candidates(pack, gap_specs)
        selected = r_ids | a_ids
        for spec in gap_specs:
            row = next(r for r in rows if r["id"] == spec.id and r["set_id"] == spec.set_id)
            unit, fit = best_unit(spec, units, items, crosswalk)
            gctx = Context(weights=ctx.weights, focus=state.get("focus") or "",
                           fit_unit=unit.title if unit else "", fit_topics=fit)
            ranked = rank(readings, spec, gctx, selected) + rank(assignments, spec, gctx, selected)
            ranked.sort(key=sort_key)
            picks = diversify(ranked, SUGGESTIONS_PER_GAP, SUGGESTION_GROUP_CAP)
            gaps.append({
                "outcome_id": spec.id, "set_id": spec.set_id, "label": spec.label, "status": row["status"],
                "total": row["total"],
                "unit_key": unit.key if unit else None, "unit_title": unit.title if unit else None,
                "suggestions": [scored_dict(s) for s in picks],
            })

    return {
        "units": [{"key": u.key, "title": u.title} for u in units],
        "sets": sets,
        "unknown_set_ids": unknown,
        "outcomes": rows,
        "gaps": gaps,
    }
