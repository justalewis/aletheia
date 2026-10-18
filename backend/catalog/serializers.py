"""Plain-dict views of catalog rows for the API and the exporters."""
from catalog.models import Arc, Assignment, Reading
from builder.planning import estimate_pages


def reading_brief(r: Reading) -> dict:
    return {
        "id": r.id,
        "level": r.level,
        "title": r.title,
        "authors": r.authors,
        "parent_title": r.parent_title,
        "year": r.year,
        "license": r.license,
        "reuse_tier": r.effective_tier,
        "license_verified": r.license_verified,
        "skins": r.skins,
        "topics": r.topics,
        "audience": r.audience,
        "url": r.best_url,
        "pdf_url": r.pdf_url,
        "pages": r.pages,
        "est_pages": estimate_pages({"pages": r.pages, "level": r.level}),
        "summary": r.summary,
    }


def reading_full(r: Reading) -> dict:
    d = reading_brief(r)
    d.update({
        "parent_id": r.parent_id,
        "publisher_platform": r.publisher_platform,
        "landing_url": r.landing_url,
        "html_url": r.html_url,
        "epub_url": r.epub_url,
        "license_url": r.license_url,
        "license_family": r.license_family,
        "declared_tier": r.reuse_tier,
        "course_level": r.course_level,
        "notes": r.notes,
        "children": [reading_brief(c) for c in r.children.order_by("id")[:300]],
    })
    return d


def assignment_brief(a: Assignment) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "authors": a.authors,
        "source_repository": a.source_repository,
        "year": a.year,
        "kind": a.kind,
        "assignment_type": a.assignment_type,
        "license": a.license,
        "reuse_tier": a.effective_tier,
        "license_verified": a.license_verified,
        "skins": a.skins,
        "topics": a.topics,
        "url": a.best_url,
        "estimated_weeks": a.estimated_weeks,
        "summary": a.summary,
    }


def assignment_full(a: Assignment) -> dict:
    d = assignment_brief(a)
    d.update({"file_url": a.file_url, "license_url": a.license_url, "notes": a.notes,
              "pairs_with_readings": a.pairs_with_readings, "course_level": a.course_level})
    return d


def arc_brief(arc: Arc) -> dict:
    units = list(arc.units.all())
    return {
        "id": arc.id,
        "focus": arc.focus,
        "title": arc.title,
        "tagline": arc.tagline,
        "course_level": arc.course_level,
        "frameworks": arc.frameworks,
        "fits_courses": arc.fits_courses,
        "unit_count": len(units),
        "ideal_weeks": sum(u.ideal_weeks for u in units),
    }


def arc_units_for_planning(arc: Arc) -> list[dict]:
    """Units in the shape builder.planning expects."""
    out = []
    for u in arc.units.prefetch_related("unitreading_set", "unitassignment_set"):
        readings = {"core": [], "optional": []}
        for ur in u.unitreading_set.all():
            readings.setdefault(ur.role, []).append(ur.reading_id)
        assignments = {"major": [], "short_term": [], "activity": []}
        for ua in u.unitassignment_set.all():
            assignments.setdefault(ua.role, []).append(ua.assignment_id)
        out.append({
            "key": u.key, "title": u.title, "summary": u.summary, "goals": u.goals, "outcomes": u.outcomes,
            "topics": u.topics, "min_weeks": u.min_weeks, "ideal_weeks": u.ideal_weeks, "priority": u.priority,
            "merge_with": u.merge_with, "notes": u.notes, "readings": readings, "assignments": assignments,
        })
    return out


def arc_full(arc: Arc) -> dict:
    d = arc_brief(arc)
    units = arc_units_for_planning(arc)
    ids_r = {i for u in units for role in u["readings"].values() for i in role}
    ids_a = {i for u in units for role in u["assignments"].values() for i in role}
    d.update({
        "rationale": arc.rationale,
        "units": units,
        "readings": {r.id: reading_brief(r) for r in Reading.objects.filter(id__in=ids_r)},
        "assignments": {a.id: assignment_brief(a) for a in Assignment.objects.filter(id__in=ids_a)},
    })
    return d
