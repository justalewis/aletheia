from collections import Counter

from django.db.models import Case, F, IntegerField, Q, When
from django.shortcuts import get_object_or_404
from ninja import Router

from catalog.models import Arc, Assignment, OutcomeSet, Pack, Reading
from catalog.serializers import (
    arc_brief, arc_full, assignment_brief, assignment_full, reading_brief, reading_full,
)

router = Router(tags=["catalog"])


def _pack(pack_id: str | None) -> Pack:
    if pack_id:
        return get_object_or_404(Pack, id=pack_id)
    pack = Pack.objects.first()
    if pack is None:
        raise Pack.DoesNotExist("No corpus pack loaded; run manage.py load_pack")
    return pack


def _filter(qs, focus, topic, tier, q):
    if focus:
        qs = qs.filter(skins_text__contains=f"|{focus}|")
    for t in [t for t in (topic or "").split(",") if t]:
        qs = qs.filter(topics_text__contains=f"|{t.lower()}|")
    if tier:
        tiers = tier.split(",")
        if "link-only" in tiers:
            qs = qs.filter(Q(reuse_tier__in=tiers) | Q(license_verified=False))
        else:
            qs = qs.filter(reuse_tier__in=tiers, license_verified=True)
    for word in (q or "").lower().split():
        qs = qs.filter(search_text__contains=word)
    return qs


PARTS = ("chapter", "article", "primary-text")
STUDENTISH = ("student", "both")
INTRO = ("100", "200", "mixed")

# Browse order: readings from open textbooks first, scholarly monographs last.
BROWSE_TIER = Case(
    # Chapters written for students in lower-division courses: the open textbooks.
    When(level="chapter", audience="student", course_level__in=INTRO, then=0),
    # Essays and primary texts for the same students (readers such as 88 Open Essays).
    When(level__in=PARTS, audience="student", course_level__in=INTRO, then=1),
    # Other short readings and web pages for lower-division students or general readers.
    When(level__in=PARTS + ("website",), audience__in=STUDENTISH, course_level__in=INTRO, then=2),
    # Whole open textbooks and collections.
    When(level__in=("book", "collection"), audience__in=STUDENTISH, course_level__in=INTRO, then=3),
    # Upper-division readings and books.
    When(audience__in=STUDENTISH, course_level="300-400", then=4),
    # Short readings for instructors or graduate courses, and whole journals.
    When(level__in=PARTS + ("website", "journal"), then=5),
    # Monographs and edited collections for instructors and graduate courses.
    default=6,
    output_field=IntegerField(),
)
# Chapters sort under their book's title, so a book's chapters stay together; standalone pieces under their own.
SHELF = Case(When(parent_title="", then=F("title")), default=F("parent_title"))


@router.get("/pack")
def pack_info(request, pack: str | None = None):
    p = _pack(pack)
    return {
        "id": p.id, "name": p.name, "version": p.version, "config": p.config,
        "counts": {
            "readings": Reading.objects.filter(pack=p, withdrawn=False).count(),
            "assignments": Assignment.objects.filter(pack=p, withdrawn=False).count(),
            "arcs": p.arcs.count(),
            "outcome_sets": p.outcome_sets.count(),
        },
    }


@router.get("/readings")
def list_readings(request, focus: str = "", topic: str = "", tier: str = "", level: str = "", q: str = "",
                  parent: str = "", limit: int = 50, offset: int = 0, pack: str | None = None):
    qs = Reading.objects.filter(pack=_pack(pack), withdrawn=False)
    qs = _filter(qs, focus, topic, tier, q)
    if level:
        qs = qs.filter(level__in=level.split(","))
    if parent:
        qs = qs.filter(parent_id=parent)
    total = qs.count()
    rows = qs.annotate(tier_rank=BROWSE_TIER, shelf=SHELF).order_by("tier_rank", "shelf", "title")
    rows = rows[offset: offset + min(limit, 200)]
    return {"total": total, "items": [reading_brief(r) for r in rows]}


@router.get("/readings/{reading_id}")
def get_reading(request, reading_id: str):
    return reading_full(get_object_or_404(Reading, id=reading_id))


@router.get("/topics")
def topics(request, focus: str = "", kind: str = "readings", pack: str | None = None):
    model = Assignment if kind == "assignments" else Reading
    qs = model.objects.filter(pack=_pack(pack), withdrawn=False)
    if focus:
        qs = qs.filter(skins_text__contains=f"|{focus}|")
    counts = Counter(t.lower() for ts in qs.values_list("topics", flat=True) for t in ts)
    return [{"topic": t, "count": n} for t, n in counts.most_common(150)]


@router.get("/assignments")
def list_assignments(request, focus: str = "", topic: str = "", tier: str = "", kind: str = "", q: str = "",
                     limit: int = 50, offset: int = 0, pack: str | None = None):
    qs = Assignment.objects.filter(pack=_pack(pack), withdrawn=False)
    qs = _filter(qs, focus, topic, tier, q)
    if kind:
        qs = qs.filter(kind__in=kind.split(","))
    total = qs.count()
    rows = qs.order_by("source_repository", "title")[offset: offset + min(limit, 200)]
    return {"total": total, "items": [assignment_brief(a) for a in rows]}


@router.get("/assignments/{assignment_id}")
def get_assignment(request, assignment_id: str):
    return assignment_full(get_object_or_404(Assignment, id=assignment_id))


@router.get("/arcs")
def list_arcs(request, focus: str = "", pack: str | None = None):
    qs = Arc.objects.filter(pack=_pack(pack)).prefetch_related("units")
    if focus:
        qs = qs.filter(focus=focus)
    return [arc_brief(a) for a in qs.order_by("focus", "title")]


@router.get("/arcs/{arc_id}")
def get_arc(request, arc_id: str):
    return arc_full(get_object_or_404(Arc, id=arc_id))


@router.get("/outcome-sets")
def list_outcome_sets(request, pack: str | None = None):
    return [
        {"id": s.id, "kind": s.kind, "institution": s.institution, "course": s.course, "title": s.title,
         "count": s.outcomes.count()}
        for s in OutcomeSet.objects.filter(pack=_pack(pack)).order_by("kind", "institution", "course")
    ]


@router.get("/outcome-sets/{set_id}")
def get_outcome_set(request, set_id: str):
    s = get_object_or_404(OutcomeSet, id=set_id)
    return {
        "id": s.id, "kind": s.kind, "institution": s.institution, "course": s.course, "title": s.title,
        "data": s.data,
        "outcomes": [{"id": o.id, "text": o.text, "frameworks": o.frameworks, "topics": o.topics}
                     for o in s.outcomes.all()],
    }
