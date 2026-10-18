"""Turn harvested candidates into a review queue, and approve reviewed records into the pack.

Build today's queue from research/harvest/*-candidates.jsonl:

    python tools/harvest/merge.py

writes research/harvest/queue-YYYYMMDD.jsonl (one candidate per line) and queue-YYYYMMDD.md
(books grouped by suggested focus, for skimming). Candidates are deduplicated against each
other (the same book from Pressbooks and the Open Textbook Library becomes one record) and
against corpus/rhetcomp/readings.jsonl:

- exact duplicates (same normalized URL, or same normalized title + parent) are left out of
  the queue and listed at the end of the .md file;
- near duplicates (same title once numbering, subtitles, and edition words are stripped) stay
  in the queue with queue.status "near-duplicate" and queue.match naming the existing records.

Approve after review (edit queue lines first if a tag or license needs fixing):

    python tools/harvest/merge.py --approve all --queue research/harvest/queue-20261017.jsonl
    python tools/harvest/merge.py --approve some-book-id,another-id --queue ...
    python tools/harvest/merge.py --approve all --queue ... --dry-run

"all" means every record with status "new"; near duplicates must be named. Approving a book
also approves its chapters (unless --book-only); approving a chapter brings its book along if
the book isn't in the pack yet. Approved records are appended to readings.jsonl with new,
permanent ids; existing lines are never changed, and the file stays sorted by id the way
tools/export_pack.py writes it. Then run `python manage.py load_pack rhetcomp` from backend/.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    HARVEST_DIR, PACK_FIELDS, PACK_READINGS, ROOT, license_family, norm_text, norm_url, read_jsonl, reuse_tier,
    slug, suggested_focus, write_jsonl,
)
from build_catalog import WHOLE_WORK_LEVELS, strictest  # noqa: E402

URL_FIELDS = ("landing_url", "html_url", "pdf_url", "epub_url")
CANDIDATE_FILES = ["pressbooks-candidates.jsonl", "otl-candidates.jsonl"]
SOURCE_PREFERENCE = {"pressbooks": 0, "otl": 1}  # when one book comes from both, keep the Pressbooks record

EDITION_WORDS = re.compile(
    r"\b(\d+(st|nd|rd|th)|first|second|third|fourth|fifth|sixth|revised|updated|new|expanded|international|canadian|"
    r"open|\d{4})\s+(edition|ed|version)\b|\b(edition|version|vol(ume)?)\s+\w+\b|\b\d+e\b|\bedition\b",
    re.I)
NUMBERING = re.compile(  # "1.2 ", "2. ", "Chapter 3: ", "Appendix A: ", "IV. ", "B) "
    r"^\s*(?:(?:chapter|unit|part|section|module|lesson|appendix)\s+[\w.\-]+\s*[.:)\-—]?\s+|"
    r"\d+(?:[.\-]\d+)*\s*[.:)]?\s+|[ivxlc]+[.:)]\s+|[a-z][.:)]\s+)", re.I)


def level_class(level: str) -> str:
    return "whole" if level in WHOLE_WORK_LEVELS else "part"


def main_title(title: str) -> str:
    return re.split(r"\s*[:(—]\s*|\s+-\s+", title or "", maxsplit=1)[0]


def loose_title(title: str) -> str:
    """Title without numbering, edition words, and (when the rest is specific enough) subtitle.

    "Technical Writing Essentials, 2nd Edition" -> "technical writing essentials". A short main
    title keeps its subtitle, so "Business Communication: A Relational Approach" and "Business
    Communication: Five Core Competencies" stay apart.
    """
    t = NUMBERING.sub("", title or "")
    main = norm_text(EDITION_WORDS.sub(" ", main_title(t)))
    if len(main.split()) >= 3:
        return main
    return norm_text(EDITION_WORDS.sub(" ", t))


def url_keys(r: dict) -> set[str]:
    return {norm_url(r.get(f, "")) for f in URL_FIELDS if norm_url(r.get(f, ""))}


def title_key(r: dict) -> str:
    return f"{norm_text(r.get('title', ''))}|{norm_text(r.get('parent_title', ''))}|{level_class(r.get('level', ''))}"


def loose_key(r: dict) -> str:
    t = loose_title(r.get("title", ""))
    if not t or len(t) < 6:
        return ""
    return f"{t}|{loose_title(r.get('parent_title', ''))}|{level_class(r.get('level', ''))}"


# ---------------------------------------------------------------------------
# Matching against the pack
# ---------------------------------------------------------------------------

class PackIndex:
    def __init__(self, pack: list[dict]):
        self.by_id = {r["id"]: r for r in pack}
        self.url: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.title: dict[str, list[str]] = defaultdict(list)
        self.loose: dict[str, list[str]] = defaultdict(list)
        for r in pack:
            cls = level_class(r.get("level", ""))
            for u in url_keys(r):
                self.url[(u, cls)].append(r["id"])
            self.title[title_key(r)].append(r["id"])
            if k := loose_key(r):
                self.loose[k].append(r["id"])

    def match(self, c: dict) -> tuple[str, list[dict]]:
        """('duplicate' | 'near-duplicate' | 'new', [{'id', 'title', 'how'}])."""
        cls = level_class(c.get("level", ""))
        exact = []
        for u in url_keys(c):
            for rid in self.url.get((u, cls), []):
                # Chapters sometimes share their book's landing page, so a part needs its title to agree too.
                if cls == "part" and loose_title(self.by_id[rid]["title"]) != loose_title(c["title"]):
                    continue
                exact.append((rid, "url"))
        exact += [(rid, "title") for rid in self.title.get(title_key(c), [])]
        if exact:
            return "duplicate", self._describe(exact)
        near = [(rid, "similar title") for rid in self.loose.get(loose_key(c), [])] if loose_key(c) else []
        if near:
            return "near-duplicate", self._describe(near)
        return "new", []

    def _describe(self, hits: list[tuple[str, str]]) -> list[dict]:
        seen, out = set(), []
        for rid, how in hits:
            if rid not in seen:
                seen.add(rid)
                r = self.by_id[rid]
                out.append({"id": rid, "title": r.get("title", ""), "parent_title": r.get("parent_title", ""),
                            "license": r.get("license", ""), "how": how})
        return out


# ---------------------------------------------------------------------------
# Cross-source dedupe of candidate books
# ---------------------------------------------------------------------------

def first_surname(authors: str) -> str:
    first = (authors or "").split(";")[0]
    return norm_text(first.split(",")[0] if "," in first else (first.split() or [""])[-1])


def merge_sources(candidates: list[dict]) -> list[dict]:
    """One record per book across sources (and across Pressbooks networks that host a copy).

    Books merge when they share a normalized URL, or the same title and first author. Chapters
    follow the surviving book; when two copies of a book both brought chapters, only the
    survivor's chapters are kept.
    """
    books = [c for c in candidates if c["level"] == "book"]
    chapters = [c for c in candidates if c["level"] != "book"]
    books.sort(key=lambda r: (SOURCE_PREFERENCE.get(r["harvest"]["source"], 9), not r.get("license"),
                              -(r["harvest"].get("chapter_count") or 0)))
    groups: list[list[dict]] = []
    index: dict[str, int] = {}
    for b in books:
        keys = [f"u:{u}" for u in url_keys(b)] + [f"t:{norm_text(b['title'])}|{first_surname(b.get('authors', ''))}",
                                                  f"k:{b['harvest']['key']}"]
        if otl := b["harvest"].get("otl_key"):
            keys.append(f"k:{otl}")
        hit = next((index[k] for k in keys if k in index), None)
        if hit is None:
            hit = len(groups)
            groups.append([])
        groups[hit].append(b)
        for k in keys:
            index.setdefault(k, hit)
    alias: dict[str, str] = {}
    chapter_source: dict[str, str] = {}  # surviving book key -> the book key whose chapters to keep
    merged = []
    for g in groups:
        best = dict(g[0])
        best["harvest"] = dict(best["harvest"])
        others = g[1:]
        with_chapters = [b["harvest"]["key"] for b in g if b["harvest"].get("chapter_count")]
        chapter_source[best["harvest"]["key"]] = with_chapters[0] if with_chapters else best["harvest"]["key"]
        for o in others:
            alias[o["harvest"]["key"]] = best["harvest"]["key"]
            for k in PACK_FIELDS:
                if best.get(k) in (None, "", []) and o.get(k) not in (None, "", []):
                    best[k] = o[k]
            best["skins"] = sorted(set(best["skins"]) | set(o["skins"]), key=lambda s: (s not in best["skins"], s))
            best["harvest"].setdefault("also_from", []).append(
                {"source": o["harvest"]["source"], "key": o["harvest"]["key"], "landing_url": o.get("landing_url", ""),
                 "license": o.get("license", "")})
        if others:
            sources = ", ".join(sorted({o["harvest"]["source"] for o in others}))
            best["notes"] = (best.get("notes", "") + f" Also found via {sources}: "
                             + "; ".join(o.get("landing_url", "") for o in others) + ".").strip()
            licensed = [r for r in g if r.get("license")]
            if len({license_family(r["license"]) for r in licensed}) > 1 or (licensed and not g[0].get("license")):
                pick, lic = strictest(licensed)
                if lic != g[0].get("license"):
                    seen = "; ".join(f"{r['license']} ({r['harvest']['source']})" for r in licensed)
                    best["license"] = lic
                    best["license_url"] = (pick or {}).get("license_url", "")
                    best["license_verified"] = bool((pick or {}).get("license_verified"))
                    best["notes"] += (f" Sources disagree or only one names a license: {seen}. Recorded the most restrictive."
                                      if len(licensed) > 1 else f" License taken from {licensed[0]['harvest']['source']}.")
            best["license_family"] = license_family(best["license"])
            best["reuse_tier"] = reuse_tier(best["license_family"])
        merged.append(best)
    seen_keys = set()
    out = []
    for r in merged + chapters:
        h = r["harvest"] = dict(r["harvest"])
        if r["level"] != "book":
            own = h.get("parent_key", "")
            survivor = alias.get(own, own)
            if chapter_source.get(survivor, survivor) != own:
                continue  # another copy of this book supplies the chapters
            h["parent_key"] = survivor
        if h["key"] in seen_keys:
            continue
        seen_keys.add(h["key"])
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------

def book_id(title: str, taken: set[str]) -> str:
    """Slug of the main title when it is specific (3+ words), else of the whole title.

    A subtitle that carries a volume or edition number stays in, so volumes get distinct ids.
    """
    main = main_title(title)
    rest = title[len(main):]
    use_main = len(norm_text(main).split()) >= 3 and not re.search(r"\d|\bvol", rest, re.I)
    return unique(slug(main if use_main else title, 60), taken)


def chapter_id(parent_id: str, title: str, taken: set[str]) -> str:
    prefix = "-".join(parent_id.split("-")[:4])
    stripped = NUMBERING.sub("", title or "") or title
    return unique(f"{prefix}-{slug(stripped, 70)}", taken)


def unique(base: str, taken: set[str]) -> str:
    base = base.strip("-") or "item"
    rid, n = base, 2
    while rid in taken:
        rid = f"{base}-{n}"
        n += 1
    taken.add(rid)
    return rid


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

def build_queue(candidates: list[dict], pack: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Returns (queue records, exact duplicates, stats)."""
    idx = PackIndex(pack)
    taken = set(idx.by_id)
    records = merge_sources(candidates)
    books = {r["harvest"]["key"]: r for r in records if r["level"] == "book"}
    queue, dupes = [], []
    status_of: dict[str, tuple[str, list[dict]]] = {}
    for r in records:
        status_of[r["harvest"]["key"]] = idx.match(r)
    # Ids: books first, so chapters can borrow their book's id as a prefix.
    final_parent: dict[str, str] = {}
    for key, b in books.items():
        status, match = status_of[key]
        if status == "duplicate":
            final_parent[key] = match[0]["id"]
        else:
            b["id"] = book_id(b["title"], taken)
            final_parent[key] = b["id"]
    for r in records:
        key = r["harvest"]["key"]
        status, match = status_of[key]
        r = dict(r)
        r["queue"] = {"status": status, "match": match, "focus": suggested_focus(r.get("skins", []))}
        if r["level"] != "book":
            pkey = r["harvest"].get("parent_key", "")
            pid = final_parent.get(pkey, "")
            r["parent_id"] = pid
            if pkey in books and status_of[pkey][0] == "duplicate":
                r["queue"]["parent_in_pack"] = pid
            if status != "duplicate":
                r["id"] = chapter_id(pid or slug(r.get("parent_title", ""), 40), r["title"], taken)
        if status == "duplicate":
            r["id"] = match[0]["id"]
            dupes.append(r)
        else:
            queue.append(r)
    # Order: by focus, then book title; each book followed by its chapters in harvest order.
    by_parent: dict[str, list[dict]] = defaultdict(list)
    for r in queue:
        if r["level"] != "book":
            by_parent[r["harvest"].get("parent_key", "")].append(r)
    ordered = []
    queue_books = [r for r in queue if r["level"] == "book"]
    focus_rank = {f: i for i, f in enumerate(FOCUS_ORDER)}
    queue_books.sort(key=lambda b: (focus_rank.get(b["queue"]["focus"], 99), b["title"].lower()))
    placed = set()
    for b in queue_books:
        ordered.append(b)
        for ch in by_parent.get(b["harvest"]["key"], []):
            ch["queue"]["focus"] = b["queue"]["focus"]
            ordered.append(ch)
            placed.add(ch["harvest"]["key"])
    # Chapters whose book is already in the pack (exact duplicate) come last, grouped by book.
    for r in queue:
        if r["level"] != "book" and r["harvest"]["key"] not in placed:
            ordered.append(r)
    stats = {
        "candidate_records": len(candidates),
        "after_cross_source_merge": len(records),
        "books": len(books),
        "books_new": sum(1 for k in books if status_of[k][0] == "new"),
        "books_near_duplicate": sum(1 for k in books if status_of[k][0] == "near-duplicate"),
        "books_duplicate": sum(1 for k in books if status_of[k][0] == "duplicate"),
        "chapters": sum(1 for r in records if r["level"] != "book"),
        "chapters_new": sum(1 for r in ordered if r["level"] != "book" and r["queue"]["status"] == "new"),
        "chapters_near_duplicate": sum(1 for r in ordered if r["level"] != "book" and r["queue"]["status"] == "near-duplicate"),
        "chapters_duplicate": sum(1 for r in dupes if r["level"] != "book"),
        "queue_records": len(ordered),
        "license_mix_books": dict(Counter(b["license_family"] for b in books.values()).most_common()),
        "license_mix_queue": dict(Counter(r["license_family"] for r in ordered).most_common()),
    }
    return ordered, dupes, stats


FOCUS_ORDER = ["FYC", "RHETORIC", "ACADEMIC", "TECHCOMM", "PROFWRIT", "BUSINESS", "SCIWRIT", "RESEARCH",
               "SPEECH", "PEDAGOGY", "MULTIMODAL", "L2", "WAW", "CULTURAL_RHET", "CREATIVE_NF", "UNSORTED"]
FOCUS_LABELS = {
    "FYC": "First-Year Composition", "RHETORIC": "Rhetoric", "ACADEMIC": "Academic Writing and WAC",
    "TECHCOMM": "Technical Communication", "PROFWRIT": "Professional Writing", "BUSINESS": "Business Communication",
    "SCIWRIT": "Science Writing", "RESEARCH": "Research and Information Literacy", "SPEECH": "Public Speaking",
    "PEDAGOGY": "For Instructors", "MULTIMODAL": "Digital and Multimodal", "L2": "Multilingual Writers",
    "WAW": "Writing about Writing", "CULTURAL_RHET": "Cultural Rhetorics", "CREATIVE_NF": "Creative Nonfiction",
    "UNSORTED": "No focus guessed",
}


def queue_markdown(queue: list[dict], dupes: list[dict], stats: dict, day: str, queue_name: str) -> str:
    books = [r for r in queue if r["level"] == "book"]
    chapters_by_book: dict[str, list[dict]] = defaultdict(list)
    for r in queue:
        if r["level"] != "book":
            chapters_by_book[r["harvest"].get("parent_key", "")].append(r)
    lines = [f"# Harvest review queue, {day}", ""]
    lines += [
        f"{stats['books']} candidate books after the subject filter and cross-source merge: "
        f"{stats['books_new']} new, {stats['books_near_duplicate']} near duplicates of pack records, "
        f"{stats['books_duplicate']} already in the pack (left out of the queue). "
        f"{stats['chapters']} chapters: {stats['chapters_new']} new, {stats['chapters_near_duplicate']} near duplicates, "
        f"{stats['chapters_duplicate']} already in the pack.",
        "",
        "Book licenses: " + ", ".join(f"{k} {v}" for k, v in stats["license_mix_books"].items()) + ".",
        "",
        "Everything here is marked `needs_review`: skins, topics, levels, and the suggested focus are keyword guesses. "
        "Books marked *license not fetched* come from hosts that refused the harvester; they stay link-only until "
        "someone checks the license on the book's page. To fix a record before approving, edit its line in "
        f"`{queue_name}.jsonl`. To approve, see the end of this file.",
        "",
    ]
    by_focus: dict[str, list[dict]] = defaultdict(list)
    for b in books:
        by_focus[b["queue"]["focus"]].append(b)
    for focus in FOCUS_ORDER:
        if focus not in by_focus:
            continue
        lines += [f"## {FOCUS_LABELS.get(focus, focus)} ({len(by_focus[focus])})", ""]
        for b in by_focus[focus]:
            chs = chapters_by_book.get(b["harvest"]["key"], [])
            new_ch = sum(1 for c in chs if c["queue"]["status"] == "new")
            lic = b["license"] or "license not fetched"
            bits = [f"{lic} ({b['reuse_tier']})"]
            if not b.get("license_verified") and b.get("license"):
                bits.append("license unverified")
            bits.append(f"{len(chs)} chapters" + (f" ({new_ch} new)" if chs and new_ch != len(chs) else ""))
            if b.get("year"):
                bits.append(str(b["year"]))
            also = b["harvest"].get("also_from", [])
            via = "+".join(sorted({b["harvest"]["source"]} | {a["source"] for a in also}))
            bits.append(via + (f", {len(also) + 1} copies" if also else ""))
            lines.append(f"- **{b['title']}** `{b['id']}`  ")
            lines.append(f"  {b.get('authors') or 'authors not listed'}. " + "; ".join(bits) + ".  ")
            lines.append(f"  {b.get('landing_url')}  ")
            if b["queue"]["status"] == "near-duplicate":
                m = "; ".join(f"`{x['id']}` {x['title']}" for x in b["queue"]["match"][:3])
                lines.append(f"  Near duplicate of: {m}  ")
            if b.get("skins"):
                lines.append(f"  Skins: {', '.join(b['skins'])}. Filter: {', '.join(b['harvest'].get('filter', {}).get('reasons', [])[:4])}.")
            lines.append("")
    orphans = [r for r in queue if r["level"] != "book" and r["queue"].get("parent_in_pack")]
    if orphans:
        lines += ["## New chapters of books already in the pack", ""]
        grouped: dict[str, list[dict]] = defaultdict(list)
        for r in orphans:
            grouped[r["queue"]["parent_in_pack"]].append(r)
        for pid, chs in sorted(grouped.items()):
            lines.append(f"- `{pid}` ({chs[0].get('parent_title')}): {len(chs)} chapters, e.g. "
                         + "; ".join(c["title"] for c in chs[:4]))
        lines.append("")
    dup_books = [r for r in dupes if r["level"] == "book"]
    if dup_books:
        lines += [f"## Already in the pack ({len(dup_books)} books, left out of the queue)", ""]
        for r in sorted(dup_books, key=lambda r: r["title"].lower()):
            m = r["queue"]["match"][0]
            lines.append(f"- {r['title']} = `{m['id']}` (matched by {m['how']})")
        lines.append("")
    lines += [
        "## Approving",
        "",
        "From the repository root, with the backend virtualenv's Python:",
        "",
        "```",
        f"python tools/harvest/merge.py --approve all --queue research/harvest/{queue_name}.jsonl --dry-run",
        f"python tools/harvest/merge.py --approve <book-id>,<book-id> --queue research/harvest/{queue_name}.jsonl",
        "cd backend && python manage.py load_pack rhetcomp",
        "```",
        "",
        "`all` takes every record marked new; near duplicates must be named by id. A book id brings its chapters "
        "(add `--book-only` to skip them).",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------

def select_for_approval(queue: list[dict], wanted: str, book_only: bool, pack_ids: set[str]) -> tuple[list[dict], list[str]]:
    msgs: list[str] = []
    pending = [r for r in queue if r.get("queue", {}).get("status") in ("new", "near-duplicate")]
    by_id = {r["id"]: r for r in pending}
    children: dict[str, list[dict]] = defaultdict(list)
    for r in pending:
        if r["level"] != "book":
            children[r.get("parent_id", "")].append(r)
    if wanted.strip().lower() == "all":
        new_ids = {r["id"] for r in pending if r["queue"]["status"] == "new"}
        near = sum(1 for r in pending if r["queue"]["status"] == "near-duplicate")
        if near:
            msgs.append(f"{near} near-duplicate records skipped; name them by id to approve them.")
        # A new chapter of a near-duplicate book waits for its book to be named.
        held = [i for i in new_ids if by_id[i]["level"] != "book" and by_id[i].get("parent_id")
                and by_id[i]["parent_id"] not in pack_ids and by_id[i]["parent_id"] not in new_ids]
        if held:
            msgs.append(f"{len(held)} new chapters held back because their book is a near duplicate; "
                        f"approve the book by id to take them.")
        wanted_ids = [r["id"] for r in pending if r["id"] in new_ids and r["id"] not in held]
    else:
        wanted_ids = [w.strip() for w in wanted.split(",") if w.strip()]
        unknown = [w for w in wanted_ids if w not in by_id]
        if unknown:
            raise SystemExit(f"not pending in this queue: {', '.join(unknown)}")
        if not book_only:
            for w in list(wanted_ids):
                wanted_ids += [c["id"] for c in children.get(w, []) if c["id"] not in wanted_ids]
    for w in list(wanted_ids):
        r = by_id[w]
        pid = r.get("parent_id", "")
        if r["level"] != "book" and pid and pid not in pack_ids and pid not in wanted_ids:
            if pid in by_id:
                wanted_ids.append(pid)
                msgs.append(f"added book {pid} so chapter {w} has its parent")
            else:
                msgs.append(f"chapter {w}: parent {pid} is neither in the pack nor in this queue")
    seen, chosen = set(), []
    for w in wanted_ids:
        if w not in seen:
            seen.add(w)
            chosen.append(by_id[w])
    return chosen, msgs


def pack_record(r: dict) -> dict:
    out = {k: r.get(k, "") for k in PACK_FIELDS}
    out["skins"] = list(r.get("skins") or [])
    out["topics"] = list(r.get("topics") or [])
    out["license_family"] = license_family(out["license"])
    out["reuse_tier"] = reuse_tier(out["license_family"])
    out["license_verified"] = bool(r.get("license_verified")) and bool(out["license"])
    out["needs_review"] = True
    return out


def approve(queue_path: Path, wanted: str, pack_path: Path = PACK_READINGS, book_only: bool = False,
            dry_run: bool = False) -> list[str]:
    """Append approved queue records to the pack. Returns the ids added."""
    queue = read_jsonl(queue_path)
    raw_lines = [l for l in pack_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    existing = {json.loads(l)["id"]: l for l in raw_lines}
    pack_urls = set()
    for l in raw_lines:
        pack_urls |= url_keys(json.loads(l))
    chosen, msgs = select_for_approval(queue, wanted, book_only, set(existing))
    taken = set(existing)
    renamed: dict[str, str] = {}
    added: list[dict] = []
    for r in chosen:
        if r.get("landing_url") and norm_url(r["landing_url"]) in pack_urls and r["level"] in WHOLE_WORK_LEVELS:
            msgs.append(f"skipped {r['id']}: its URL is already in the pack (approved earlier?)")
            continue
        rid = r["id"]
        if rid in taken:
            new = unique(rid, taken)
            renamed[rid] = new
            msgs.append(f"id {rid} is taken in the pack now; using {new}")
            rid = new
        else:
            taken.add(rid)
        rec = pack_record(r)
        rec["id"] = rid
        added.append(rec)
    for rec in added:
        if rec.get("parent_id") in renamed:
            rec["parent_id"] = renamed[rec["parent_id"]]
    for m in msgs:
        print(m)
    if dry_run:
        print(f"dry run: would add {len(added)} records to {pack_path}")
        return [r["id"] for r in added]
    lines = dict(existing)
    for rec in added:
        lines[rec["id"]] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
    pack_path.write_text("".join(lines[k] + "\n" for k in sorted(lines)), encoding="utf-8")
    # Mark the queue lines so a second --approve doesn't add them again.
    added_ids = {rec["id"] for rec in added}
    for q in queue:
        final = renamed.get(q.get("id"), q.get("id"))
        if final in added_ids and q.get("queue", {}).get("status") in ("new", "near-duplicate"):
            q["queue"]["status"] = "approved"
            q["queue"]["approved_id"] = final
    write_jsonl(queue_path, queue)
    print(f"added {len(added)} records to {pack_path.relative_to(ROOT) if pack_path.is_relative_to(ROOT) else pack_path}")
    print("Next: from backend/, run  python manage.py load_pack rhetcomp")
    return [r["id"] for r in added]


# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--approve", help="'all' or comma-separated queue ids to add to the pack")
    ap.add_argument("--queue", type=Path, help="queue file to approve from")
    ap.add_argument("--book-only", action="store_true", help="approve books without their chapters")
    ap.add_argument("--dry-run", action="store_true", help="show what --approve would add; change nothing")
    ap.add_argument("--candidates", type=Path, action="append", help="candidate files (default: research/harvest/*-candidates.jsonl)")
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"), help="queue file date stamp (default today)")
    args = ap.parse_args(argv)

    if args.approve:
        if not args.queue:
            raise SystemExit("--approve needs --queue <file>")
        approve(args.queue, args.approve, book_only=args.book_only, dry_run=args.dry_run)
        return

    files = args.candidates or [HARVEST_DIR / f for f in CANDIDATE_FILES]
    candidates = []
    for f in files:
        rows = read_jsonl(f)
        print(f"{f.name}: {len(rows)} records")
        candidates += rows
    pack = read_jsonl(PACK_READINGS)
    queue, dupes, stats = build_queue(candidates, pack)
    name = f"queue-{args.date}"
    write_jsonl(HARVEST_DIR / f"{name}.jsonl", queue)
    day = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}"
    (HARVEST_DIR / f"{name}.md").write_text(queue_markdown(queue, dupes, stats, day, name), encoding="utf-8")
    for k, v in stats.items():
        print(f"{k}: {v}")
    print(f"wrote research/harvest/{name}.jsonl and .md")


if __name__ == "__main__":
    main()
