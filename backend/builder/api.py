from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from builder import exports, reader_accessible
from builder.licensing import license_report
from builder.models import Build
from builder.planning import Term, plan_course
from catalog.api import _pack
from catalog.models import Arc, Assignment, Reading
from catalog.serializers import arc_units_for_planning, assignment_brief, reading_brief

router = Router(tags=["builder"])


class PlanIn(Schema):
    arc_id: str
    weeks: int = 16
    fixed_start: int = 1
    fixed_end: int = 1
    pages_per_week: int = 40


class BuildIn(Schema):
    state: dict
    forked_from: str | None = None


class ExportIn(Schema):
    state: dict


def _plan_payload(arc: Arc, term: Term) -> dict:
    units = arc_units_for_planning(arc)
    ids_r = {i for u in units for role in u["readings"].values() for i in role}
    ids_a = {i for u in units for role in u["assignments"].values() for i in role}
    readings = {r.id: r for r in Reading.objects.filter(id__in=ids_r)}
    page_info = {rid: {"pages": r.pages, "level": r.level} for rid, r in readings.items()}
    plan = plan_course(units, term, page_info)
    summaries = {u["key"]: {"summary": u["summary"], "notes": u["notes"], "topics": u["topics"]} for u in units}
    for u in plan["units"]:
        first = u["merged_from"][0]
        u.update({k: v for k, v in summaries.get(first, {}).items()})
    plan["arc"] = {"id": arc.id, "title": arc.title, "focus": arc.focus, "rationale": arc.rationale}
    plan["readings"] = {rid: reading_brief(r) for rid, r in readings.items()}
    plan["assignments"] = {a.id: assignment_brief(a) for a in Assignment.objects.filter(id__in=ids_a)}
    return plan


@router.post("/plan")
def make_plan(request, body: PlanIn):
    arc = get_object_or_404(Arc, id=body.arc_id)
    if not 1 <= body.weeks <= 30:
        raise HttpError(400, "weeks must be between 1 and 30")
    term = Term(body.weeks, max(body.fixed_start, 0), max(body.fixed_end, 0), max(body.pages_per_week, 5))
    return _plan_payload(arc, term)


@router.post("/license-report")
def report(request, body: ExportIn):
    ctx = exports.resolve(body.state)
    return ctx["report"]


@router.post("/builds")
def create_build(request, body: BuildIn):
    state = body.state
    arc = Arc.objects.filter(id=state.get("arc_id")).first()
    build = Build.objects.create(
        pack=_pack(None), title=(state.get("title") or "Untitled course")[:300], focus=state.get("focus", ""),
        arc=arc, state=state, forked_from=Build.objects.filter(id=body.forked_from).first() if body.forked_from else None)
    return {"id": str(build.id), "edit_token": build.edit_token}


@router.get("/builds/{build_id}")
def get_build(request, build_id: str):
    b = get_object_or_404(Build, id=build_id)
    return {"id": str(b.id), "title": b.title, "state": b.state, "updated": b.updated.isoformat(),
            "forked_from": str(b.forked_from_id) if b.forked_from_id else None}


@router.put("/builds/{build_id}")
def update_build(request, build_id: str, body: BuildIn):
    b = get_object_or_404(Build, id=build_id)
    if request.headers.get("X-Edit-Token") != b.edit_token:
        raise HttpError(403, "This link is view-only. Fork the course to edit your own copy.")
    b.state = body.state
    b.title = (body.state.get("title") or b.title)[:300]
    b.focus = body.state.get("focus", b.focus)
    b.save()
    return {"id": str(b.id), "updated": b.updated.isoformat()}


def _filename(state: dict, ext: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in (state.get("title") or "course-reader").lower()).strip("-")
    return f"{base or 'course-reader'}.{ext}"


@router.post("/export/html")
def export_html(request, body: ExportIn):
    html = exports.reader_html(exports.resolve(body.state))
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{_filename(body.state, "html")}"'
    return resp


@router.post("/export/pdf")
def export_pdf(request, body: ExportIn, variant: str = "accessible"):
    """`accessible`: one tagged document, PDF-only readings linked. `print`: publisher PDFs merged in."""
    builders = {"accessible": reader_accessible.build, "print": exports.reader_pdf}
    if variant not in builders:
        raise HttpError(400, "variant must be accessible or print")
    try:
        pdf = builders[variant](exports.resolve(body.state))
    except RuntimeError as e:
        raise HttpError(501, str(e))
    resp = HttpResponse(pdf, content_type="application/pdf")
    name = _filename(body.state, "pdf")
    if variant == "print":
        name = name.removesuffix(".pdf") + "-print.pdf"
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@router.post("/export/imscc")
def export_imscc(request, body: ExportIn, flavor: str = "canvas"):
    data = exports.build_imscc(exports.resolve(body.state), "standard" if flavor == "standard" else "canvas")
    resp = HttpResponse(data, content_type="application/zip")
    suffix = "" if flavor != "standard" else "-standard"
    resp["Content-Disposition"] = f'attachment; filename="{_filename(body.state, "imscc").replace(".imscc", suffix + ".imscc")}"'
    return resp
