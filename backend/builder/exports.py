"""Turn a build's state into a course reader (HTML/PDF) or a Common Cartridge package.

Build state (what the frontend holds and posts):
    {"title", "instructor", "institution", "term_label", "focus", "arc_id",
     "term": {"weeks", "fixed_start", "fixed_end", "pages_per_week"},
     "units": [{"key", "title", "start_week", "end_week", "weeks", "goals", "outcomes",
                "readings": [{"id", "selected"}], "assignments": [{"id", "role", "selected"}]}]}

Only selected items are exported. The reader carries the full text of each redistributable
reading the content cache holds (see content/embed.py for the rules), under its attribution;
everything else is linked.
"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

from django.template.loader import render_to_string

from builder.licensing import attribution, license_report
from builder.reader_pdf import build as build_reader_pdf
from catalog.models import Assignment, Reading
from content.embed import attach


@dataclass
class ResolvedUnit:
    key: str
    title: str
    start_week: int | None
    end_week: int | None
    goals: list
    outcomes: list
    readings: list = field(default_factory=list)
    assignments: list = field(default_factory=list)

    @property
    def weeks_label(self) -> str:
        if not self.start_week:
            return ""
        if self.start_week == self.end_week:
            return f"Week {self.start_week}"
        return f"Weeks {self.start_week}–{self.end_week}"


def resolve(state: dict) -> dict:
    units = state.get("units", [])
    r_ids = [r["id"] for u in units for r in u.get("readings", []) if r.get("selected")]
    a_ids = [a["id"] for u in units for a in u.get("assignments", []) if a.get("selected")]
    readings = {r.id: r for r in Reading.objects.filter(id__in=r_ids)}
    assignments = {a.id: a for a in Assignment.objects.filter(id__in=a_ids)}
    resolved = []
    for u in units:
        ru = ResolvedUnit(u.get("key", ""), u.get("title", ""), u.get("start_week"), u.get("end_week"),
                          u.get("goals", []), u.get("outcomes", []))
        for r in u.get("readings", []):
            obj = readings.get(r["id"])
            if r.get("selected") and obj:
                obj.credit = attribution(obj)
                ru.readings.append(obj)
        for a in u.get("assignments", []):
            obj = assignments.get(a["id"])
            if a.get("selected") and obj:
                obj.credit = attribution(obj, "assignment")
                obj.role = a.get("role", "")
                ru.assignments.append(obj)
        resolved.append(ru)
    all_r = [r for u in resolved for r in u.readings]
    all_a = [a for u in resolved for a in u.assignments]
    return {
        "title": state.get("title") or "Course Reader",
        "instructor": state.get("instructor", ""),
        "institution": state.get("institution", ""),
        "term_label": state.get("term_label", ""),
        "term": state.get("term", {}),
        "units": resolved,
        "readings": all_r,
        "assignments": all_a,
        "report": license_report(all_r, all_a),
        "generated": date.today().isoformat(),
    }


# ---------------------------------------------------------------- reader

def reader_html(ctx: dict, store=None) -> str:
    attach(ctx["readings"], "html", store)
    return render_to_string("builder/reader.html", ctx)


def reader_pdf(ctx: dict, renderer=None, store=None) -> bytes:
    """WeasyPrint for Aletheia's pages plus the publishers' PDFs, merged with pypdf (see reader_pdf.py).

    WeasyPrint is installed in the Docker image, not required locally; without it this raises
    RuntimeError, which the API turns into a 501.
    """
    return build_reader_pdf(ctx, renderer, store)


# ---------------------------------------------------------------- common cartridge

CC_NS = (
    'xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1" '
    'xmlns:lomimscc="http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1 '
    'http://www.imsglobal.org/profile/cc/ccv1p1/ccv1p1_imscp_v1p2_v1p0.xsd '
    'http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest '
    'http://www.imsglobal.org/profile/cc/ccv1p1/LOM/ccv1p1_lommanifest_v1p0.xsd"'
)
CANVAS_NS = ('xmlns="http://canvas.instructure.com/xsd/cccv1p0" '
             'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             'xsi:schemaLocation="http://canvas.instructure.com/xsd/cccv1p0 '
             'https://canvas.instructure.com/xsd/cccv1p0.xsd"')


def _ident(prefix: str, *parts: str) -> str:
    """Stable identifiers so re-importing the same build updates rather than duplicates."""
    return prefix + uuid.uuid5(uuid.NAMESPACE_URL, "aletheia:" + ":".join(parts)).hex


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "page"


def _page(title: str, ident: str, body: str) -> str:
    return render_to_string("builder/cc_page.html", {"title": title, "identifier": ident, "body": body})


def build_imscc(ctx: dict, flavor: str = "canvas") -> bytes:
    """Common Cartridge 1.1. `canvas` adds Canvas's course_settings layer (real Pages, modules,
    assignment shells); `standard` is plain CC 1.1 for Moodle, D2L, and others."""
    canvas = flavor == "canvas"
    course_id = _ident("c", ctx["title"])
    files: dict[str, str] = {}
    resources: list[str] = []
    modules: list[dict] = []
    used_paths: set[str] = set()

    def add_page(title: str, body_html: str, key: str) -> str:
        ident = _ident("g", course_id, key)
        path = f"wiki_content/{_slug(title)}.html"
        n = 2
        while path in used_paths:
            path = f"wiki_content/{_slug(title)}-{n}.html"
            n += 1
        used_paths.add(path)
        files[path] = _page(title, ident, body_html)
        resources.append(f'<resource identifier="{ident}" type="webcontent" href="{path}"><file href="{path}"/></resource>')
        return ident

    def add_assignment(a: Assignment, key: str, position: int) -> str:
        ident = _ident("i", course_id, key)
        body = render_to_string("builder/cc_assignment.html", {"a": a})
        if not canvas:
            return add_page(f"Assignment: {a.title}", body, key)
        slug = _slug(a.title)
        files[f"{ident}/{slug}.html"] = (
            '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
            f"<title>Assignment: {escape(a.title)}</title></head><body>{body}</body></html>")
        files[f"{ident}/assignment_settings.xml"] = render_to_string(
            "builder/cc_assignment_settings.xml", {"ident": ident, "title": a.title, "position": position, "ns": CANVAS_NS})
        resources.append(
            f'<resource identifier="{ident}" type="associatedcontent/imscc_xmlv1p1/learning-application-resource" '
            f'href="{ident}/{slug}.html"><file href="{ident}/{slug}.html"/>'
            f'<file href="{ident}/assignment_settings.xml"/></resource>')
        return ident

    # Start Here: schedule and the license page.
    start_items = [
        ("WikiPage", "Course schedule", add_page("Course schedule", render_to_string("builder/cc_schedule.html", ctx), "schedule")),
        ("WikiPage", "About this reader: licenses and credits",
         add_page("About this reader: licenses and credits", render_to_string("builder/cc_licenses.html", ctx), "licenses")),
    ]
    modules.append({"title": "Start Here", "items": start_items})

    position = 0
    for u in ctx["units"]:
        items = []
        if u.readings:
            items.append(("ContextModuleSubHeader", "Readings", None))
            for r in u.readings:
                body = render_to_string("builder/cc_reading.html", {"r": r})
                items.append(("WikiPage", r.title, add_page(r.title, body, f"{u.key}:{r.id}")))
        if u.assignments:
            items.append(("ContextModuleSubHeader", "Assignments and activities", None))
            for a in u.assignments:
                position += 1
                kind = "Assignment" if canvas else "WikiPage"
                items.append((kind, a.title, add_assignment(a, f"{u.key}:{a.id}", position)))
        title = f"{u.weeks_label}: {u.title}" if u.weeks_label else u.title
        modules.append({"title": title, "items": items})

    # Organizations (standard layer: every LMS reads this) and Canvas module_meta share item ids.
    org_items, meta_modules = [], []
    for mi, m in enumerate(modules):
        mid = _ident("m", course_id, str(mi), m["title"])
        children, meta_items = [], []
        for ii, (kind, t, ref) in enumerate(m["items"]):
            iid = _ident("it", course_id, str(mi), str(ii))
            if ref:
                children.append(f'<item identifier="{iid}" identifierref="{ref}"><title>{escape(t)}</title></item>')
            meta_items.append(
                f'<item identifier="{iid}"><content_type>{kind}</content_type><workflow_state>active</workflow_state>'
                f"<title>{escape(t)}</title>" + (f"<identifierref>{ref}</identifierref>" if ref else "")
                + f"<position>{ii + 1}</position><new_tab/><indent>{0 if kind == 'ContextModuleSubHeader' else 1}</indent></item>")
        org_items.append(f'<item identifier="{mid}"><title>{escape(m["title"])}</title>{"".join(children)}</item>')
        meta_modules.append(
            f'<module identifier="{mid}"><title>{escape(m["title"])}</title><workflow_state>unpublished</workflow_state>'
            f"<position>{mi + 1}</position><require_sequential_progress>false</require_sequential_progress>"
            f"<locked>false</locked><items>{''.join(meta_items)}</items></module>")

    if canvas:
        settings_files = ["course_settings/course_settings.xml", "course_settings/module_meta.xml",
                          "course_settings/assignment_groups.xml"]
        files["course_settings/canvas_export.txt"] = "Q: What did the panda say when he was forced out of his natural habitat?\nA: This is un-BEAR-able\n"
        files["course_settings/course_settings.xml"] = render_to_string(
            "builder/cc_course_settings.xml", {"ident": course_id, "title": ctx["title"], "ns": CANVAS_NS})
        files["course_settings/assignment_groups.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n<assignmentGroups {CANVAS_NS}>'
            f'<assignmentGroup identifier="{_ident("ag", course_id)}"><title>Assignments</title>'
            "<position>1</position><group_weight>0.0</group_weight></assignmentGroup></assignmentGroups>")
        files["course_settings/module_meta.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n<modules {CANVAS_NS}>{"".join(meta_modules)}</modules>')
        settings_res = "".join(f'<file href="{f}"/>' for f in settings_files)
        resources.insert(0,
            f'<resource identifier="{_ident("cs", course_id)}" type="associatedcontent/imscc_xmlv1p1/learning-application-resource" '
            f'href="course_settings/canvas_export.txt"><file href="course_settings/canvas_export.txt"/>{settings_res}</resource>')

    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<manifest identifier="{course_id}" {CC_NS}>'
        "<metadata><schema>IMS Common Cartridge</schema><schemaversion>1.1.0</schemaversion>"
        f"<lomimscc:lom><lomimscc:general><lomimscc:title><lomimscc:string>{escape(ctx['title'])}</lomimscc:string>"
        "</lomimscc:title></lomimscc:general></lomimscc:lom></metadata>"
        '<organizations><organization identifier="org_1" structure="rooted-hierarchy">'
        f'<item identifier="LearningModules">{"".join(org_items)}</item></organization></organizations>'
        f"<resources>{''.join(resources)}</resources></manifest>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("imsmanifest.xml", manifest)
        for path, content in files.items():
            z.writestr(path, content)
    return buf.getvalue()
