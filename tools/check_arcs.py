"""Check sample course arcs against the pack before loading them.

    python tools/check_arcs.py                     # every arc in corpus/rhetcomp/arcs
    python tools/check_arcs.py arcs/sci-*.yaml     # just these

Errors (exit 1): unknown reading or assignment ids, unknown focus or outcome ids, bad weeks,
priority or merge_with values, a file whose id doesn't match its name. Warnings: the schema's
soft rules in arcs/README.md (core readings that can't go in the reader, counts outside the
suggested ranges, a semester that doesn't add up to about 14 weeks of units, repeated readings).
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import yaml

PACK = Path(__file__).resolve().parent.parent / "corpus" / "rhetcomp"
LEVELS = {"100", "200", "300-400", "mixed"}
PART_LEVELS = {"chapter", "article", "primary-text"}


def load_jsonl(name: str) -> dict[str, dict]:
    with open(PACK / name, encoding="utf-8") as fh:
        return {r["id"]: r for r in map(json.loads, fh)}


def outcome_ids() -> set[str]:
    """framework:outcome ids the arcs may cite, taken from the arcs and outcome files already in the pack."""
    known = set()
    for f in (PACK / "arcs").glob("*.yaml"):
        for u in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("units", []):
            known.update(u.get("outcomes") or [])
    return known


def check(path: Path, readings, assignments, focuses, outcomes) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "units" not in doc:
        return [f"{path.name}: not an arc (no units)"], []
    if doc.get("id") != path.stem:
        errors.append(f"id {doc.get('id')!r} doesn't match the file name")
    if doc.get("focus") not in focuses:
        errors.append(f"focus {doc.get('focus')!r} isn't a top-level focus in pack.yaml")
    for key in ("title", "tagline", "rationale"):
        if not str(doc.get(key) or "").strip():
            errors.append(f"missing {key}")
    if str(doc.get("course_level")) not in LEVELS:
        errors.append(f"course_level {doc.get('course_level')!r} should be one of {sorted(LEVELS)}")
    seen_units, seen_readings, total = set(), {}, 0
    for u in doc["units"]:
        key = u.get("id", "?")
        where = f"{key}:"
        if key in seen_units:
            errors.append(f"{where} unit id repeated")
        seen_units.add(key)
        for field in ("title", "summary"):
            if not str(u.get(field) or "").strip():
                errors.append(f"{where} missing {field}")
        if not 2 <= len(u.get("goals") or []) <= 4:
            warnings.append(f"{where} {len(u.get('goals') or [])} goals (README suggests 2-4)")
        for o in u.get("outcomes") or []:
            if o not in outcomes:
                errors.append(f"{where} unknown outcome {o}")
        weeks = u.get("weeks") or {}
        lo, ideal = weeks.get("min"), weeks.get("ideal")
        if not (isinstance(lo, int) and isinstance(ideal, int) and 1 <= lo <= ideal):
            errors.append(f"{where} weeks should be {{min, ideal}} with 1 <= min <= ideal")
        else:
            total += ideal
        if u.get("priority") not in ("core", "optional"):
            errors.append(f"{where} priority {u.get('priority')!r} should be core or optional")
        if u.get("merge_with") not in ("next", "previous", None):
            errors.append(f"{where} merge_with {u.get('merge_with')!r} should be next, previous, or null")
        r = u.get("readings") or {}
        core, optional = r.get("core") or [], r.get("optional") or []
        if not 2 <= len(core) <= 4:
            warnings.append(f"{where} {len(core)} core readings (README suggests 2-4)")
        if not 3 <= len(optional) <= 8:
            warnings.append(f"{where} {len(optional)} optional readings (README suggests 3-8)")
        for role, ids in (("core", core), ("optional", optional)):
            for rid in ids:
                rec = readings.get(rid)
                if rec is None:
                    errors.append(f"{where} unknown reading {rid}")
                    continue
                if rid in seen_readings:
                    warnings.append(f"{where} {rid} is also in unit {seen_readings[rid]}")
                seen_readings.setdefault(rid, key)
                if role == "core":
                    if not (rec.get("license_verified") and rec.get("reuse_tier") in ("adapt", "adapt-nc", "verbatim")):
                        warnings.append(f"{where} core reading {rid} can't go in the reader "
                                        f"({rec.get('reuse_tier')}, verified={rec.get('license_verified')})")
                    if rec.get("level") not in PART_LEVELS:
                        warnings.append(f"{where} core reading {rid} is a {rec.get('level')}, not a chapter or article")
        a = u.get("assignments") or {}
        ids = list(a.get("major") or []) + list(a.get("activities") or [])
        if a.get("short_term_variant"):
            ids.append(a["short_term_variant"])
        if len(a.get("major") or []) > 1:
            warnings.append(f"{where} more than one major assignment")
        for aid in ids:
            if aid not in assignments:
                errors.append(f"{where} unknown assignment {aid}")
    if not 12 <= total <= 15:
        warnings.append(f"units' ideal weeks add up to {total}; a 16-week semester wants about 14")
    return errors, warnings


def main(argv: list[str]) -> int:
    readings, assignments = load_jsonl("readings.jsonl"), load_jsonl("assignments.jsonl")
    pack = yaml.safe_load((PACK / "pack.yaml").read_text(encoding="utf-8"))
    focuses = {f["code"] for f in pack["focuses"] if not f.get("tag_only")}
    outcomes = outcome_ids()
    paths = [Path(p) for a in argv for p in glob.glob(a)] or sorted((PACK / "arcs").glob("*.yaml"))
    failed = False
    for path in paths:
        errors, warnings = check(path, readings, assignments, focuses, outcomes)
        status = "FAIL" if errors else ("warn" if warnings else "ok")
        print(f"{status:4}  {path.name}")
        for e in errors:
            print(f"      error: {e}")
        for w in warnings:
            print(f"      warning: {w}")
        failed |= bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
