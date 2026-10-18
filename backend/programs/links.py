"""Linking a saved build to a program: the `program_id` and `section` fields of the builds API."""
from __future__ import annotations

import re
import uuid

from ninja.errors import HttpError

SECTION_TEXT = ("course_code", "section", "term", "instructor")
MAX_ENROLLMENT = 10000

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def parse_uuid(value) -> uuid.UUID | None:
    """A UUID from a bare id or anything that contains one (a pasted link, say)."""
    m = UUID_RE.search(str(value or ""))
    return uuid.UUID(m.group(0)) if m else None


def clean_section(raw) -> dict:
    """Section info as the report reads it. Raises 400 on an enrollment that isn't a whole number."""
    if not raw or not isinstance(raw, dict):
        return {}
    out = {k: str(raw.get(k) or "").strip()[:120] for k in SECTION_TEXT}
    n = raw.get("enrollment")
    if n in (None, ""):
        n = 0
    try:
        if isinstance(n, bool) or float(n) != int(float(n)):
            raise ValueError
        n = int(float(n))
    except (TypeError, ValueError):
        raise HttpError(400, "Enrollment must be a whole number.")
    if not 0 <= n <= MAX_ENROLLMENT:
        raise HttpError(400, f"Enrollment must be between 0 and {MAX_ENROLLMENT}.")
    out["enrollment"] = n
    return out


def program_fields(body) -> dict:
    """Build model fields for whichever of `program_id` and `section` the request sent.

    An unknown or removed program unlinks the build rather than failing the save.
    """
    from programs.models import Program

    sent = getattr(body, "model_fields_set", set())
    out = {}
    if "program_id" in sent:
        pid = parse_uuid(body.program_id) if body.program_id else None
        out["program"] = Program.objects.filter(id=pid).first() if pid else None
    if "section" in sent:
        out["section"] = clean_section(body.section)
    if out.get("program", ...) is None:
        out["section"] = {}
    return out
