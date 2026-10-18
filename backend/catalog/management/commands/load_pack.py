"""Load (or reload) a corpus pack from corpus/<pack>/ into the database.

    python manage.py load_pack rhetcomp

The pack files are the source of truth; this rebuilds the catalog tables from them inside
one transaction. Arc references to unknown catalog ids are reported and skipped.
"""
import json
import re
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog import outcomes
from catalog.models import (
    Arc, Assignment, Outcome, OutcomeSet, Pack, Reading, Source, Unit, UnitAssignment, UnitReading,
)

READING_FIELDS = {
    "title", "authors", "year", "license", "license_family", "license_url", "license_verified",
    "reuse_tier", "skins", "topics", "course_level", "summary", "notes", "withdrawn", "level",
    "parent_title", "publisher_platform", "landing_url", "html_url", "pdf_url", "epub_url", "audience",
}
ASSIGNMENT_FIELDS = {
    "title", "authors", "year", "license", "license_family", "license_url", "license_verified",
    "reuse_tier", "skins", "topics", "course_level", "summary", "notes", "withdrawn",
    "source_repository", "url", "file_url", "assignment_type", "kind", "estimated_weeks",
    "pairs_with_readings",
}


def jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_yaml(path: Path):
    """YAML with dates turned into ISO strings so the result can go into JSONFields."""
    return json.loads(json.dumps(yaml.safe_load(path.read_text(encoding="utf-8")), default=str))


WORDS_PER_PAGE = 300
PART_LEVELS = {"chapter", "article", "primary-text"}


def parse_pages(value, level: str) -> int | None:
    """Page count from the research data's mixed formats: "12", "88-92", "~400 words",
    "131 (start)". Book-sized totals copied onto chapters are discarded."""
    s = str(value or "").strip().lower().replace(",", "")
    if not s:
        return None
    if m := re.search(r"(\d+)\s*words", s):
        return max(1, round(int(m.group(1)) / WORDS_PER_PAGE))
    if m := re.search(r"(\d+)\s*[-–]\s*(\d+)", s):
        a, b = int(m.group(1)), int(m.group(2))
        return b - a + 1 if b >= a else None
    if "start" in s:
        return None
    m = re.search(r"\d+", s)
    if not m:
        return None
    n = int(m.group())
    if level in PART_LEVELS and n > 80:
        return None
    return n or None


def fit_lengths(obj, problems: list[str]) -> None:
    """Clip text that won't fit its column. SQLite ignores max_length; Postgres rejects the row."""
    for field in obj._meta.concrete_fields:
        limit = getattr(field, "max_length", None)
        value = getattr(obj, field.attname, None)
        if limit and isinstance(value, str) and len(value) > limit:
            problems.append(f"{obj._meta.model_name} {obj.pk}: {field.name} clipped from {len(value)} to {limit} chars")
            setattr(obj, field.attname, value[:limit])


def clean(rec: dict, fields: set[str]) -> dict:
    out = {}
    for k in fields:
        v = rec.get(k)
        if k in ("skins", "topics", "pairs_with_readings"):
            out[k] = v if isinstance(v, list) else ([] if v in (None, "") else [v])
        elif k in ("license_verified", "withdrawn"):
            out[k] = bool(v)
        else:
            out[k] = "" if v is None else str(v)
    return out


class Command(BaseCommand):
    help = "Load a corpus pack into the catalog tables."

    def add_arguments(self, parser):
        parser.add_argument("pack", nargs="?", default="rhetcomp")

    @transaction.atomic
    def handle(self, *args, pack: str, **opts):
        root = Path(settings.CORPUS_DIR) / pack
        cfg_path = root / "pack.yaml"
        if not cfg_path.exists():
            raise CommandError(f"No pack at {root}")
        cfg = load_yaml(cfg_path)
        files = cfg.get("files", {})

        p, _ = Pack.objects.update_or_create(
            id=cfg["id"], defaults={"name": cfg["name"], "version": str(cfg.get("version", "")), "config": cfg})

        # Arcs and units reference catalog rows, so clear them before the catalog.
        Arc.objects.filter(pack=p).delete()
        OutcomeSet.objects.filter(pack=p).delete()

        readings = jsonl(root / files.get("readings", "readings.jsonl"))
        Reading.objects.filter(pack=p).delete()
        clipped: list[str] = []
        rows = [Reading(id=r["id"], pack=p, pages=parse_pages(r.get("pages"), r.get("level", "")), **clean(r, READING_FIELDS))
                for r in readings]
        for row in rows:
            row.fill_index_fields()
            fit_lengths(row, clipped)
        Reading.objects.bulk_create(rows, batch_size=500)
        parents = {r["id"]: r.get("parent_id") for r in readings if r.get("parent_id")}
        known = set(Reading.objects.filter(pack=p).values_list("id", flat=True))
        for child, parent in parents.items():
            if parent in known:
                Reading.objects.filter(id=child).update(parent_id=parent)

        assignments = jsonl(root / files.get("assignments", "assignments.jsonl"))
        Assignment.objects.filter(pack=p).delete()
        rows = [Assignment(id=a["id"], pack=p, **clean(a, ASSIGNMENT_FIELDS)) for a in assignments]
        for row in rows:
            row.fill_index_fields()
            fit_lengths(row, clipped)
        Assignment.objects.bulk_create(rows, batch_size=500)

        Source.objects.filter(pack=p).delete()
        Source.objects.bulk_create([
            Source(id=s["id"], pack=p, name=s.get("name", s["id"]), url=s.get("url") or "", type=s.get("type") or "", data=s)
            for s in jsonl(root / files.get("sources", "sources.jsonl"))])

        n_out = self.load_outcomes(p, root / files.get("outcomes", "outcomes/"))
        n_arcs, problems = self.load_arcs(p, root / files.get("arcs", "arcs/"))
        problems += self.load_outcome_scores(p, root / files.get("outcomes", "outcomes/"))

        self.stdout.write(self.style.SUCCESS(
            f"{p.id}: {len(readings)} readings, {len(assignments)} assignments, "
            f"{n_out} outcome sets, {n_arcs} arcs"))
        for msg in clipped + problems:
            self.stdout.write(self.style.WARNING(msg))

    def load_outcomes(self, p: Pack, folder: Path) -> int:
        count = 0
        for f in sorted(folder.glob("*.yaml")):
            if f.stem == "crosswalk":  # topic -> code weights; see load_outcome_scores
                continue
            doc = load_yaml(f) or {}
            if f.stem == "frameworks":
                for fw in doc.get("frameworks", []) if isinstance(doc, dict) else []:
                    s = OutcomeSet.objects.create(
                        id=fw.get("id", f"fw-{count}"), pack=p, kind="framework", title=fw.get("name", fw.get("id", "")), data=fw)
                    entries = fw.get("categories") or fw.get("frames") or fw.get("outcomes") or []
                    for i, o in enumerate(entries):
                        body = o.get("definition") or o.get("description") or o.get("text") or ""
                        text = f"{o['name']}: {body}" if o.get("name") and body else (body or o.get("name", ""))
                        Outcome.objects.create(
                            id=o.get("code") or f"{s.id}-{i + 1}", outcome_set=s, order=i, text=text,
                            frameworks=[o["code"]] if o.get("code") else [], topics=o.get("topics", []))
                    count += 1
                continue
            s = OutcomeSet.objects.create(
                id=doc["id"], pack=p, kind="course", institution=doc.get("institution", ""),
                course=doc.get("course", ""), title=doc.get("title", ""),
                data={k: v for k, v in doc.items() if k != "outcomes"})
            for i, o in enumerate(doc.get("outcomes", [])):
                Outcome.objects.create(
                    id=o.get("id") or f"{s.id}-{i + 1}", outcome_set=s, order=i, text=o.get("text", ""),
                    frameworks=o.get("frameworks", []), topics=o.get("topics", []))
            count += 1
        return count

    def load_outcome_scores(self, p: Pack, folder: Path) -> list[str]:
        """Store the topic -> outcome crosswalk and score every item against it (catalog.outcomes).
        Runs after arcs, since items inherit the outcomes of arc units that use them."""
        codes = set(Outcome.objects.filter(outcome_set__pack=p, outcome_set__kind="framework").values_list("id", flat=True))
        topics = {t.lower() for model in (Reading, Assignment)
                  for ts in model.objects.filter(pack=p).values_list("topics", flat=True) for t in ts}
        crosswalk, problems = outcomes.load_crosswalk(folder / "crosswalk.yaml", codes, topics)
        p.crosswalk = crosswalk
        p.save(update_fields=["crosswalk"])
        outcomes.score_items(p, crosswalk)
        return problems

    def load_arcs(self, p: Pack, folder: Path) -> tuple[int, list[str]]:
        problems, count = [], 0
        reading_ids = set(Reading.objects.filter(pack=p).values_list("id", flat=True))
        assignment_ids = set(Assignment.objects.filter(pack=p).values_list("id", flat=True))
        for f in sorted(folder.glob("*.yaml")):
            doc = load_yaml(f)
            if not isinstance(doc, dict) or "units" not in doc:
                continue
            arc = Arc.objects.create(
                id=doc["id"], pack=p, focus=doc.get("focus", ""), title=doc.get("title", doc["id"]),
                tagline=doc.get("tagline", ""), rationale=(doc.get("rationale") or "").strip(),
                course_level=str(doc.get("course_level", "")), frameworks=doc.get("frameworks", []),
                fits_courses=doc.get("fits_courses", []))
            for order, u in enumerate(doc["units"]):
                weeks = u.get("weeks") or {}
                unit = Unit.objects.create(
                    arc=arc, key=u["id"], order=order, title=u.get("title", u["id"]), summary=u.get("summary", ""),
                    goals=u.get("goals", []), outcomes=u.get("outcomes", []), topics=u.get("topics", []),
                    min_weeks=int(weeks.get("min", 1)), ideal_weeks=int(weeks.get("ideal", 2)),
                    priority=u.get("priority", "core"), merge_with=u.get("merge_with") or "", notes=u.get("notes", "") or "")
                for role in ("core", "optional"):
                    for i, rid in enumerate((u.get("readings") or {}).get(role, []) or []):
                        if rid in reading_ids:
                            UnitReading.objects.create(unit=unit, reading_id=rid, role=role, order=i)
                        else:
                            problems.append(f"{arc.id}/{unit.key}: unknown reading {rid}")
                a = u.get("assignments") or {}
                plan = [("major", a.get("major") or []), ("short_term", [a["short_term_variant"]] if a.get("short_term_variant") else []),
                        ("activity", a.get("activities") or [])]
                for role, ids in plan:
                    for i, aid in enumerate(ids):
                        if aid in assignment_ids:
                            UnitAssignment.objects.create(unit=unit, assignment_id=aid, role=role, order=i)
                        else:
                            problems.append(f"{arc.id}/{unit.key}: unknown assignment {aid}")
            count += 1
        return count, problems
