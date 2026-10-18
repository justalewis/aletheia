"""Merge the raw research JSON files into one normalized, de-duplicated catalog.

Reads   research/raw/readings_*.json, assignments_*.json, repositories_*.json
        research/overrides.json  (hand-checked corrections, applied after the merge)
Writes  research/catalog/{readings,assignments,sources}.{json,csv}
        research/catalog/sheets/*.tsv  (chunked, trimmed columns for Google Sheets pasteData)
        research/catalog/stats.md
        research/catalog/merge_log.txt  (every override and automatic license tightening applied)

License rule: when sources disagree about an item's license, the catalog records the most
restrictive one and keeps the others in notes, so the builder never over-permits.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "research" / "raw"
OUT = ROOT / "research" / "catalog"
OVERRIDES = ROOT / "research" / "overrides.json"

SKINS = [
    "FYC", "RESEARCH", "ACADEMIC", "WAW", "TECHCOMM", "PROFWRIT", "BUSINESS", "SCIWRIT",
    "RHETORIC", "CULTURAL_RHET", "MULTIMODAL", "CREATIVE_NF", "L2", "SPEECH", "PEDAGOGY",
]

READING_FIELDS = [
    "id", "level", "title", "authors", "parent_title", "parent_id", "publisher_platform", "year",
    "license", "license_family", "reuse_tier", "license_url", "license_verified",
    "landing_url", "pdf_url", "html_url", "epub_url", "pages", "skins", "topics",
    "course_level", "audience", "summary", "notes", "drive_file_id", "drive_path",
    "source_agent", "also_found_by",
]
ASSIGNMENT_FIELDS = [
    "id", "title", "authors", "source_repository", "year", "license", "license_family", "reuse_tier",
    "license_url", "license_verified", "url", "file_url", "assignment_type", "kind", "skins", "topics",
    "estimated_weeks", "course_level", "pairs_with_readings", "summary", "notes", "source_agent",
    "also_found_by",
]
SOURCE_FIELDS = [
    "id", "name", "url", "type", "content_scope", "default_license", "license_verified",
    "item_count_estimate", "has_api", "harvest_method", "api_docs_url", "api_notes", "auth",
    "metadata_exposed", "rate_limits", "skins", "notes", "source_agent",
]

# Trimmed column sets for the Google Sheet (keeps the upload small and the sheet readable).
SHEET_READING_COLS = [
    "title", "authors", "parent_title", "level", "year", "license", "reuse_tier", "skins",
    "topics", "audience", "link", "pdf_url", "drive_path", "summary_short", "license_verified", "id",
]
SHEET_ASSIGNMENT_COLS = [
    "title", "authors", "source_repository", "year", "license", "reuse_tier", "kind",
    "assignment_type", "skins", "topics", "estimated_weeks", "link", "summary_short",
    "license_verified", "id",
]
SHEET_SOURCE_COLS = [
    "name", "type", "url", "content_scope", "default_license", "has_api", "harvest_method",
    "api_docs_url", "item_count_estimate", "notes",
]

DRIVE_INVENTORY = ROOT / "research" / "drive_inventory.json"
FRONT_BACK_MATTER = re.compile(r"\b(index|glossary|acknowledg\w*|contributors|front matter|front)\b", re.I)
REFERENCE_FIELDS = ["title", "classification", "publisher", "drive_path", "drive_file_id", "notes"]


def norm_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[‘’“”'\"]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


TRACKING_PARAM = re.compile(r"^(utm_\w+|fbclid|gclid|ref)$", re.I)


def norm_url(u: str) -> str:
    """Host + path + query, ignoring scheme, www, case, trailing slash and tracking params.

    The query matters: repository download links (viewcontent.cgi?article=..., article/file?id=...)
    differ only there.
    """
    u = (u or "").strip()
    if not u:
        return ""
    parts = urlsplit(u if "://" in u else "https://" + u)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    query = "&".join(sorted(p for p in parts.query.split("&") if p and not TRACKING_PARAM.match(p.split("=")[0])))
    return f"{host}{path}?{query}" if query else f"{host}{path}"


def license_family(lic: str) -> str:
    l = (lic or "").lower().replace("–", "-").replace("—", "-")
    if not l.strip():
        return "Unknown"
    is_cc = bool(re.search(r"\bcc\b", l)) or "creative commons" in l
    if "all rights reserved" in l or "free access" in l or ("copyright" in l and not is_cc):
        return "Free access (all rights reserved)"
    if "public domain" in l or "pdm" in l or "us government" in l or "federal" in l:
        return "Public Domain"
    if "cc0" in l or "cc zero" in l:
        return "CC0"
    if "open government licen" in l or "ogl" in l:
        return "OGL"
    if "w3c document license" in l:
        return "W3C Document License"
    if is_cc:
        nc ="nc" in re.findall(r"[a-z]+", l) or "noncommercial" in l or "non-commercial" in l
        nd = "nd" in re.findall(r"[a-z]+", l) or "noderiv" in l or "no deriv" in l
        sa = "sa" in re.findall(r"[a-z]+", l) or "sharealike" in l or "share alike" in l
        fam = "CC BY"
        if nc:
            fam += "-NC"
        if nd:
            fam += "-ND"
        elif sa:
            fam += "-SA"
        return fam
    if "mit" in l or "apache" in l or "gfdl" in l:
        return "Other open"
    return "Other/unclear"


def reuse_tier(fam: str) -> str:
    """How the builder may use an item.

    adapt     : copy, excerpt, edit (CC BY, BY-SA, CC0, PD, OGL)
    adapt-nc  : copy, excerpt, edit, non-commercial only (BY-NC, BY-NC-SA)
    verbatim  : copy whole, no edits or excerpts (BY-ND, BY-NC-ND)
    link-only : point to it, never copy
    """
    if fam in ("CC BY", "CC BY-SA", "CC0", "Public Domain", "OGL", "Other open"):
        return "adapt"
    if fam in ("CC BY-NC", "CC BY-NC-SA"):
        return "adapt-nc"
    if fam in ("CC BY-ND", "CC BY-NC-ND", "W3C Document License"):
        return "verbatim"
    return "link-only"


TIER_ORDER = ("adapt", "adapt-nc", "verbatim", "link-only")


def restriction_key(fam: str) -> tuple:
    """Sort key for license families: higher means fewer permissions."""
    return (TIER_ORDER.index(reuse_tier(fam)), "NC" in fam, "ND" in fam, "SA" in fam,
            fam not in ("Public Domain", "CC0"))


def combined_family(fams) -> str:
    """The family that permits only what every one of `fams` permits.

    Usually that is simply the strictest input, but CC BY-ND and CC BY-NC-SA, say, combine to
    CC BY-NC-ND.
    """
    fams = list(fams)
    worst = max(fams, key=restriction_key)
    if reuse_tier(worst) == "link-only":
        return worst
    nc = any("NC" in f for f in fams)
    nd = any("ND" in f or f == "W3C Document License" for f in fams)
    sa = any("SA" in f for f in fams)
    if not (nc or nd or sa) or (nd and worst == "W3C Document License" and not nc):
        return worst
    return "CC BY" + ("-NC" if nc else "") + ("-ND" if nd else "-SA" if sa else "")


def strictest(records: list[dict]) -> tuple[dict | None, str]:
    """Among records with a license, the one to follow, and the license string to record.

    Prefers a verified record when several share the strictest family; if no input has the
    combined family, returns a synthesized license string naming its sources.
    """
    licensed = [r for r in records if (r.get("license") or "").strip()]
    if not licensed:
        return None, ""
    target = combined_family({license_family(r["license"]) for r in licensed})
    matches = [r for r in licensed if license_family(r["license"]) == target]
    if matches:
        pick = max(matches, key=lambda r: as_bool(r.get("license_verified")))
        return pick, pick["license"]
    names = "; ".join(sorted({r["license"] for r in licensed}))
    return None, f"{target} (most restrictive combination of: {names})"


def as_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[;,|]", str(v)) if x.strip()]


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1", "y")


def load(pattern: str) -> list[dict]:
    rows = []
    for f in sorted(RAW.glob(pattern)):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"!! could not parse {f.name}: {e}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            data = data.get("items") or data.get("records") or list(data.values())[0]
        for r in data:
            if isinstance(r, dict):
                r = {k: clean_text(v) for k, v in r.items()}
                r.setdefault("_file", f.name)
                rows.append(r)
    return rows


def filled(r: dict, fields: list[str]) -> int:
    return sum(1 for k in fields if r.get(k) not in (None, "", [], False))


WHOLE_WORK_LEVELS = {"book", "collection", "journal", "website"}


def title_sig(title: str) -> str:
    return " ".join(norm_text(title).split()[:6])


def dedupe(rows: list[dict], fields: list[str], url_keys: list[str], title_key: str,
           parent_key: str | None, url_alone: bool = False, aliases: dict | None = None) -> list[dict]:
    """Merge records that share a URL or the same normalized (title, parent).

    Chapters often reuse their book's landing page or whole-book PDF, so a shared URL
    only merges records whose titles also start the same, unless url_alone is set or
    the record is a whole work (book, collection, journal, website).

    Records from the same raw file with different ids were listed separately on purpose
    (two essays with one title, volumes 1 and 2), so they never merge with each other.

    If merged records disagree about the license, the most restrictive one wins and the
    others go into notes. `aliases` (if given) collects merged-away id -> surviving id, so
    parent_id references to a merged-away record can be repointed.
    """
    groups: list[list[dict]] = []
    index: dict[str, int] = {}

    def separate_on_purpose(group: list[dict], r: dict) -> bool:
        rid = r.get("id")
        return bool(rid) and any(o.get("_file") == r.get("_file") and o.get("id") and o.get("id") != rid
                                 for o in group)

    for r in rows:
        whole = url_alone or r.get("level") in WHOLE_WORK_LEVELS
        sig = "" if whole else "|" + title_sig(r.get(title_key, ""))
        keys = [("u:" + norm_url(r.get(k, "")) + sig) for k in url_keys if norm_url(r.get(k, ""))]
        t = norm_text(r.get(title_key, ""))
        if t:
            p = norm_text(r.get(parent_key, "")) if parent_key else ""
            keys.append(f"t:{t}|{p}|{r.get('level', '')}")
        hit = next((index[k] for k in keys if k in index and not separate_on_purpose(groups[index[k]], r)), None)
        if hit is None:
            hit = len(groups)
            groups.append([])
        groups[hit].append(r)
        for k in keys:
            index.setdefault(k, hit)
    merged = []
    for g in groups:
        g.sort(key=lambda r: (as_bool(r.get("license_verified")), filled(r, fields)), reverse=True)
        best = dict(g[0])
        for other in g[1:]:
            for k in fields:
                if best.get(k) in (None, "", []) and other.get(k) not in (None, "", []):
                    best[k] = other[k]
            for k in ("skins", "topics"):
                if k in best or k in other:
                    best[k] = sorted(set(as_list(best.get(k))) | set(as_list(other.get(k))))
        agents = sorted({r.get("source_agent", "") for r in g} - {best.get("source_agent", ""), ""})
        best["also_found_by"] = "; ".join(agents)
        if aliases is not None and best.get("id"):
            for o in g[1:]:
                if o.get("id") and o["id"] != best["id"]:
                    aliases[o["id"]] = best["id"]
        if len({license_family(o.get("license", "")) for o in g if (o.get("license") or "").strip()}) > 1:
            pick, lic = strictest(g)
            if license_family(lic) != license_family(best.get("license", "")):
                best["license"] = lic
                best["license_url"] = pick.get("license_url", "") if pick else ""
                best["license_verified"] = as_bool(pick.get("license_verified")) if pick else False
            seen = "; ".join(sorted({f"{o['license']} ({o.get('source_agent') or o['_file']})"
                                     for o in g if (o.get("license") or "").strip()}))
            best["notes"] = (best.get("notes", "") + " " if best.get("notes") else "") + \
                f"License conflict between merged records: {seen}. Recorded the most restrictive."
        merged.append(best)
    return merged


def apply_overrides(rows: list[dict], kind: str, overrides: dict) -> list[str]:
    """Apply research/overrides.json to merged rows; returns a log of what changed.

    "rules" match records by regex on fields (a key like "title|parent_title" matches either
    field); entries under "readings"/"assignments" are keyed by final record id and win over rules.
    Each entry can "set" fields, rewrite notes with "notes_sub" ([pattern, replacement] pairs),
    and append a "note".
    """
    log: list[str] = []
    by_id = {r.get("id"): r for r in rows}

    def field_text(r: dict, k: str) -> str:
        v = r.get(k, "")
        return "; ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v))

    def apply(r: dict, entry: dict, label: str) -> None:
        before = r.get("license", "")
        for k, v in entry.get("set", {}).items():
            r[k] = v
        notes = r.get("notes", "") or ""
        for pat, repl in entry.get("notes_sub", []):
            notes = re.sub(pat, repl, notes)
        if entry.get("note"):
            notes = (notes.rstrip() + " " if notes.strip() else "") + entry["note"]
        r["notes"] = notes.strip()
        change = f" license: {before!r} -> {r.get('license', '')!r}" if r.get("license", "") != before else ""
        log.append(f"{kind} {r.get('id')}: {label}{change}")

    for rule in overrides.get("rules", []):
        if rule.get("applies_to", "readings") != kind:
            continue
        pats = {k: re.compile(v, re.I) for k, v in rule.get("match", {}).items()}
        hits = [r for r in rows if pats and all(any(p.search(field_text(r, f)) for f in k.split("|"))
                                                for k, p in pats.items())]
        if not hits:
            print(f"!! override rule {rule.get('name')!r} matched no {kind}", file=sys.stderr)
        for r in hits:
            apply(r, rule, f"rule {rule.get('name')}")
    for rid, entry in overrides.get(kind, {}).items():
        if rid.startswith("_"):
            continue
        if rid not in by_id:
            print(f"!! override for unknown {kind} id {rid!r}", file=sys.stderr)
            continue
        apply(by_id[rid], entry, "override")
    return log


def unique_ids(rows: list[dict], title_key: str) -> None:
    seen: Counter = Counter()
    for r in rows:
        base = r.get("id") or re.sub(r"\s+", "-", norm_text(r.get(title_key, "")))[:60] or "item"
        base = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")
        seen[base] += 1
        r["id"] = base if seen[base] == 1 else f"{base}-{seen[base]}"


MOJIBAKE = {"â€™": "'", "â€˜": "'", "â€œ": '"', "â€\x9d": '"', "â€“": "-", "â€”": "-", "Ã©": "é"}


def clean_text(s):
    if not isinstance(s, str):
        return s
    for bad, good in MOJIBAKE.items():
        s = s.replace(bad, good)
    s = re.sub(r"(?<=\w)�(?=\w)", "'", s)  # lost apostrophe inside a word
    return s.replace("�", "").strip()


def finalize(rows: list[dict], fields: list[str]) -> list[dict]:
    out = []
    for r in rows:
        for k, v in list(r.items()):
            r[k] = clean_text(v)
        fam = license_family(r.get("license") or r.get("default_license", ""))
        r["license_family"] = fam
        r["reuse_tier"] = reuse_tier(fam)
        r["skins"] = [s for s in (x.upper().replace(" ", "_") for x in as_list(r.get("skins"))) if s in SKINS]
        r["topics"] = sorted({t.lower() for t in as_list(r.get("topics"))})
        if "pairs_with_readings" in fields:
            r["pairs_with_readings"] = as_list(r.get("pairs_with_readings"))
        r["license_verified"] = as_bool(r.get("license_verified"))
        out.append({k: r.get(k, "") for k in fields})
    return out


def tighten_parents(readings: list[dict]) -> list[str]:
    """A whole book (collection, journal) can't be reused more freely than its strictest part.

    Runs after finalize(). When a parent's license is looser than one of its children's, the
    parent takes the strictest child license and keeps its own in notes. Parents with no license
    are left alone: they are already link-only.
    """
    log: list[str] = []
    for _ in range(3):  # chapter -> book -> collection
        kids: dict[str, list[dict]] = defaultdict(list)
        for r in readings:
            if r.get("parent_id") and r.get("license"):
                kids[r["parent_id"]].append(r)
        changed = False
        for p in readings:
            if not p.get("license") or p["id"] not in kids:
                continue
            ch = kids[p["id"]]
            target = combined_family([c["license_family"] for c in ch] + [p["license_family"]])
            if restriction_key(target) <= restriction_key(p["license_family"]) or target == p["license_family"]:
                continue
            pick, lic = strictest([c for c in ch if license_family(c["license"]) == target] or ch + [p])
            own = p["license"] + (f" ({p['license_url']})" if p.get("license_url") else "")
            log.append(f"readings {p['id']}: tightened to strictest part license: {p['license']!r} -> {lic!r}")
            p["notes"] = (p["notes"] + " " if p.get("notes") else "") + (
                f"Parts carry their own licenses; the strictest ({lic}) is recorded here so reuse of the "
                f"whole never over-permits. Own license statement: {own}. Use the part records for "
                f"part-level reuse.")
            p["license"] = lic
            p["license_url"] = pick.get("license_url", "") if pick else ""
            p["license_verified"] = bool(p["license_verified"] and (pick or {}).get("license_verified"))
            p["license_family"] = license_family(lic)
            p["reuse_tier"] = reuse_tier(p["license_family"])
            changed = True
        if not changed:
            break
    return log


def link_drive_inventory(readings: list[dict]) -> list[dict]:
    """Attach Drive file ids to matching readings; add unmatched OA files; return commercial refs."""
    if not DRIVE_INVENTORY.exists():
        return []
    inv = json.loads(DRIVE_INVENTORY.read_text(encoding="utf-8"))
    books = {norm_text(b.get("folder_path", "")): b for b in inv.get("books", [])}
    by_title: dict[str, list[dict]] = defaultdict(list)
    for r in readings:
        by_title[norm_text(r.get("title", ""))].append(r)

    def find(title: str, book: str, level: str | None) -> dict | None:
        t = norm_text(title)
        if not t:
            return None
        cands = by_title.get(t, [])
        if not cands and len(t) >= 15:
            cands = [r for k, rs in by_title.items() if k and (t in k or k in t) and len(k) >= 15 for r in rs]
        if level:
            cands = [r for r in cands if r.get("level") == level] or cands
        if book and len(cands) > 1:
            b = norm_text(book)
            cands = [r for r in cands if b and b[:20] in norm_text(r.get("parent_title", ""))] or cands
        return cands[0] if cands else None

    reference = []
    for f in inv.get("files", []):
        if f.get("is_folder"):
            continue
        cls = f.get("classification", "")
        if cls == "commercial":
            reference.append({
                "title": f.get("book_title") or f.get("name", ""),
                "classification": cls,
                "publisher": f.get("publisher", ""),
                "drive_path": f.get("path", ""),
                "drive_file_id": f.get("drive_id", ""),
                "notes": f.get("notes", ""),
            })
            continue
        if cls not in ("oa", "free-access"):
            continue
        if FRONT_BACK_MATTER.search(f.get("chapter_title") or f.get("name", "")):
            continue
        folder = norm_text(str(f.get("path", "")).rsplit("/", 1)[0]) if "/" in str(f.get("path", "")) else ""
        book = books.get(folder, {})
        book_title = f.get("book_title") or book.get("title", "")
        chapter = f.get("chapter_title", "")
        hit = find(chapter, book_title, "chapter") if chapter else find(book_title, "", "book")
        if hit:
            hit["drive_file_id"] = f.get("drive_id", "")
            hit["drive_path"] = f.get("path", "")
            continue
        lic = f.get("license") or book.get("license", "")
        parent = find(book_title, "", "book") if chapter else None
        rec = {
            "level": "chapter" if chapter else "book",
            "title": chapter or book_title or f.get("name", ""),
            "authors": f.get("chapter_authors", "") if chapter else book.get("authors", ""),
            "parent_title": book_title if chapter else "",
            "publisher_platform": book.get("publisher", ""),
            "year": book.get("year", ""),
            "license": lic,
            "license_url": book.get("license_source_url", ""),
            "license_verified": bool(book.get("license_verified")),
            "notes": "Found in Drive corpus; not yet matched to a web record. " + (f.get("notes") or ""),
            "drive_file_id": f.get("drive_id", ""),
            "drive_path": f.get("path", ""),
            "source_agent": "drive-corpus",
            "skins": (parent or {}).get("skins", []),
            "course_level": (parent or {}).get("course_level", ""),
            "audience": (parent or {}).get("audience", ""),
        }
        readings.append(rec)
        by_title[norm_text(rec["title"])].append(rec)
    return reference


def cell(v) -> str:
    if isinstance(v, list):
        v = "; ".join(v)
    if isinstance(v, bool):
        v = "TRUE" if v else "FALSE"
    s = "" if v is None else str(v)
    return re.sub(r"[\t\r\n]+", " ", s).strip()


def sheet_cell(v) -> str:
    """cell() plus guards for Google Sheets pasteData: no straight quotes, no formula starts."""
    s = cell(v)
    s = re.sub(r'(?<![\w.,!?])"', "“", s).replace('"', "”")
    if s[:1] in ("=", "+", "-", "@"):
        s = "'" + s
    return s


def sheet_value(r: dict, k: str):
    """Columns that exist only in the Google Sheet view."""
    if k == "link":
        return r.get("html_url") or r.get("landing_url") or r.get("url") or r.get("pdf_url") or r.get("file_url", "")
    if k == "summary_short":
        s = cell(r.get("summary", ""))
        return s if len(s) <= 220 else s[:217].rsplit(" ", 1)[0] + "..."
    return r.get(k)


def write_tables(name: str, rows: list[dict], fields: list[str], sheet_cols: list[str]) -> None:
    (OUT / f"{name}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    with (OUT / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for r in rows:
            w.writerow([cell(r.get(k)) for k in fields])
    sheets = OUT / "sheets"
    sheets.mkdir(exist_ok=True)
    for old in list(sheets.glob(f"{name}_*.tsv")) + list(sheets.glob(f"{name}_manifest.json")):
        old.unlink()
    lines = ["\t".join(sheet_cols)] + ["\t".join(sheet_cell(sheet_value(r, k)) for k in sheet_cols) for r in rows]
    chunks: list[list[str]] = [[]]
    size = 0
    for line in lines:
        if size + len(line) > 40_000 and chunks[-1]:
            chunks.append([])
            size = 0
        chunks[-1].append(line)
        size += len(line) + 1
    manifest, start = [], 0
    for n, chunk in enumerate(chunks, 1):
        path = sheets / f"{name}_{n:02d}.tsv"
        path.write_text("\n".join(chunk), encoding="utf-8")
        manifest.append({"file": path.name, "start_row": start, "rows": len(chunk)})
        start += len(chunk)
    (sheets / f"{name}_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    readings = load("readings_*.json")
    assignments = load("assignments_*.json")
    sources = load("repositories_*.json")

    aliases: dict[str, str] = {}
    readings = dedupe(readings, READING_FIELDS, ["pdf_url", "html_url", "landing_url"], "title", "parent_title",
                      aliases=aliases)
    assignments = dedupe(assignments, ASSIGNMENT_FIELDS, ["file_url", "url"], "title", "source_repository")
    sources = dedupe(sources, SOURCE_FIELDS, ["url"], "name", None, url_alone=True)

    # Chapters may point at a book record that merged into another one; follow it.
    for r in readings:
        if r.get("parent_id") in aliases:
            r["parent_id"] = aliases[r["parent_id"]]

    reference = link_drive_inventory(readings)

    # Fill missing parent_id from a book/collection record with the same title.
    parents = {norm_text(r.get("title", "")): r.get("id") for r in readings
               if r.get("level") in ("book", "collection", "journal", "website")}
    for r in readings:
        if not r.get("parent_id") and r.get("parent_title"):
            r["parent_id"] = parents.get(norm_text(r["parent_title"]), "")

    unique_ids(readings, "title")
    unique_ids(assignments, "title")
    unique_ids(sources, "name")

    override_log: list[str] = []
    if OVERRIDES.exists():
        overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
        override_log += apply_overrides(readings, "readings", overrides)
        override_log += apply_overrides(assignments, "assignments", overrides)

    readings = finalize(readings, READING_FIELDS)
    assignments = finalize(assignments, ASSIGNMENT_FIELDS)
    sources = finalize(sources, SOURCE_FIELDS)
    tighten_log = tighten_parents(readings)

    level_order = {"collection": 0, "website": 1, "journal": 1, "book": 2, "chapter": 3, "article": 4, "primary-text": 5}
    readings.sort(key=lambda r: (r["source_agent"], r["parent_title"] or r["title"], level_order.get(r["level"], 9), r["title"]))
    assignments.sort(key=lambda r: (r["source_repository"], r["title"]))
    sources.sort(key=lambda r: (r["type"], r["name"]))

    write_tables("readings", readings, READING_FIELDS, SHEET_READING_COLS)
    write_tables("assignments", assignments, ASSIGNMENT_FIELDS, SHEET_ASSIGNMENT_COLS)
    write_tables("sources", sources, SOURCE_FIELDS, SHEET_SOURCE_COLS)
    reference.sort(key=lambda r: r["title"])
    write_tables("commercial_reference", reference, REFERENCE_FIELDS, REFERENCE_FIELDS)

    # Stats
    def tally(rows, key, multi=False):
        c = Counter()
        for r in rows:
            vals = r[key] if multi else [r[key]]
            for v in vals or ["(none)"]:
                c[v or "(none)"] += 1
        return c

    lines = ["# Catalog stats", ""]
    lines.append(f"- Readings: {len(readings)} records")
    lines.append(f"- Assignments / syllabi: {len(assignments)} records")
    lines.append(f"- Sources and repositories: {len(sources)} records")
    lines.append(f"- Readings with a file in the Drive corpus: {sum(bool(r['drive_file_id']) for r in readings)}")
    lines.append(f"- Commercial books in the Drive corpus (reference only, not in catalog): {len(reference)}")
    lines.append(f"- Readings with license verified on page: {sum(r['license_verified'] for r in readings)}")
    lines.append(f"- Records changed by research/overrides.json: {len({l.split(':')[0] for l in override_log})}")
    lines.append(f"- Parent records tightened to their strictest part's license: {len(tighten_log)}")
    for title, rows, key, multi in [
        ("Readings by level", readings, "level", False),
        ("Readings by reuse tier", readings, "reuse_tier", False),
        ("Readings by license family", readings, "license_family", False),
        ("Readings by skin", readings, "skins", True),
        ("Readings by source agent", readings, "source_agent", False),
        ("Assignments by kind", assignments, "kind", False),
        ("Assignments by skin", assignments, "skins", True),
        ("Assignments by reuse tier", assignments, "reuse_tier", False),
        ("Assignments by repository", assignments, "source_repository", False),
    ]:
        lines += ["", f"## {title}", ""]
        for k, v in tally(rows, key, multi).most_common():
            lines.append(f"- {k}: {v}")
    (OUT / "stats.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "merge_log.txt").write_text("\n".join(override_log + tighten_log) + "\n", encoding="utf-8")
    print("\n".join(lines[:10]))


if __name__ == "__main__":
    main()
