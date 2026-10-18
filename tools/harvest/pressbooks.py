"""Harvest openly licensed writing books from Pressbooks.

    python tools/harvest/pressbooks.py                      # Directory feed, then book APIs
    python tools/harvest/pressbooks.py --max-books 300      # cap the books whose APIs we call
    python tools/harvest/pressbooks.py --network https://opentextbc.ca
    python tools/harvest/pressbooks.py --from-otl           # books otl.py found on Pressbooks

Discovery reads the Pressbooks Directory OPDS feed (https://pressbooks.directory/opds), page
by page, and optionally each --network's /wp-json/pressbooks/v2/books list. Every book is
screened against the subject rules in common.py. For books that pass, the script calls
/wp-json/pressbooks/v2/metadata and /toc on the book's own site, applies the full filter
(with Thema/BISAC codes and the full description), and emits one book record plus one record
per chapter. Front matter, back matter, and part pages are left out unless substantive.

Hosts that answer with a bot check or a 403 (pressbooks.pub sits behind a CDN that does) are
skipped, never retried. A book on a skipped host that clearly passes the filter still gets a
book-level record from the Directory data, with no license (so it stays link-only) and a note.

API responses are cached under research/harvest/raw/pressbooks/ (gitignored), so a rerun
picks up where the last one stopped. Output: research/harvest/pressbooks-candidates.jsonl and
research/harvest/pressbooks-summary.json.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    HARVEST_DIR, RAW_DIR, SKINS, TODAY, Blocked, BudgetExhausted, FetchError, PoliteSession, candidate_key,
    classify_subject_code, clean_html, guess_audience, guess_course_level, join_people, keep_book, log,
    license_string, make_reading, person_name, read_json, read_jsonl, short_summary, subject_score,
    tag_skins, tag_topics, write_json, write_jsonl, year_from,
)
from build_catalog import tighten_parents  # noqa: E402

OPDS_URL = "https://pressbooks.directory/opds"
CACHE = RAW_DIR / "pressbooks"
OUT = HARVEST_DIR / "pressbooks-candidates.jsonl"
SUMMARY = HARVEST_DIR / "pressbooks-summary.json"
HOSTS = HARVEST_DIR / "pressbooks-hosts.json"
OTL_CANDIDATES = HARVEST_DIR / "otl-candidates.jsonl"
NETWORK_PAGE = 10                # the books endpoint allows per_page 1-10

# What counts as a chapter worth listing.
MIN_CHAPTER_WORDS = 100          # shorter "chapters" are usually headings or placeholders
SUBSTANTIVE_WORDS = 1000         # front/back matter and part pages need this many words...
BOILERPLATE_TITLE = re.compile(  # ...and a title that isn't one of these
    r"^\s*(about (this|the) (book|text|textbook|toolkit|guide|resource|project|authors?|editors?|contributors?|publisher)|"
    r"about the (authors?|editors?|contributors?)|acknowledg\w*|accessibility\w*|copyright|dedication|land acknowledg\w*|"
    r"licen[cs]\w*|attributions?|media attributions|image credits|versioning|version history|revision history|"
    r"review statement|feedback|errata|glossary|index|bibliography|references|works cited|further reading|"
    r"how to use this|using this (book|text)|cover|title page|table of contents|contents|list of (figures|tables|links)|"
    r"contributors|authors?|editors?|adoption\w*|print version|downloads?|instructor resources|ancillar\w*|"
    r"(video |audio )?transcripts?|misc(ellaneous)?)\b",
    re.I)
APPENDIX_PREFIX = re.compile(r"^\s*appendix\s+[\w.]+\s*[:.\-—]\s*", re.I)


# Some Directory descriptions open with the book's license; it is kept as a hint in the notes,
# never used as the record's license (the record's license comes only from the book's own API).
LICENSE_HINT = re.compile(r"\blicen[cs]e:\s*((?:CC|Creative Commons)[^.<]{0,70}?(?:\)|(?=\s{2}|$|\.))|"
                          r"All rights reserved|Public domain)", re.I)


def book_api(book_url: str, endpoint: str) -> str:
    return book_url.rstrip("/") + f"/wp-json/pressbooks/v2/{endpoint}"


def cache_path(book_url: str, name: str) -> Path:
    parts = urlsplit(book_url)
    key = re.sub(r"[^a-z0-9]+", "_", (parts.netloc + parts.path).lower()).strip("_")[:120]
    return CACHE / "books" / key / f"{name}.json"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def opds_entries(session: PoliteSession, max_pages: int | None, refresh: bool) -> list[dict]:
    """Every publication in the Directory feed, as a flat dict."""
    entries: list[dict] = []
    page, total_pages = 1, None
    while total_pages is None or page <= total_pages:
        if max_pages and page > max_pages:
            break
        path = CACHE / "opds" / f"page-{page:04d}.json"
        data = None if refresh else read_json(path)
        if data is None:
            data = session.get_json(f"{OPDS_URL}?page={page}")
            if data is None:
                break
            write_json(path, data)
        meta = data.get("metadata", {})
        if total_pages is None:
            per = meta.get("itemsPerPage") or 20
            total_pages = -(-int(meta.get("numberOfItems") or 0) // int(per)) or 1
            log(f"Directory feed: {meta.get('numberOfItems')} books on {total_pages} pages")
        for pub in data.get("publications", []):
            entries.append(opds_entry(pub))
        if page % 20 == 0:
            log(f"feed page {page}/{total_pages}")
        if not any(l.get("rel") == "next" for l in data.get("links", [])):
            break
        page += 1
    return entries


def opds_entry(pub: dict) -> dict:
    m = pub.get("metadata", {})
    links = {l.get("type", ""): l.get("href", "") for l in pub.get("links", [])}
    self_link = next((l["href"] for l in pub.get("links", []) if l.get("rel") == "self"), "")
    return {
        "via": "directory",
        "url": self_link,
        "title": clean_html(m.get("title", "")),
        "authors": split_author_string(m.get("author", "")),
        "publisher": clean_html(m.get("publisher", "")),
        "language": m.get("language", ""),
        "description": clean_html(m.get("description", "")),
        "subject_names": [s.get("name", "") for s in m.get("subject", []) if s.get("name")],
        "subject_codes": [],
        "modified": m.get("modified", ""),
        "pdf_url": links.get("application/pdf", ""),
        "epub_url": links.get("application/epub+zip", ""),
    }


def split_author_string(s: str) -> list[str]:
    s = clean_html(s)
    if not s:
        return []
    parts = [p.strip() for p in re.split(r";\s*|,\s*(?:and\s+)?|\s+and\s+|\s*&\s*", s) if p.strip()]
    # "Smith, Jane" (one person, inverted) splits into two one-word pieces; keep it whole.
    if len(parts) == 2 and all(len(p.split()) == 1 for p in parts):
        return [s]
    return [person_name(full=p) for p in parts]


def network_entries(session: PoliteSession, network: str, refresh: bool) -> list[dict]:
    """Books listed by one Pressbooks network's /wp-json/pressbooks/v2/books endpoint."""
    entries, page = [], 1
    host = urlsplit(network).netloc
    while True:
        path = CACHE / "networks" / re.sub(r"[^a-z0-9]+", "_", host.lower()) / f"page-{page:04d}.json"
        data = None if refresh else read_json(path)
        if data is None:
            data = session.get_json(network.rstrip("/") + "/wp-json/pressbooks/v2/books?" +
                                    urlencode({"per_page": NETWORK_PAGE, "page": page}))
            if not data:
                break
            write_json(path, data)
        for b in data:
            md = b.get("metadata", {})
            entries.append({
                "via": f"network {host}",
                "url": b.get("link", ""),
                "title": clean_html(md.get("name", "")),
                "authors": [person_name(a.get("contributor_first_name", ""), a.get("contributor_last_name", ""), a.get("name", ""))
                            for a in md.get("author", []) if isinstance(a, dict)],
                "publisher": clean_html((md.get("publisher") or {}).get("name", "")),
                "language": md.get("inLanguage", ""),
                "description": clean_html(md.get("description") or md.get("disambiguatingDescription", "")),
                "subject_names": about_names(md),
                "subject_codes": about_codes(md),
                "modified": md.get("dateModified", ""),
                "pdf_url": "", "epub_url": "",
            })
        if len(data) < NETWORK_PAGE:
            break
        page += 1
    log(f"network {host}: {len(entries)} books")
    return entries


def otl_entries() -> list[dict]:
    """Books otl.py found whose online version sits on a Pressbooks site."""
    out = []
    for r in read_jsonl(OTL_CANDIDATES):
        url = (r.get("harvest") or {}).get("pressbooks_url")
        if url and r.get("level") == "book":
            out.append({"via": "otl", "url": url, "title": r.get("title", ""), "authors": [], "publisher": "",
                        "language": "en", "description": r.get("summary", ""), "subject_names": [],
                        "subject_codes": [], "modified": "", "pdf_url": "", "epub_url": "",
                        "otl_key": r["harvest"]["key"]})
    return out


# ---------------------------------------------------------------------------
# Book and chapter records
# ---------------------------------------------------------------------------

def about_names(meta: dict) -> list[str]:
    return [clean_html(a.get("name")) for a in meta.get("about") or [] if isinstance(a, dict) and a.get("name")]


def about_codes(meta: dict) -> list[str]:
    return list(dict.fromkeys(c for a in meta.get("about") or [] if isinstance(a, dict)
                              if (c := classify_subject_code(a.get("identifier")))))


def people(items) -> str:
    return join_people(person_name(a.get("contributor_first_name", ""), a.get("contributor_last_name", ""), a.get("name", ""))
                       for a in items or [] if isinstance(a, dict))


def lic_of(meta: dict) -> tuple[str, str]:
    lic = meta.get("license") or {}
    if isinstance(lic, str):
        return license_string(url=lic)
    return license_string(lic.get("url", ""), lic.get("name", ""), lic.get("code", ""))


def platform(publisher: str, host: str) -> str:
    return f"{publisher} (Pressbooks, {host})" if publisher else f"Pressbooks ({host})"


def substantive(entry: dict, kind: str) -> bool:
    words = int(entry.get("word_count") or 0)
    if kind == "chapter":
        return bool(entry.get("has_post_content", True)) and words >= MIN_CHAPTER_WORDS
    title = APPENDIX_PREFIX.sub("", clean_html(entry.get("title", "")))
    return words >= SUBSTANTIVE_WORDS and not BOILERPLATE_TITLE.search(title)


def build_records(entry: dict, meta: dict, toc: dict | None, score: int, reasons: list[str]) -> list[dict]:
    book_url = entry["url"]
    host = urlsplit(book_url).netloc
    title = clean_html(meta.get("name") or entry["title"])
    description = clean_html(meta.get("description") or meta.get("disambiguatingDescription") or entry["description"])
    subject_names = about_names(meta) or entry["subject_names"]
    authors = people(meta.get("author")) or people(meta.get("editor")) or join_people(entry["authors"])
    lic, lic_url = lic_of(meta)
    publisher = clean_html((meta.get("publisher") or {}).get("name", "")) or entry["publisher"]
    context = " ".join([title, description, " ".join(subject_names)])
    skins = tag_skins(title, description, subject_names)
    year = year_from(meta.get("datePublished"), meta.get("copyrightYear"))
    book_key = candidate_key("pb", book_url)
    words = meta.get("wordCount")
    book = make_reading(
        level="book", title=title, source="pressbooks", key=book_key,
        license=lic, license_url=lic_url, license_verified=bool(lic),
        authors=authors, year=year, landing_url=book_url, html_url=book_url,
        pdf_url=entry.get("pdf_url", ""), epub_url=entry.get("epub_url", ""),
        publisher_platform=platform(publisher, host), summary=short_summary(description),
        pages=f"{words} words" if words else "", skins=skins, topics=tag_topics(title, description),
        course_level=guess_course_level(context, skins), audience=guess_audience(context, skins),
        notes=f"Harvested from the Pressbooks API on {TODAY}; license from the book's Pressbooks metadata."
              if lic else f"Harvested from the Pressbooks API on {TODAY}; the book's metadata names no license.",
        harvest={"via": entry["via"], "host": host, "subjects": subject_names, "subject_codes": about_codes(meta),
                 "filter": {"score": score, "reasons": reasons}, "otl_key": entry.get("otl_key", "")},
    )
    records = [book]
    if not toc:
        return records

    def chapter(e: dict, kind: str, part_title: str = "") -> dict | None:
        if e.get("status", "publish") != "publish" or not substantive(e, kind):
            return None
        cm = e.get("metadata") or {}
        c_lic, c_lic_url = lic_of(cm)
        c_title = clean_html(e.get("title") or cm.get("name", ""))
        note = {"front-matter": "Front matter. ", "back-matter": "Back matter. ", "part": "Part introduction. "}.get(kind, "")
        if part_title:
            note += f"In part: {part_title}. "
        note += ("License from the chapter's Pressbooks metadata." if c_lic else
                 "No chapter license in the metadata; the book's license applies.")
        return make_reading(
            level="chapter", title=c_title, source="pressbooks", key=candidate_key("pb", e.get("link", "")),
            parent_key=book_key, license=c_lic or lic, license_url=c_lic_url if c_lic else lic_url,
            license_verified=bool(c_lic or lic),
            authors=people(cm.get("author")) or authors, year=year_from(cm.get("datePublished")) or year,
            landing_url=e.get("link", ""), html_url=e.get("link", ""), parent_title=title,
            publisher_platform=book["publisher_platform"], pages=f"{e.get('word_count')} words" if e.get("word_count") else "",
            skins=sorted(set(skins) | set(tag_skins(c_title)), key=SKINS.index), topics=tag_topics(c_title),
            course_level=book["course_level"],
            audience=book["audience"], notes=note.strip(),
            harvest={"via": entry["via"], "host": host, "toc_kind": kind, "word_count": e.get("word_count")},
        )

    for e in toc.get("front-matter", []):
        if (rec := chapter(e, "front-matter")):
            records.append(rec)
    for part in toc.get("parts", []):
        p_title = clean_html(part.get("title", ""))
        if part.get("has_post_content") and (rec := chapter(part, "part")):
            records.append(rec)
        for e in part.get("chapters", []):
            if (rec := chapter(e, "chapter", p_title if len(toc.get("parts", [])) > 1 else "")):
                records.append(rec)
    for e in toc.get("back-matter", []):
        if (rec := chapter(e, "back-matter")):
            records.append(rec)
    tighten_book(records)
    book["harvest"]["chapter_count"] = len(records) - 1
    return records


def tighten_book(records: list[dict]) -> None:
    """A book can't be reused more freely than its strictest chapter (build_catalog's rule)."""
    if len(records) < 2:
        return
    book_key = records[0]["harvest"]["key"]
    for r in records:
        r["id"] = r["harvest"]["key"]
        r["parent_id"] = book_key if r is not records[0] else ""
    tighten_parents(records)
    for r in records:
        r["id"] = ""
        r["parent_id"] = ""


def directory_only_record(entry: dict, score: int, reasons: list[str], blocked: str) -> dict:
    """Book record from Directory data alone, for a book whose host refused the harvester."""
    host = urlsplit(entry["url"]).netloc
    desc = entry["description"]
    hint = ""
    if m := LICENSE_HINT.search(desc):
        hint = m.group(1).strip()
        desc = (desc[:m.start()] + " " + desc[m.end():]).strip()
    context = " ".join([entry["title"], desc, " ".join(entry["subject_names"])])
    skins = tag_skins(entry["title"], desc, entry["subject_names"])
    notes = (f"Found in the Pressbooks Directory on {TODAY}. The book's site refused the harvester ({blocked}), "
             f"so the license and chapters were not fetched; the record stays link-only until someone checks the "
             f"license on the book's page.")
    if hint:
        notes += f" The Directory description mentions a license: {hint}."
    return make_reading(
        level="book", title=entry["title"], source="pressbooks", key=candidate_key("pb", entry["url"]),
        authors=join_people(entry["authors"]), landing_url=entry["url"], html_url=entry["url"],
        pdf_url=entry.get("pdf_url", ""), epub_url=entry.get("epub_url", ""),
        publisher_platform=platform(entry["publisher"], host), summary=short_summary(desc),
        skins=skins, topics=tag_topics(entry["title"], desc), course_level=guess_course_level(context, skins),
        audience=guess_audience(context, skins), notes=notes,
        harvest={"via": entry["via"], "host": host, "subjects": entry["subject_names"], "blocked": blocked,
                 "filter": {"score": score, "reasons": reasons}, "otl_key": entry.get("otl_key", "")},
    )


# ---------------------------------------------------------------------------

def fetch_book(session: PoliteSession, url: str, refresh: bool) -> tuple[dict | None, dict | None]:
    out = []
    for name in ("metadata", "toc"):
        path = cache_path(url, name)
        data = None if refresh else read_json(path)
        if data is None:
            data = session.get_json(book_api(url, name))
            if data is None:
                return (out[0] if out else None), None
            write_json(path, data)
        out.append(data)
    return out[0], out[1]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-pages", type=int, help="stop the Directory feed after this many pages")
    ap.add_argument("--max-books", type=int, default=300, help="call the APIs of at most this many books (default 300)")
    ap.add_argument("--network", action="append", default=[], help="also list books from this Pressbooks network URL")
    ap.add_argument("--from-otl", action="store_true", help="also fetch books that otl.py found on Pressbooks sites")
    ap.add_argument("--no-directory", action="store_true", help="skip the Directory feed")
    ap.add_argument("--refresh", action="store_true", help="ignore cached responses")
    ap.add_argument("--retry-blocked", action="store_true", help="forget hosts that refused us last time")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between requests to one host (default 2)")
    args = ap.parse_args(argv)

    state_path = CACHE / "session-state.json"
    state = read_json(state_path, {}) or {}
    if args.retry_blocked:
        state["blocked"] = {}
    session = PoliteSession(delay=args.delay, state=state)

    entries: list[dict] = []
    try:
        if not args.no_directory:
            entries += opds_entries(session, args.max_pages, args.refresh)
        for net in args.network:
            entries += network_entries(session, net, args.refresh)
    except (Blocked, FetchError) as e:
        log(f"discovery stopped: {e}")
    if args.from_otl:
        entries += otl_entries()

    hosts = Counter(urlsplit(e["url"]).netloc.lower() for e in entries if e.get("url"))
    write_json(HOSTS, dict(sorted(hosts.items())))

    # Pass 1: screen on what discovery gave us. Books from OTL already passed otl.py's filter.
    seen: dict[str, dict] = {}
    screened = []
    for e in entries:
        k = candidate_key("pb", e["url"])
        if not e["url"]:
            continue
        if k in seen:
            if e.get("otl_key"):  # the Directory already listed this OTL book; remember the link
                seen[k]["otl_key"] = e["otl_key"]
            continue
        seen[k] = e

    def from_otl(e: dict) -> bool:  # otl.py's filter already kept it
        return e["via"] == "otl" or bool(e.get("otl_key"))

    for e in seen.values():
        score, reasons = subject_score(e["title"], e["description"], e["subject_names"], e["subject_codes"], e["language"])
        if from_otl(e) or keep_book(score, prescreen=True):
            screened.append((score, reasons, e))
    screened.sort(key=lambda t: (not from_otl(t[2]), -t[0], t[2]["title"].lower()))
    log(f"{len(seen)} distinct books discovered; {len(screened)} pass the first screen")

    records: list[dict] = []
    stats = Counter()
    blocked_books: list[dict] = []
    fetched = 0
    try:
        for score, reasons, e in screened:
            if fetched >= args.max_books:
                stats["not fetched (over --max-books)"] += 1
                continue
            host = urlsplit(e["url"]).netloc.lower()
            if reason := session.is_blocked(host):
                stats["host blocked"] += 1
                if keep_book(score) or from_otl(e):
                    blocked_books.append(directory_only_record(e, score, reasons, reason))
                continue
            fetched += 1
            try:
                meta, toc = fetch_book(session, e["url"], args.refresh)
            except Blocked as err:
                log(str(err))
                stats["host blocked"] += 1
                if keep_book(score) or from_otl(e):
                    blocked_books.append(directory_only_record(e, score, reasons, session.is_blocked(host) or str(err)))
                continue
            except FetchError as err:
                log(f"skipped {e['url']}: {err}")
                stats["fetch error"] += 1
                continue
            if meta is None:
                stats["no Pressbooks API (404)"] += 1
                continue
            desc = clean_html(meta.get("description") or meta.get("disambiguatingDescription") or e["description"])
            score2, reasons2 = subject_score(clean_html(meta.get("name") or e["title"]), desc,
                                             about_names(meta) or e["subject_names"], about_codes(meta),
                                             meta.get("inLanguage") or e["language"])
            if not (keep_book(score2) or (from_otl(e) and score2 > -99)):
                stats["dropped by full filter"] += 1
                continue
            try:
                recs = build_records(e, meta, toc, score2, reasons2)
            except Exception as err:  # one odd book must not end an hour-long run
                log(f"could not map {e['url']}: {type(err).__name__}: {err}")
                stats["mapping error"] += 1
                continue
            stats["books kept"] += 1
            stats["chapters"] += len(recs) - 1
            records += recs
            if fetched % 25 == 0:
                log(f"{fetched} books fetched, {stats['books kept']} kept, {stats['chapters']} chapters")
                write_json(state_path, session.state)
    except BudgetExhausted as e:
        log(str(e))
    except KeyboardInterrupt:
        log("interrupted; writing what we have")
    finally:
        write_json(state_path, session.state)

    records += blocked_books
    write_jsonl(OUT, records)
    books = [r for r in records if r["level"] == "book"]
    summary = {
        "run": TODAY,
        "directory_books": sum(1 for e in entries if e["via"] == "directory"),
        "network_books": sum(1 for e in entries if e["via"].startswith("network")),
        "otl_books": sum(1 for e in entries if e["via"] == "otl"),
        "distinct_books": len(seen),
        "passed_first_screen": len(screened),
        "api_fetches_attempted": fetched,
        "books_kept_with_metadata": stats["books kept"],
        "books_kept_directory_only (host blocked)": len(blocked_books),
        "chapters": stats["chapters"],
        "stats": dict(stats),
        "license_mix_books": dict(Counter(r["license_family"] for r in books).most_common()),
        "blocked_hosts": dict(sorted(session.state["blocked"].items())),
        "requests_made": session.requests_made,
    }
    write_json(SUMMARY, summary)
    log(f"wrote {len(records)} records ({len(books)} books) to {OUT.relative_to(HARVEST_DIR.parent.parent)}")
    for k, v in summary.items():
        if k not in ("blocked_hosts", "stats"):
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
