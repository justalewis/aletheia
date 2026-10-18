"""Harvest writing books from the Open Textbook Library (https://open.umn.edu/opentextbooks).

    python tools/harvest/otl.py --max-requests 12     # do 12 requests, save state, stop
    python tools/harvest/otl.py --max-requests 12     # run again: picks up where it stopped
    python tools/harvest/otl.py --subjects-only       # just pick the subjects and show them
    python tools/harvest/otl.py --write-only          # rewrite candidates from saved state, no requests

open.umn.edu's robots.txt sets Crawl-delay: 120, so this script makes one request every two
minutes, however it is started. All progress lives in a state file
(research/harvest/otl-state.json, gitignored): the chosen subjects, the pages still to fetch,
every book collected so far, and the time of the last request, so a restarted run waits out the
delay too. Each run ends by rewriting research/harvest/otl-candidates.jsonl from everything
collected.

The API (https://open.umn.edu/opentextbooks/api-docs) has no table of contents, so records
are book level: license, description, and format links (PDF, eBook, online). When a book's
online version sits on a Pressbooks site, the record says so (harvest.pressbooks_url) and
`pressbooks.py --from-otl` can fetch its chapters.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    HARVEST_DIR, TODAY, Blocked, BudgetExhausted, FetchError, PoliteSession, candidate_key, clean_html,
    guess_audience, guess_course_level, join_people, keep_book, lc_rule, license_string, log, make_reading,
    person_name, read_json, short_summary, subject_score, tag_skins, tag_topics, write_json, write_jsonl,
    year_from,
)

API = "https://open.umn.edu/opentextbooks"
STATE = HARVEST_DIR / "otl-state.json"
OUT = HARVEST_DIR / "otl-candidates.jsonl"
SUMMARY = HARVEST_DIR / "otl-summary.json"
PRESSBOOKS_HOSTS = HARVEST_DIR / "pressbooks-hosts.json"

# The Library's subjects are broad (one "Literature, Rhetoric, and Poetry" for all of comp), so
# the plan is: page through the subjects below in full, in this order, then run a keyword
# search inside a few broad subjects where writing books hide. A subject whose LC call number
# falls in a common.LC_RULES range worth 3 points is also paged in full.
FULL_SUBJECTS = [
    r"rhetoric|composition|writing",
    r"communications?\b",
    r"library science|information literacy",
    r"student success",
]
SUBJECT_SKIP = re.compile(r"journalism$", re.I)   # children of a picked subject that aren't worth a page
SCOPED_SEARCHES = [  # (subject name pattern, search query)
    (r"^business$", "communication"),
    (r"^business$", "writing"),
    (r"^engineering & technology$", "writing"),
    (r"^engineering & technology$", "communication"),
    (r"^languages$", "writing"),
    (r"^education$", "writing"),
    (r"^natural sciences$", "writing"),
    (r"^social sciences$", "writing"),
]

# Hosts that run Pressbooks under their own domain. pressbooks-hosts.json (written by
# pressbooks.py from the Directory) extends this list.
KNOWN_PRESSBOOKS_HOSTS = {
    "opentextbc.ca", "open.lib.umn.edu", "pressbooks.bccampus.ca", "pressbooks.library.torontomu.ca",
    "pressbooks.ulib.csuohio.edu", "pressbooks.lib.vt.edu", "opentext.wsu.edu", "uen.pressbooks.pub",
    "openoregon.pressbooks.pub", "oer.pressbooks.pub",
}


def otl_license(name: str) -> tuple[str, str]:
    """OTL lists license names without versions ('Attribution-NonCommercial-ShareAlike')."""
    n = (name or "").strip()
    if not n:
        return "", ""
    if "gnu" in n.lower() or "free documentation" in n.lower():
        return "GFDL (GNU Free Documentation License)", "https://www.gnu.org/licenses/fdl-1.3.html"
    lic, url = license_string(name=n)
    return lic, url


def pressbooks_root(url: str, hosts: set[str]) -> str:
    """The Pressbooks book root for an online-version URL, or '' if the host isn't Pressbooks."""
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    if not host or not (host in hosts or "pressbooks" in host):
        return ""
    segs = [s for s in parts.path.split("/") if s]
    if not segs or segs[0] in ("chapter", "part", "front-matter", "back-matter", "wp-json"):
        return f"{parts.scheme or 'https'}://{parts.netloc}/"
    return f"{parts.scheme or 'https'}://{parts.netloc}/{segs[0]}/"


def contributor_names(contribs: list[dict]) -> str:
    def name(c):
        if c.get("corporate"):
            return clean_html(" ".join(x for x in (c.get("first_name"), c.get("middle_name"), c.get("last_name")) if x))
        first = " ".join(x for x in (c.get("first_name"), c.get("middle_name")) if x)
        return person_name(first, c.get("last_name", ""), "")
    authors = [c for c in contribs if (c.get("contribution") or "").lower().startswith("author")]
    editors = [c for c in contribs if (c.get("contribution") or "").lower().startswith("editor")]
    return join_people(name(c) for c in (authors or editors or contribs))


def book_record(b: dict, pb_hosts: set[str]) -> tuple[dict | None, int, list[str]]:
    title = clean_html(b.get("title", ""))
    desc = clean_html(b.get("description", ""))
    subjects = b.get("subjects", []) or []
    names = [s.get("name", "") for s in subjects]
    # The Library's subjects are whole fields ("Literature, Rhetoric, and Poetry", call number
    # PE1408, holds every literature anthology too), so they don't count toward the score.
    score, reasons = subject_score(title, desc, language=b.get("language", ""))
    if not keep_book(score):
        return None, score, reasons
    formats = [{"format": f.get("format", ""), "url": f.get("url", "")} for f in b.get("formats", []) or [] if f.get("url")]

    def first(kind_pat: str) -> str:
        return next((f["url"] for f in formats if re.search(kind_pat, f["format"], re.I)), "")

    online, pdf = first(r"^online"), first(r"^pdf")
    ebook = first(r"ebook|epub")
    epub = ebook if ebook.lower().split("?")[0].endswith(".epub") or "type=epub" in ebook.lower() else ""
    pb_url = next((r for f in formats if (r := pressbooks_root(f["url"], pb_hosts))), "")
    lic, lic_url = otl_license(b.get("license", ""))
    publisher = clean_html(((b.get("publishers") or [{}])[0] or {}).get("name", ""))
    context = " ".join([title, desc, " ".join(names)])
    skins = tag_skins(title, desc)  # OTL subject names are too broad to tag with
    edition = clean_html(b.get("edition_statement", ""))
    no_version = " (no version given)" if lic.startswith("CC") and not re.search(r"\d", lic) else ""
    notes = (f"Harvested from the Open Textbook Library API on {TODAY}. License as the Library lists it{no_version}; "
             f"confirm it on the book itself before embedding.")
    if edition:
        notes += f" Edition: {edition}."
    if b.get("rating") and str(b.get("textbook_reviews_count") or "0") != "0":
        notes += f" Library rating {b['rating']} from {b['textbook_reviews_count']} reviews."
    if pb_url:
        notes += f" Online version is on a Pressbooks site ({pb_url})."
    rec = make_reading(
        level="book", title=title, source="otl", key=f"otl:{b.get('id')}",
        license=lic, license_url=lic_url, license_verified=False,
        authors=contributor_names(b.get("contributors", []) or []), year=year_from(b.get("copyright_year")),
        landing_url=b.get("url", "") or f"{API}/textbooks/{b.get('id')}", html_url=online, pdf_url=pdf, epub_url=epub,
        publisher_platform=f"{publisher} (Open Textbook Library)" if publisher else "Open Textbook Library",
        summary=short_summary(desc), skins=skins, topics=tag_topics(title, desc),
        course_level=guess_course_level(context, skins), audience=guess_audience(context, skins), notes=notes,
        harvest={"otl_id": b.get("id"), "subjects": [f"{s.get('name')} ({s.get('call_number')})" for s in subjects],
                 "formats": formats, "pressbooks_url": pb_url, "filter": {"score": score, "reasons": reasons},
                 "isbn13": b.get("isbn13", "")},
    )
    return rec, score, reasons


# ---------------------------------------------------------------------------

def walk_subjects(subjects: list[dict], parent: str = ""):
    for s in subjects:
        yield s, parent
        yield from walk_subjects(s.get("child_subjects") or [], s.get("name", ""))


def plan_queries(subjects_flat: list[dict]) -> list[dict]:
    """The ordered list of subject queries to page through (see FULL_SUBJECTS, SCOPED_SEARCHES)."""
    plan = []
    for prio, pat in enumerate(FULL_SUBJECTS):
        for s in subjects_flat:
            if re.search(pat, s["name"] or "", re.I) and not SUBJECT_SKIP.search(s["name"] or ""):
                plan.append({"subject": s["id"], "name": s["name"], "q": "", "count": s.get("textbooks_count")})
    for s in subjects_flat:
        lc = lc_rule(s.get("call_number") or "")
        if lc and lc[0] >= 3 and not any(p["subject"] == s["id"] for p in plan):
            plan.append({"subject": s["id"], "name": s["name"], "q": "", "count": s.get("textbooks_count")})
    for pat, q in SCOPED_SEARCHES:
        for s in subjects_flat:
            if re.search(pat, s["name"] or "", re.I):
                plan.append({"subject": s["id"], "name": s["name"], "q": q, "count": None})
    seen, out = set(), []
    for p in plan:  # a subject matched by two patterns is paged once
        k = (p["subject"], p["q"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def query_key(item: dict) -> str:
    return f"{item['subject']}:{item.get('q', '')}:{item['page']}"


def trim(book: dict) -> dict:
    return {k: v for k, v in book.items() if k not in ("reviews",)}


def run(state: dict, session: PoliteSession, subjects_only: bool, replan: bool) -> None:
    if not state.get("subjects_all"):
        data = session.get_json(f"{API}/subjects.json")
        state["subjects_all"] = [{"id": s["id"], "name": s.get("name"), "call_number": s.get("call_number"),
                                  "textbooks_count": s.get("textbooks_count"), "parent": p}
                                 for s, p in walk_subjects((data or {}).get("data", []))]
        replan = True
    if replan or "queue" not in state:
        state["plan"] = plan_queries(state["subjects_all"])
        done = set(state.get("done", []))
        state["queue"] = [{"subject": p["subject"], "q": p["q"], "page": 1} for p in state["plan"]
                          if query_key({**p, "page": 1}) not in done]
        save(state, session)
        log("plan: " + "; ".join(f"{p['name']}" + (f" q={p['q']!r}" if p["q"] else f" ({p['count']})") for p in state["plan"]))
    if subjects_only:
        return
    while state["queue"]:
        item = state["queue"][0]
        params = {"page": item["page"]}
        if item.get("q"):
            params["q"] = item["q"]
        data = session.get_json(f"{API}/subjects/{item['subject']}.json?" + urlencode(params))
        state["queue"].pop(0)
        state.setdefault("done", []).append(query_key(item))
        if data:
            for b in data.get("data", []):
                state.setdefault("books", {})[str(b["id"])] = trim(b)
            total = (data.get("links") or {}).get("total_pages") or 0
            if item["page"] < total:
                state["queue"].insert(0, {**item, "page": item["page"] + 1})
            log(f"subject {item['subject']}{' q=' + item['q'] if item.get('q') else ''} page {item['page']}/{total}: "
                f"{len(data.get('data', []))} books ({len(state['books'])} collected, {len(state['queue'])} queries queued)")
        save(state, session)


def save(state: dict, session: PoliteSession) -> None:
    state["session"] = session.state
    state["requests_total"] = state.get("requests_total_before", 0) + session.requests_made
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_json(STATE, state)


def write_candidates(state: dict) -> dict:
    pb_hosts = set(KNOWN_PRESSBOOKS_HOSTS) | set((read_json(PRESSBOOKS_HOSTS, {}) or {}).keys())
    kept, dropped = [], []
    for b in state.get("books", {}).values():
        rec, score, reasons = book_record(b, pb_hosts)
        if rec:
            kept.append(rec)
        else:
            dropped.append({"title": b.get("title"), "score": score, "reasons": reasons})
    kept.sort(key=lambda r: r["title"].lower())
    write_jsonl(OUT, kept)
    from collections import Counter
    summary = {
        "run": TODAY,
        "requests_total": state.get("requests_total", 0),
        "plan": [f"{p['name']}" + (f" q={p['q']}" if p["q"] else f" ({p['count']})") for p in state.get("plan", [])],
        "pages_done": len(state.get("done", [])),
        "pages_queued": len(state.get("queue", [])),
        "books_collected": len(state.get("books", {})),
        "books_kept": len(kept),
        "books_dropped_by_filter": len(dropped),
        "on_pressbooks": sum(1 for r in kept if r["harvest"]["pressbooks_url"]),
        "license_mix": dict(Counter(r["license_family"] for r in kept).most_common()),
        "dropped_examples": sorted(dropped, key=lambda d: -d["score"])[:25],
    }
    write_json(SUMMARY, summary)
    return summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-requests", type=int, default=12, help="stop after this many requests (default 12)")
    ap.add_argument("--subjects-only", action="store_true", help="fetch subjects and plan the queries, then stop")
    ap.add_argument("--replan", action="store_true",
                    help="rebuild the query plan from the saved subject list (after editing FULL_SUBJECTS or "
                         "SCOPED_SEARCHES); finished queries stay done")
    ap.add_argument("--write-only", action="store_true", help="rewrite candidates from the state file; no requests")
    ap.add_argument("--reset", action="store_true", help="start over (keeps the last-request time and robots cache)")
    args = ap.parse_args(argv)

    state = read_json(STATE, {}) or {}
    if args.reset:
        state = {"session": state.get("session", {})}
    if not args.write_only:
        state["requests_total_before"] = state.get("requests_total", 0)
        session = PoliteSession(max_requests=args.max_requests, state=state.setdefault("session", {}))
        try:
            run(state, session, args.subjects_only, args.replan)
            if not state.get("queue") and not args.subjects_only:
                log("every queued page is done")
        except BudgetExhausted as e:
            log(f"{e}; state saved, run again to continue")
        except (Blocked, FetchError) as e:
            log(f"stopped: {e}")
        except KeyboardInterrupt:
            log("interrupted; state saved")
        finally:
            save(state, session)
    summary = write_candidates(state)
    print(json.dumps({k: v for k, v in summary.items() if k != "dropped_examples"}, indent=1))


if __name__ == "__main__":
    main()
