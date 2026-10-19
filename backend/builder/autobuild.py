"""Start from your course: a theme and/or course outcomes in, a planned course out.

Deterministic, no model or network calls (a semantic signal can slot into `item_score` later, as in
catalog/outcomes.py).

1. Read the request. The theme, course outcomes (typed, or a stored set), and course name become
   weighted word stems; each outcome line is also matched to the catalog's topic vocabulary.
2. Pick the arc. Arcs in the chosen focus are scored by how well their units' text, outcomes codes,
   and readings match; the best one is planned over the term like any sample course.
3. Tailor it. Theme readings the catalog has plenty of are added to the unit they fit best. Then
   outcome coverage runs on the result, and the best suggestions for each gap go into the unit that
   suits them.

The result is an ordinary build state the course builder opens; every choice can be changed there.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from django.db.models import Q

from builder.planning import Term
from catalog import outcomes as oc
from catalog.models import Arc, Assignment, Pack, Reading
from catalog.serializers import arc_units_for_planning

MAX_THEME_CHARS = 2000
MAX_OUTCOMES_CHARS = 8000
MAX_OUTCOME_LINES = 30
THEME_FLOOR = 2.0            # a reading must match the theme at least this well to be added
THEME_PER_UNIT = 3
OUTCOME_PICKS_PER_GAP = 2
THEME_WEIGHT, OUTCOME_WEIGHT, NAME_WEIGHT = 1.0, 0.6, 0.3

STOP = frozenset("""a about above after again all also an and any are as at be because been before being between both but by can
could course courses did do does during each few for from further had has have having how if in into is it its just may
more most must no nor not of off on once only or other our out over own same should so some such than that the their them
then there these they this those through to too under until up use used using very was we were what when where which while
who whom why will with would you your students student class classes learn learning able skills skill""".split())
# Words that appear in nearly every record of this catalog, so they say nothing about a theme.
DOMAIN_STOP = frozenset("write writing writer writers written text texts reading readings read composition rhetoric".split())


def stem(word: str) -> str:
    w = word.lower().strip("'’")
    if w.endswith(("'s", "’s")):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str, drop_domain: bool = False) -> list[str]:
    out = []
    for w in re.findall(r"[a-z][a-z'’-]*[a-z]|[a-z]", (text or "").lower()):
        for part in w.split("-"):
            if len(part) < 3 or part in STOP or (drop_domain and part in DOMAIN_STOP):
                continue
            out.append(stem(part))
    return out


def prefix(s: str) -> str:
    """Database prefilter: a stem's start, so 'technology' finds 'technologies'."""
    return s[:-1] if len(s) >= 6 else s


def clean_outcome_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•–—]+|\d+[.)]|[a-z][.)]|students will:?|upon completion[^:]*:)\s*", "", raw.strip(), flags=re.I)
        if len(line) >= 8:
            lines.append(line[:400])
    return lines[:MAX_OUTCOME_LINES]


@dataclass
class Request:
    theme: str = ""
    outcomes_text: str = ""
    outcome_set_ids: list[str] = field(default_factory=list)
    course_name: str = ""
    focus: str = ""
    arc_id: str = ""
    term: Term = field(default_factory=lambda: Term(16))


def query_weights(req: Request) -> dict[str, float]:
    w: dict[str, float] = {}
    for text, weight in ((req.course_name, NAME_WEIGHT), (req.outcomes_text, OUTCOME_WEIGHT), (req.theme, THEME_WEIGHT)):
        for t in tokens(text, drop_domain=True):
            w[t] = max(w.get(t, 0.0), weight)
    return w


def topic_vocabulary(pack: Pack) -> dict[str, frozenset[str]]:
    """Every catalog topic with the word stems it needs: 'research question' -> {research, question}."""
    seen: set[str] = set()
    for model in (Reading, Assignment):
        for topics in model.objects.filter(pack=pack, withdrawn=False).values_list("topics", flat=True):
            seen.update(str(t).strip().lower() for t in topics or [])
    return {t: frozenset(tokens(t)) for t in seen if tokens(t)}


def matching_topics(text: str, vocab: dict[str, frozenset[str]]) -> list[str]:
    have = set(tokens(text))
    return sorted(t for t, need in vocab.items() if need <= have)


# ---------------------------------------------------------------------------- items

LEVEL_FACTOR = {"chapter": 1.1, "article": 1.1, "primary-text": 1.1, "website": 0.9, "book": 0.5,
                "collection": 0.4, "journal": 0.4}
AUDIENCE_FACTOR = {"student": 1.1, "both": 1.0, "instructor": 0.6}


def item_score(item: Reading, weights: dict[str, float], arc_level: str) -> float:
    """How well a reading's title, topics, and summary match the query words, times quality factors.

    A title hit is worth 3, a topic hit 2, a summary hit 1. The item must earn a fair share of what
    a full title match on every word would (40%, or 60% for a one-word query), and most of the
    words must appear somewhere (all of two, two of three), so a reading that only mentions
    "labor" or "change" doesn't count as a match for "AI and labor" or "climate change".
    """
    title, topics = set(tokens(item.title)), set(tokens(" ".join(item.topics or [])))
    body = set(tokens(f"{item.summary} {item.parent_title}"))
    raw = 0.0
    for t, w in weights.items():
        raw += w * (3.0 if t in title else 2.0 if t in topics else 1.0 if t in body else 0.0)
    n = len(weights)
    hit = sum(1 for t in weights if t in title or t in topics or t in body)
    if raw <= 0 or raw < (0.4 if n > 1 else 0.6) * 3.0 * sum(weights.values()) or hit < min(n, max(2, math.ceil(0.6 * n))):
        return 0.0
    q = LEVEL_FACTOR.get(item.level, 1.0) * AUDIENCE_FACTOR.get(item.audience, 1.0) * (1.15 if item.redistributable else 1.0)
    if arc_level in ("100", "200") and item.course_level == "grad":
        q *= 0.5
    return raw * q


def theme_candidates(pack: Pack, weights: dict[str, float], focus: str, limit: int = 600) -> list[Reading]:
    cond = Q()
    for t in weights:
        cond |= Q(search_text__contains=prefix(t))
    if not cond.children:
        return []
    qs = Reading.objects.filter(pack=pack, withdrawn=False).filter(cond).exclude(level__in=("book", "collection", "journal"))
    if focus:
        qs = qs.filter(skins_text__contains=f"|{focus}|")
    return list(qs.defer("notes")[:limit])


# ---------------------------------------------------------------------------- arcs

def unit_words(u: dict) -> set[str]:
    return set(tokens(" ".join([u["title"], u["summary"], " ".join(u["goals"]), " ".join(u["topics"])]), drop_domain=True))


def arc_words(arc: Arc, units: list[dict], focus_label: str) -> tuple[set[str], list[set[str]]]:
    return set(tokens(f"{arc.title} {arc.tagline} {focus_label}")), [unit_words(u) for u in units]


def arc_score(units: list[dict], words: tuple[set[str], list[set[str]]], weights: dict[str, float], idf: dict[str, float],
              spec_codes: set[str], spec_topics: set[str], reading_scores: dict[str, float],
              topic_w: dict[str, float] | None = None) -> float:
    """Query words count for what they distinguish: a word in every arc's text (analyze, project) is worth
    little, one in a few arcs' (usability, labor) is worth a lot."""
    head, per_unit = words
    score = sum(w * idf.get(t, 0.0) for t, w in weights.items() if t in head)
    for u, uw in zip(units, per_unit):
        score += 1.5 * sum(w * idf.get(t, 0.0) for t, w in weights.items() if t in uw)
        score += 1.5 * len(spec_codes & set(u["outcomes"]))
        # A shared specific topic ("usability testing") says more than a generic one ("argument").
        score += 1.5 * sum((topic_w or {}).get(t, 1.0) for t in spec_topics & {t.lower() for t in u["topics"]})
        for role, factor in (("core", 1.0), ("optional", 0.5)):
            score += factor * sum(reading_scores.get(rid, 0.0) for rid in u["readings"].get(role, []))
    return score


# ---------------------------------------------------------------------------- build

def state_unit(u: dict) -> dict:
    return {
        "key": u["key"], "title": u["title"], "start_week": u["start_week"], "end_week": u["end_week"],
        "weeks": u["weeks"], "goals": u.get("goals", []), "outcomes": u.get("outcomes", []),
        "readings": [{"id": r["id"], "selected": r["selected"], "role": r["role"]} for r in u["readings"]],
        "assignments": [{"id": a["id"], "role": a["role"], "selected": a["selected"]} for a in u["assignments"]],
        "summary": u.get("summary", ""), "notes": u.get("notes", ""), "topics": u.get("topics", []),
        "merged_from": u.get("merged_from", [u["key"]]),
    }


def unit_fit(unit: dict, item: Reading, weights: dict[str, float]) -> float:
    shared = len({t.lower() for t in unit.get("topics", [])} & {t.lower() for t in item.topics or []})
    words = set(tokens(" ".join([unit["title"], unit.get("summary", "")])))
    return 0.35 * min(shared, 3) + 0.25 * sum(w for t, w in weights.items() if t in words)


def add_reading(unit: dict, rid: str) -> None:
    unit["readings"].append({"id": rid, "selected": True, "role": "added", "added": True})


class NothingToGoOn(ValueError):
    pass


def build(pack: Pack, req: Request) -> dict:
    from builder.api import _plan_payload  # imported here: builder.api imports this module

    req.theme, req.outcomes_text = req.theme.strip()[:MAX_THEME_CHARS], req.outcomes_text.strip()[:MAX_OUTCOMES_CHARS]
    weights = query_weights(req)
    set_specs: list[oc.OutcomeSpec] = []
    if req.outcome_set_ids:
        _, set_specs, _ = oc.specs_for_sets(pack, req.outcome_set_ids)
    for s in set_specs:  # a stored course's own wording counts like typed outcomes
        for t in tokens(s.text, drop_domain=True):
            weights[t] = max(weights.get(t, 0.0), OUTCOME_WEIGHT)
    if not weights and not req.focus:
        raise NothingToGoOn("Add a theme, some course outcomes, or pick a focus.")

    vocab = topic_vocabulary(pack)
    custom = []
    for i, line in enumerate(clean_outcome_lines(req.outcomes_text), start=1):
        custom.append(oc.OutcomeSpec(f"custom:{i}", "custom", f"Outcome {i}", line, [], matching_topics(line, vocab)))
    specs = set_specs + custom
    spec_codes = {c for s in specs for c in s.codes}
    spec_topics = {t for s in specs for t in s.topics}

    # Which arc. Reading scores for the whole query make arcs whose readings fit the theme win.
    arcs = list(Arc.objects.filter(pack=pack, focus=req.focus) if req.focus and not req.arc_id else Arc.objects.filter(pack=pack))
    if not arcs:
        raise NothingToGoOn("There are no sample courses for that focus yet.")
    # Readings matching the theme (or, with no theme, none) make arcs that already use them win.
    theme_w = {t: w for t, w in weights.items() if w >= THEME_WEIGHT}
    cands = theme_candidates(pack, theme_w, req.focus) if theme_w else []
    scores = {r.id: item_score(r, theme_w, "") for r in cands}
    labels = {f["code"]: f.get("label", "") for f in (pack.config or {}).get("focuses", [])}
    arc_units = {a.id: arc_units_for_planning(a) for a in arcs}
    arc_text = {a.id: arc_words(a, arc_units[a.id], labels.get(a.focus, "")) for a in arcs}
    df = Counter(t for head, per in arc_text.values() for t in head | set().union(*per))
    idf = {t: math.log(1 + len(arcs) / n) for t, n in df.items()}
    topic_w = oc.topic_weights(pack)
    ranked = [(arc_score(arc_units[a.id], arc_text[a.id], weights, idf, spec_codes, spec_topics, scores, topic_w), a.id, a)
              for a in arcs]
    ranked.sort(key=lambda x: (-x[0], x[1]))
    if req.arc_id:  # the instructor picked one of the alternatives
        picked = next((r for r in ranked if r[1] == req.arc_id), None)
        if picked is None:
            raise NothingToGoOn("That sample course doesn't exist.")
        ranked.remove(picked)
        ranked.insert(0, picked)
    best = ranked[0][2]

    payload = _plan_payload(best, req.term)
    units = [state_unit(u) for u in payload["units"]]
    arc_level = best.course_level
    in_arc = {r["id"] for u in units for r in u["readings"]}

    # Theme readings go to the unit they fit best.
    theme_added: list[dict] = []
    if req.theme and theme_w:
        pool =sorted(((item_score(r, theme_w, arc_level), r) for r in cands if r.id not in in_arc),
                      key=lambda x: (-x[0], x[1].title))
        per_unit: Counter = Counter()
        for sc, r in pool:  # link-only readings can join too; they show as links
            if sc < THEME_FLOOR or sum(per_unit.values()) >= THEME_PER_UNIT * len(units):
                break
            open_units = [u for u in units if per_unit[u["key"]] < THEME_PER_UNIT]
            if not open_units:
                break
            unit = max(open_units, key=lambda u: (unit_fit(u, r, theme_w), -per_unit[u["key"]]))
            add_reading(unit, r.id)
            per_unit[unit["key"]] += 1
            theme_added.append({"id": r.id, "title": r.title, "unit": unit["title"]})

    state = {
        "title": req.course_name.strip()[:200] or best.title, "instructor": "", "institution": "", "term_label": "",
        "focus": best.focus, "arc_id": best.id, "term": payload["term"], "units": units,
        "plan_note": {"weeks": req.term.weeks, "dropped": payload["dropped"], "merged": payload["merged"]},
        "outcome_set_ids": list(req.outcome_set_ids),
    }

    # Outcomes: fill what the arc and theme readings leave uncovered.
    report = []
    if specs:
        cov = oc.coverage(pack, state, req.outcome_set_ids, extra_specs=custom)
        gaps = {(g["set_id"], g["outcome_id"]): g for g in cov["gaps"]}
        by_key = {u["key"]: u for u in units}
        have = {r["id"] for u in units for r in u["readings"]} | {a["id"] for u in units for a in u["assignments"]}
        for row in cov["outcomes"]:
            added = []
            gap = gaps.get((row["set_id"], row["id"]))
            if gap and gap["unit_key"] in by_key:
                unit = by_key[gap["unit_key"]]
                for s in gap["suggestions"]:
                    if len(added) >= OUTCOME_PICKS_PER_GAP:
                        break
                    if s["id"] in have:
                        continue
                    if s["item_type"] == "reading":
                        add_reading(unit, s["id"])
                    else:
                        unit["assignments"].append({"id": s["id"], "role": "activity", "selected": True, "added": True})
                    have.add(s["id"])
                    added.append({"id": s["id"], "title": s["title"], "type": s["item_type"], "unit": unit["title"]})
            spec = next((s for s in specs if s.id == row["id"] and s.set_id == row["set_id"]), None)
            report.append({
                "id": row["id"], "set_id": row["set_id"], "label": row["label"], "text": row["text"],
                "status": row["status"], "had": row["total"], "added": added,
                "matched_topics": spec.topics[:6] if spec and spec.set_id == "custom" else [],
                "unmatched": bool(spec and spec.set_id == "custom" and not spec.topics and not added and row["total"] == 0),
            })

    why = [f"You chose {best.title}." if req.arc_id else
           f"Best match among {len(arcs)} sample courses" + (f" in {best.focus}" if req.focus else "") + "."]
    if req.theme:
        why.append(f"{len(theme_added)} readings on your theme were added to the units they fit." if theme_added
                   else "No readings in the catalog matched the theme closely enough to add.")
    return {
        "state": state,
        "arc": {"id": best.id, "title": best.title, "focus": best.focus, "tagline": best.tagline},
        "alternatives": [{"id": a.id, "title": a.title, "focus": a.focus} for _, _, a in ranked[1:4]],
        "why": why,
        "theme_added": theme_added,
        "outcomes": report,
    }
