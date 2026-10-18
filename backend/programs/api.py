"""Program API: public pages for instructors, and admin endpoints behind the X-Program-Token header."""
from __future__ import annotations

import re
import secrets

from django.db.models import Max
from django.http import HttpResponse
from ninja import Router, Schema
from ninja.errors import HttpError

from builder.models import Build
from programs import ztc
from programs.links import parse_uuid
from programs.models import Program, ProgramArc, default_settings

router = Router(tags=["programs"])

FORBIDDEN = "This needs the program's admin link. Open the private admin link you saved when you set up the program."
# Keys that belong to one instructor's copy, not to a course the program shares.
PRIVATE_STATE_KEYS = ("program", "section")


class ProgramIn(Schema):
    name: str
    institution: str = ""
    description: str = ""


class ProgramUpdate(Schema):
    name: str | None = None
    institution: str | None = None
    description: str | None = None
    settings: dict | None = None


class ArcIn(Schema):
    state: dict | None = None
    title: str | None = None
    summary: str | None = None
    build_id: str | None = None
    published: bool = False


class ArcUpdate(Schema):
    title: str | None = None
    summary: str | None = None
    published: bool | None = None
    order: int | None = None
    state: dict | None = None


# ---------------------------------------------------------------- helpers

def _program(program_id: str) -> Program:
    pid = parse_uuid(program_id)
    p = Program.objects.filter(id=pid).first() if pid and str(pid) == program_id.lower() else None
    if p is None:
        raise HttpError(404, "We couldn't find that program.")
    return p


def _is_admin(request, program: Program) -> bool:
    return secrets.compare_digest(request.headers.get("X-Program-Token") or "", program.admin_token)


def _admin(request, program_id: str) -> Program:
    p = _program(program_id)
    if not _is_admin(request, p):
        raise HttpError(403, FORBIDDEN)
    return p


def _arc(program: Program, arc_id: str) -> ProgramArc:
    aid = parse_uuid(arc_id)
    a = program.arcs.filter(id=aid).first() if aid and str(aid) == arc_id.lower() else None
    if a is None:
        raise HttpError(404, "We couldn't find that course in this program.")
    return a


def _text(value: str | None, limit: int) -> str:
    return (value or "").strip()[:limit]


def _clean_state(state: dict) -> dict:
    if not isinstance(state, dict) or not isinstance(state.get("units"), list):
        raise HttpError(400, "That course has no units to share.")
    return {k: v for k, v in state.items() if k not in PRIVATE_STATE_KEYS}


def _clean_settings(current: dict, incoming: dict) -> dict:
    out = {**default_settings(), **(current or {})}
    if "textbook_cost_per_student" in incoming:
        try:
            cost = round(float(incoming["textbook_cost_per_student"]), 2)
        except (TypeError, ValueError):
            raise HttpError(400, "Textbook cost per student must be a number.")
        if not 0 <= cost <= 10000:
            raise HttpError(400, "Textbook cost per student must be between 0 and 10,000.")
        out["textbook_cost_per_student"] = cost
    if "currency" in incoming:
        cur = str(incoming["currency"] or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", cur):
            raise HttpError(400, "Currency must be a three-letter code, like USD.")
        out["currency"] = cur
    if "terms" in incoming:
        terms = incoming["terms"] or []
        if not isinstance(terms, list):
            raise HttpError(400, "Terms must be a list.")
        seen, clean = set(), []
        for t in terms:
            t = str(t or "").strip()[:60]
            if t and t.lower() not in seen:
                seen.add(t.lower())
                clean.append(t)
        out["terms"] = clean[:40]
    return out


def _counts(state: dict) -> dict:
    r, a = ztc.selected_ids(state)
    return {"units": len((state or {}).get("units") or []), "readings": len(r), "assignments": len(a)}


def _program_out(p: Program) -> dict:
    return {"id": str(p.id), "name": p.name, "institution": p.institution, "description": p.description,
            "settings": {**default_settings(), **(p.settings or {})}}


def _arc_brief(a: ProgramArc) -> dict:
    c = _counts(a.state)
    return {"id": str(a.id), "title": a.title, "summary": a.summary, "focus": a.focus, "unit_count": c["units"],
            "readings": c["readings"], "assignments": c["assignments"], "published": a.published, "order": a.order,
            "updated": a.updated.isoformat(),
            "source_build": str(a.source_build_id) if a.source_build_id else None}


# ---------------------------------------------------------------- public

@router.post("/programs")
def create_program(request, body: ProgramIn):
    name = _text(body.name, 300)
    if not name:
        raise HttpError(400, "Give the program a name.")
    p = Program.objects.create(name=name, institution=_text(body.institution, 300),
                               description=_text(body.description, 5000))
    return {"id": str(p.id), "admin_token": p.admin_token}


@router.get("/programs/{program_id}")
def get_program(request, program_id: str):
    p = _program(program_id)
    out = _program_out(p)
    out["arcs"] = [_arc_brief(a) for a in p.arcs.filter(published=True)]
    return out


@router.get("/programs/{program_id}/arcs/{arc_id}")
def get_program_arc(request, program_id: str, arc_id: str):
    """A course's full state, for "Use this course". Unpublished courses need the admin token."""
    p = _program(program_id)
    a = _arc(p, arc_id)
    if not a.published and not _is_admin(request, p):
        raise HttpError(404, "We couldn't find that course in this program.")
    return {**_arc_brief(a), "state": a.state, "program": {"id": str(p.id), "name": p.name}}


# ---------------------------------------------------------------- admin

@router.put("/programs/{program_id}")
def update_program(request, program_id: str, body: ProgramUpdate):
    p = _admin(request, program_id)
    if body.name is not None:
        name = _text(body.name, 300)
        if not name:
            raise HttpError(400, "Give the program a name.")
        p.name = name
    if body.institution is not None:
        p.institution = _text(body.institution, 300)
    if body.description is not None:
        p.description = _text(body.description, 5000)
    if body.settings is not None:
        p.settings = _clean_settings(p.settings, body.settings)
    p.save()
    return _program_out(p)


@router.get("/programs/{program_id}/arcs")
def list_program_arcs(request, program_id: str):
    p = _admin(request, program_id)
    return [_arc_brief(a) for a in p.arcs.all()]


@router.post("/programs/{program_id}/arcs")
def create_program_arc(request, program_id: str, body: ArcIn):
    p = _admin(request, program_id)
    source = None
    if body.build_id:
        bid = parse_uuid(body.build_id)
        source = Build.objects.filter(id=bid).first() if bid else None
        if source is None:
            raise HttpError(404, "We couldn't find a saved course at that link.")
        state = _clean_state(source.state)
    elif body.state is not None:
        state = _clean_state(body.state)
    else:
        raise HttpError(400, "Send a course state or a saved course link.")
    title = _text(body.title, 300) or _text(state.get("title"), 300) or (source.title if source else "") or "Untitled course"
    order = (p.arcs.aggregate(m=Max("order"))["m"] or 0) + 1
    a = ProgramArc.objects.create(program=p, title=title, summary=_text(body.summary, 2000),
                                  focus=_text(state.get("focus"), 20), state=state, source_build=source,
                                  published=body.published, order=order)
    return _arc_brief(a)


@router.put("/programs/{program_id}/arcs/{arc_id}")
def update_program_arc(request, program_id: str, arc_id: str, body: ArcUpdate):
    p = _admin(request, program_id)
    a = _arc(p, arc_id)
    if body.title is not None:
        a.title = _text(body.title, 300) or a.title
    if body.summary is not None:
        a.summary = _text(body.summary, 2000)
    if body.published is not None:
        a.published = body.published
    if body.order is not None:
        a.order = max(0, body.order)
    if body.state is not None:
        a.state = _clean_state(body.state)
        a.focus = _text(a.state.get("focus"), 20)
    a.save()
    return _arc_brief(a)


@router.delete("/programs/{program_id}/arcs/{arc_id}")
def delete_program_arc(request, program_id: str, arc_id: str):
    p = _admin(request, program_id)
    a = _arc(p, arc_id)
    a.delete()
    return {"deleted": arc_id}


@router.get("/programs/{program_id}/sections")
def list_sections(request, program_id: str):
    p = _admin(request, program_id)
    out = []
    for b in p.builds.order_by("-updated"):
        c = _counts(b.state)
        out.append({"id": str(b.id), "title": b.title, "updated": b.updated.isoformat(),
                    "section": b.section or {}, "instructor": (b.section or {}).get("instructor")
                    or (b.state or {}).get("instructor", ""), **c})
    return out


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower()))[:60] or "program"


@router.get("/programs/{program_id}/report")
def program_report(request, program_id: str, term: str = "", format: str = "json"):
    p = _admin(request, program_id)
    report = ztc.build_report(p, p.builds.all(), term.strip())
    if format != "csv":
        return report
    text = ztc.report_csv(report, lambda bid: request.build_absolute_uri(f"/build/{bid}"))
    resp = HttpResponse("﻿" + text, content_type="text/csv; charset=utf-8")
    name = f"{_slug(p.name)}-ztc-report{'-' + _slug(term) if term.strip() else ''}.csv"
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp
