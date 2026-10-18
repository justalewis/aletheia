"""Tests for the pure parts of the harvesters: mapping, filter, dedupe, ids, polite HTTP.

Run from the repository root with the backend virtualenv's Python:

    python -m pytest tools/harvest -q

No test touches the network.
"""
from __future__ import annotations

import json

import pytest
import requests
from requests.structures import CaseInsensitiveDict

import common
import merge
import otl
import pressbooks
from common import (
    Blocked, Disallowed, PoliteSession, clean_html, classify_subject_code, keep_book, lc_rule, license_string,
    make_reading, person_name, subject_score, tag_skins, tag_topics,
)


# ---------------------------------------------------------------------------
# License mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,name,code,expected", [
    ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "CC BY-NC-SA (Attribution NonCommercial ShareAlike)", "",
     ("CC BY-NC-SA 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/")),
    ("http://creativecommons.org/licenses/by/3.0/us/", "", "", ("CC BY 3.0 US", "https://creativecommons.org/licenses/by/3.0/us/")),
    ("https://creativecommons.org/publicdomain/zero/1.0/", "", "", ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")),
    ("", "All Rights Reserved", "all-rights-reserved", ("All rights reserved", "")),
    ("", "Attribution-NonCommercial-ShareAlike", "", ("CC BY-NC-SA", "")),
    ("", "Attribution-NoDerivs", "", ("CC BY-ND", "")),
    ("", "Attribution", "", ("CC BY", "")),
    ("", "No Rights Reserved", "", ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")),
    ("", "CC BY 4.0", "", ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/")),
    ("", "", "", ("", "")),
])
def test_license_string(url, name, code, expected):
    assert license_string(url, name, code) == expected


def test_license_string_feeds_the_catalog_tiers():
    lic, _ = license_string("https://creativecommons.org/licenses/by-nd/4.0/")
    rec = make_reading(level="book", title="T", source="pressbooks", key="pb:x", license=lic, license_verified=True)
    assert (rec["license_family"], rec["reuse_tier"]) == ("CC BY-ND", "verbatim")
    unknown = make_reading(level="book", title="T", source="pressbooks", key="pb:y", license="", license_verified=True)
    assert unknown["reuse_tier"] == "link-only" and unknown["license_verified"] is False


def test_otl_license_names():
    assert otl.otl_license("Attribution-NonCommercial-NoDerivs") == ("CC BY-NC-ND", "")
    assert otl.otl_license("Free Documentation License (GNU)")[0].startswith("GFDL")
    assert common.license_family(otl.otl_license("Free Documentation License (GNU)")[0]) == "Other open"


# ---------------------------------------------------------------------------
# Subject filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,desc,names,codes,keep", [
    ("Technical Writing Essentials", "", [], [], True),
    ("Exploring Public Speaking", "", [], [], True),
    ("Rhetoric Matters", "", [], [], True),
    ("Information Literacy for the Digital Age", "", [], [], True),
    ("Business Communication for Success", "", [], [], True),
    ("88 Open Essays", "A reader for first-year composition courses.", [], [], True),
    ("Guide to Grant Proposals", "", ["Writing and editing guides"], ["thema:CBW"], True),
    ("Untitled Handbook", "", [], ["lc:PE1408 .A1"], True),
    ("The Mass Media Landscape", "Lessons on the evolution of U.S. mass media.", ["Media studies"], [], False),
    ("Introduction to Poetry Writing", "", [], [], False),
    ("Writing Proofs in Mathematics", "", [], [], False),
    ("Music Composition Basics", "", [], [], False),
    ("Escribir en español: Writing in Spanish", "", [], [], False),
    ("Principles of Biology", "Cells and genetics.", ["Biology"], [], False),
])
def test_subject_filter(title, desc, names, codes, keep):
    score, reasons = subject_score(title, desc, names, codes, "en")
    assert keep_book(score) is keep, (score, reasons)


def test_subject_filter_creative_nonfiction_survives_the_creative_writing_rule():
    score, _ = subject_score("Writing Creative Nonfiction: The Essay", "", [], [], "en")
    assert keep_book(score)


def test_subject_filter_rejects_other_languages():
    score, reasons = subject_score("Writing Guide", "", [], [], "fr")
    assert not keep_book(score) and "not in English" in reasons[0]


def test_prescreen_is_looser_than_keep():
    score, _ = subject_score("Essays on Everything", "", [], [], "en")
    assert keep_book(score, prescreen=True) and not keep_book(score)


def test_subject_codes():
    assert classify_subject_code("CBW") == "thema:CBW"
    assert classify_subject_code("REF026000     REFERENCE / Writing Skills") == "bisac:REF026000"
    assert classify_subject_code("Business Computer Info Tech") is None
    assert lc_rule("PE1408")[0] == 3
    assert lc_rule("PN4121 .B3")[0] == 3
    assert lc_rule("QA76") is None
    assert lc_rule("") is None


# ---------------------------------------------------------------------------
# Tagging and small mappers
# ---------------------------------------------------------------------------

def test_tag_skins_needs_two_description_mentions():
    assert tag_skins("Technical Writing Essentials", "One chapter touches on social media.") == ["TECHCOMM"]
    assert "MULTIMODAL" in tag_skins("A Book", "Social media posts and more social media campaigns.")


def test_tag_topics_ranks_title_matches_first():
    topics = tag_topics("Writing a Thesis Statement", "")
    assert topics == ["thesis"]
    assert len(tag_topics("Argument", "audience genre style revision citation grammar summary")) == common.MAX_TOPICS


@pytest.mark.parametrize("first,last,full,expected", [
    ("Amanda", "Coolidge", "", "Coolidge, Amanda"),
    ("", "", "Suzan Last", "Last, Suzan"),
    ("", "", "BCcampus", "BCcampus"),
    ("", "", "Project Management Open Resources", "Project Management Open Resources"),
    ("", "", "Smith, Jane", "Smith, Jane"),
    ("", "", "Martin Luther King Jr.", "King, Martin Luther, Jr."),
    ("", "", "Dr. Layne Goodman", "Goodman, Layne"),
])
def test_person_name(first, last, full, expected):
    assert person_name(first, last, full) == expected


def test_clean_html_handles_double_escaping_and_cut_tags():
    assert clean_html("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;") == "Hello & welcome"
    assert clean_html('Adoption Form <a class="link-button" href="https://example.org/fed...') == "Adoption Form"


def test_make_reading_has_the_pack_shape():
    rec = make_reading(level="chapter", title="<em>Thesis</em>", source="otl", key="otl:1", parent_key="otl:0",
                       license="CC BY 4.0", license_verified=True, authors="Doe, Jane")
    assert set(common.PACK_FIELDS) <= set(rec)
    assert rec["title"] == "Thesis" and rec["needs_review"] is True and rec["provenance"] == "harvest-otl"
    assert rec["harvest"]["parent_key"] == "otl:0"
    with pytest.raises(KeyError):
        make_reading(level="book", title="T", source="otl", key="k", not_a_field=1)


# ---------------------------------------------------------------------------
# Pressbooks mapping
# ---------------------------------------------------------------------------

def _lic(url):
    return {"url": url, "name": "x"}


BY = "https://creativecommons.org/licenses/by/4.0/"
BY_NC = "https://creativecommons.org/licenses/by-nc/4.0/"


def _entry(e_id, title, words, link, lic=BY, **extra):
    return {"id": e_id, "title": title, "word_count": words, "link": link, "status": "publish",
            "has_post_content": True, "metadata": {"license": _lic(lic), "author": [{"name": "Jane Doe"}]}, **extra}


def test_pressbooks_build_records_keeps_substantive_parts_only():
    book = "https://pb.example.org/writebook/"
    meta = {"name": "Writing in College", "license": {"url": BY, "name": "CC BY"},
            "author": [{"contributor_first_name": "Jane", "contributor_last_name": "Doe", "name": "Jane Doe"}],
            "about": [{"identifier": "CBW", "name": "Writing and editing guides"}],
            "description": "<p>A first-year writing textbook.</p>", "datePublished": "2021-05-01", "wordCount": 30000,
            "publisher": {"name": "Example College"}}
    toc = {
        "front-matter": [_entry(1, "Acknowledgements", 2000, book + "front-matter/ack/"),
                         _entry(2, "Introduction: Why Write?", 1500, book + "front-matter/intro/")],
        "parts": [{"id": 3, "title": "Part One", "word_count": 40, "has_post_content": True, "link": book + "part/one/",
                   "chapters": [_entry(4, "1.1 Writing a Thesis Statement", 2500, book + "chapter/thesis/"),
                                _entry(5, "Placeholder", 30, book + "chapter/placeholder/"),
                                _entry(6, "1.2 Revising Drafts", 1800, book + "chapter/revising/", lic=BY_NC)]}],
        "back-matter": [_entry(7, "Glossary", 5000, book + "back-matter/glossary/")],
    }
    entry = pressbooks.opds_entry({"metadata": {"title": "Writing in College", "author": "Jane Doe", "language": "en"},
                                   "links": [{"href": book, "rel": "self", "type": "text/html"}]})
    recs = pressbooks.build_records(entry, meta, toc, 9, ["title: writing"])
    titles = [r["title"] for r in recs]
    assert titles == ["Writing in College", "Introduction: Why Write?", "1.1 Writing a Thesis Statement", "1.2 Revising Drafts"]
    bk, intro, thesis, revising = recs
    assert bk["level"] == "book" and bk["harvest"]["chapter_count"] == 3 and bk["year"] == 2021
    assert bk["authors"] == "Doe, Jane" and bk["pages"] == "30000 words"
    # The book takes the strictest chapter license, as build_catalog.tighten_parents does.
    assert bk["license_family"] == "CC BY-NC" and "strictest" in bk["notes"]
    assert revising["license"] == "CC BY-NC 4.0" and thesis["license"] == "CC BY 4.0"
    assert all(r["parent_title"] == "Writing in College" and r["harvest"]["parent_key"] == bk["harvest"]["key"]
               for r in recs[1:])
    assert intro["notes"].startswith("Front matter")
    assert "thesis" in thesis["topics"]
    assert all(r["id"] == "" and r["parent_id"] == "" for r in recs)


def test_pressbooks_chapter_without_license_falls_back_to_book():
    book = "https://pb.example.org/b/"
    meta = {"name": "B Writing", "license": {"url": BY_NC}}
    ch = _entry(1, "Chapter", 900, book + "chapter/c/")
    ch["metadata"] = {}
    recs = pressbooks.build_records(pressbooks.opds_entry({"metadata": {"title": "B"}, "links": [{"href": book, "rel": "self"}]}),
                                    meta, {"parts": [{"chapters": [ch]}]}, 5, [])
    assert recs[1]["license"] == "CC BY-NC 4.0" and "book's license applies" in recs[1]["notes"]


def test_split_author_string():
    assert pressbooks.split_author_string("Jane Doe, John Smith and Ann Lee") == ["Doe, Jane", "Smith, John", "Lee, Ann"]
    assert pressbooks.split_author_string("Doe, Jane") == ["Doe, Jane"]
    assert pressbooks.split_author_string("") == []


def test_directory_only_record_stays_link_only_and_keeps_license_hint():
    e = pressbooks.opds_entry({"metadata": {"title": "Public Speaking", "author": "Layne Goodman",
                                            "description": "License: CC BY-NC-SA (Attribution NonCommercial ShareAlike)"},
                               "links": [{"href": "https://open.example.edu/com225/", "rel": "self"}]})
    rec = pressbooks.directory_only_record(e, 3, ["title: public speaking"], "HTTP 403 bot check")
    assert rec["license"] == "" and rec["reuse_tier"] == "link-only"
    assert "CC BY-NC-SA" in rec["notes"] and "License" not in rec["summary"]


# ---------------------------------------------------------------------------
# OTL mapping
# ---------------------------------------------------------------------------

OTL_BOOK = {
    "id": 42, "title": "Writing for Success", "copyright_year": 2015, "license": "Attribution-NonCommercial-ShareAlike",
    "language": "eng", "description": "<p>A first-year writing text for college composition courses.</p>",
    "contributors": [{"contribution": "Author", "first_name": "Jane", "last_name": "Doe", "corporate": False},
                     {"contribution": "Editor", "first_name": "Ed", "last_name": "Itor", "corporate": False}],
    "subjects": [{"name": "Literature, Rhetoric, and Poetry", "call_number": "PE1408"}],
    "publishers": [{"name": "University of Minnesota Libraries Publishing"}],
    "formats": [{"format": "Online", "url": "https://open.lib.umn.edu/writingforsuccess/chapter/intro/"},
                {"format": "PDF", "url": "https://conservancy.umn.edu/x.pdf"},
                {"format": "eBook", "url": "https://open.lib.umn.edu/writingforsuccess/open/download?type=epub"}],
    "url": "https://open.umn.edu/opentextbooks/textbooks/writing-for-success",
}


def test_otl_book_record():
    rec, score, reasons = otl.book_record(OTL_BOOK, {"open.lib.umn.edu"})
    assert rec["license"] == "CC BY-NC-SA" and rec["license_verified"] is False and rec["reuse_tier"] == "adapt-nc"
    assert rec["authors"] == "Doe, Jane" and rec["year"] == 2015
    assert rec["pdf_url"].endswith(".pdf") and rec["epub_url"].endswith("type=epub")
    assert rec["harvest"]["pressbooks_url"] == "https://open.lib.umn.edu/writingforsuccess/"
    assert "no version given" in rec["notes"] and "FYC" in rec["skins"]


def test_otl_subject_does_not_carry_a_literature_book():
    lit = dict(OTL_BOOK, title="American Literature to 1865", description="Readings in early American literature.")
    rec, score, _ = otl.book_record(lit, set())
    assert rec is None


def test_otl_plan_pages_core_subjects_then_scoped_searches():
    subjects = [{"id": 33, "name": "Literature, Rhetoric, and Poetry", "call_number": "PE1408", "textbooks_count": 124, "parent": "Humanities"},
                {"id": 2, "name": "Business", "call_number": "HF5001", "textbooks_count": 144, "parent": ""},
                {"id": 76, "name": "New Media Journalism", "call_number": "PN4699-5650", "textbooks_count": 14, "parent": "J"},
                {"id": 37, "name": "Biology", "call_number": "QH308.2", "textbooks_count": 88, "parent": "Natural Sciences"}]
    plan = otl.plan_queries(subjects)
    assert [(p["subject"], p["q"]) for p in plan] == [(33, ""), (2, "communication"), (2, "writing")]


def test_pressbooks_root():
    hosts = {"opentextbc.ca"}
    assert otl.pressbooks_root("https://opentextbc.ca/writing/chapter/one/", hosts) == "https://opentextbc.ca/writing/"
    assert otl.pressbooks_root("https://x.pressbooks.pub/book", set()) == "https://x.pressbooks.pub/book/"
    assert otl.pressbooks_root("https://example.org/book/", hosts) == ""


# ---------------------------------------------------------------------------
# Merge: dedupe, ids, approval
# ---------------------------------------------------------------------------

def _pack_rec(rid, title, level="book", parent="", url="", license="CC BY 4.0"):
    rec = {k: "" for k in common.PACK_FIELDS}
    rec.update(id=rid, title=title, level=level, parent_title=parent, landing_url=url, html_url=url, license=license,
               license_family=common.license_family(license), reuse_tier=common.reuse_tier(common.license_family(license)),
               skins=[], topics=[], license_verified=True)
    return rec


def _cand(source, level, title, url, key=None, parent_key="", parent_title="", license="CC BY 4.0", **h):
    rec = make_reading(level=level, title=title, source=source, key=key or f"{source}:{url}", parent_key=parent_key,
                       license=license, license_verified=source == "pressbooks", landing_url=url, html_url=url,
                       parent_title=parent_title, skins=["FYC"], harvest=h)
    return rec


@pytest.fixture
def pack():
    return [
        _pack_rec("old-book", "Old Book of Writing", url="https://pb.example.org/oldbook/"),
        _pack_rec("old-book-intro", "Introduction", level="chapter", parent="Old Book of Writing",
                  url="https://pb.example.org/oldbook/chapter/intro/"),
        _pack_rec("technical-writing-essentials", "Technical Writing Essentials", url="https://pb.example.org/tw/"),
        _pack_rec("new-writing-guide", "Taken id on purpose"),
    ]


@pytest.fixture
def candidates():
    old = "https://pb.example.org/oldbook/"
    new = "https://pb.example.org/newguide/"
    return [
        _cand("pressbooks", "book", "Old Book of Writing", old, chapter_count=2),
        _cand("pressbooks", "chapter", "Introduction", old + "chapter/intro/", parent_key=f"pressbooks:{old}", parent_title="Old Book of Writing"),
        _cand("pressbooks", "chapter", "2. Brand New Chapter on Revision", old + "chapter/new/", parent_key=f"pressbooks:{old}", parent_title="Old Book of Writing"),
        _cand("pressbooks", "book", "New Writing Guide", new, chapter_count=1),
        _cand("pressbooks", "chapter", "1.1 Thesis Statements", new + "chapter/thesis/", parent_key=f"pressbooks:{new}", parent_title="New Writing Guide"),
        _cand("otl", "book", "New Writing Guide", "https://open.umn.edu/opentextbooks/textbooks/9", key="otl:9",
              license="CC BY-NC", pressbooks_url=new),
        _cand("pressbooks", "book", "Technical Writing Essentials 2nd Edition", "https://pb.example.org/tw2/"),
    ]


def test_merge_sources_combines_otl_and_pressbooks(candidates):
    candidates[-2]["html_url"] = "https://pb.example.org/newguide/"  # OTL's online link points at the Pressbooks book
    merged = merge.merge_sources(candidates)
    books = [r for r in merged if r["level"] == "book" and r["title"] == "New Writing Guide"]
    assert len(books) == 1
    b = books[0]
    assert b["harvest"]["source"] == "pressbooks" and b["harvest"]["also_from"][0]["source"] == "otl"
    # The sources disagree, so the stricter license is recorded (and it came from OTL, unverified).
    assert b["license"] == "CC BY-NC" and b["license_verified"] is False and "most restrictive" in b["notes"]


def test_build_queue_dedupes_against_pack_and_assigns_unique_ids(candidates, pack):
    queue, dupes, stats = merge.build_queue(candidates, pack)
    by_title = {r["title"]: r for r in queue}
    # The old book and its Introduction are exact duplicates; the new chapter of the old book is queued.
    assert {r["title"] for r in dupes} == {"Old Book of Writing", "Introduction"}
    new_ch = by_title["2. Brand New Chapter on Revision"]
    assert new_ch["parent_id"] == "old-book" and new_ch["queue"]["parent_in_pack"] == "old-book"
    assert new_ch["id"] == "old-book-brand-new-chapter-on-revision"
    # A new edition is a near duplicate, with the match named.
    tw2 = by_title["Technical Writing Essentials 2nd Edition"]
    assert tw2["queue"]["status"] == "near-duplicate" and tw2["queue"]["match"][0]["id"] == "technical-writing-essentials"
    # Ids never collide with pack ids, and chapters point at their book's new id.
    guide = by_title["New Writing Guide"]
    assert guide["id"] == "new-writing-guide-2"
    assert by_title["1.1 Thesis Statements"]["parent_id"] == guide["id"]
    assert by_title["1.1 Thesis Statements"]["id"] == "new-writing-guide-2-thesis-statements"
    ids = [r["id"] for r in queue]
    assert len(ids) == len(set(ids)) and not set(ids) & {p["id"] for p in pack}
    assert stats["books_duplicate"] == 1 and stats["books_near_duplicate"] == 1 and stats["books_new"] == 1
    md = merge.queue_markdown(queue, dupes, stats, "2026-10-17", "queue-20261017")
    assert "New Writing Guide" in md and "Already in the pack" in md and "--approve" in md


def test_loose_title_strips_numbering_and_editions():
    assert merge.loose_title("1.2 Writing Essentials, 2nd Edition") == merge.loose_title("Writing Essentials")
    assert merge.loose_title("Chapter 3: Revising") == "revising"
    assert merge.loose_title("Technical Writing Essentials: Introduction to Professional Communications") == \
        merge.loose_title("Technical Writing Essentials 2nd Edition")
    # A short main title keeps its subtitle, so different books with generic names stay apart.
    assert merge.loose_title("Business Communication - A Relational Approach") != \
        merge.loose_title("Business Communication: Five Core Competencies")


def test_unique_ids():
    taken = {"a-book"}
    assert merge.book_id("A Book", taken) == "a-book-2"
    assert merge.book_id("A Book", taken) == "a-book-3"
    assert merge.book_id("Writing in College: A Guide for Students", set()) == "writing-in-college"


def test_approve_appends_sorted_and_never_touches_existing_lines(tmp_path, candidates, pack):
    pack_path = tmp_path / "readings.jsonl"
    original = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in sorted(pack, key=lambda r: r["id"])]
    original[0] = original[0].replace('", "', '",  "', 1)  # odd spacing must survive byte for byte
    pack_path.write_text("\n".join(original) + "\n", encoding="utf-8")
    queue, _, _ = merge.build_queue(candidates, pack)
    queue_path = tmp_path / "queue.jsonl"
    common.write_jsonl(queue_path, queue)

    added = merge.approve(queue_path, "all", pack_path=pack_path)
    lines = pack_path.read_text(encoding="utf-8").splitlines()
    ids = [json.loads(l)["id"] for l in lines]
    assert ids == sorted(ids)
    for line in original:
        assert line in lines
    new = [json.loads(l) for l in lines if json.loads(l)["id"] in added]
    assert new and all(r["needs_review"] is True and "harvest" not in r and "queue" not in r for r in new)
    assert set(common.PACK_FIELDS) <= set(new[0])
    # Near duplicates wait to be named.
    assert not any(r["title"].startswith("Technical Writing Essentials 2nd") for r in new)
    # A second run adds nothing: the queue lines are marked approved.
    assert merge.approve(queue_path, "all", pack_path=pack_path) == []


def test_approve_named_book_brings_chapters_and_handles_id_collisions(tmp_path, candidates, pack):
    pack_path = tmp_path / "readings.jsonl"
    queue, _, _ = merge.build_queue(candidates, pack)
    guide = next(r for r in queue if r["title"] == "New Writing Guide")
    # Someone added a record with the proposed id after the queue was written.
    pack_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in pack + [_pack_rec(guide["id"], "Other")]),
                         encoding="utf-8")
    queue_path = tmp_path / "queue.jsonl"
    common.write_jsonl(queue_path, queue)
    added = merge.approve(queue_path, guide["id"], pack_path=pack_path)
    recs = {json.loads(l)["id"]: json.loads(l) for l in pack_path.read_text(encoding="utf-8").splitlines()}
    assert len(added) == 2 and guide["id"] + "-2" in added
    chapter = next(recs[i] for i in added if recs[i]["level"] == "chapter")
    assert chapter["parent_id"] == guide["id"] + "-2"


def test_approve_dry_run_changes_nothing(tmp_path, candidates, pack):
    pack_path = tmp_path / "readings.jsonl"
    pack_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in pack), encoding="utf-8")
    before = pack_path.read_bytes()
    queue, _, _ = merge.build_queue(candidates, pack)
    queue_path = tmp_path / "queue.jsonl"
    common.write_jsonl(queue_path, queue)
    assert merge.approve(queue_path, "all", pack_path=pack_path, dry_run=True)
    assert pack_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Polite HTTP (fake transport, no network, no sleeping)
# ---------------------------------------------------------------------------

def _resp(status, body="", headers=None):
    r = requests.Response()
    r.status_code = status
    r.headers = CaseInsensitiveDict(headers or {})
    r._content = body.encode("utf-8")
    r.encoding = "utf-8"
    return r


class FakeHTTP:
    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []
        self.headers = {}

    def get(self, url, timeout=None, allow_redirects=True):
        self.calls.append(url)
        seq = self.routes.get(url)
        if not seq:
            return _resp(404, "nope", {"content-type": "text/plain"})
        return seq.pop(0) if len(seq) > 1 else seq[0]


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(common.time, "sleep", lambda s: slept.append(s))
    return slept


def _session(routes, **kw):
    s = PoliteSession(**kw)
    s.http = FakeHTTP(routes)
    return s


def test_robots_crawl_delay_and_disallow(no_sleep):
    s = _session({
        "https://ex.org/robots.txt": [_resp(200, "User-agent: *\nCrawl-delay: 120\nDisallow: /private/", {"content-type": "text/plain"})],
        "https://ex.org/api": [_resp(200, '{"ok": 1}', {"content-type": "application/json"})],
    })
    assert s.get_json("https://ex.org/api") == {"ok": 1}
    assert s.host_delay("ex.org") == 120
    with pytest.raises(Disallowed):
        s.get_json("https://ex.org/private/x")
    assert no_sleep and max(no_sleep) > 100  # the second request waited out the crawl-delay


def test_bot_check_stops_without_retry(no_sleep):
    blocked = _resp(403, "<html>Request blocked. Generated by cloudfront</html>", {"content-type": "text/html", "server": "CloudFront"})
    s = _session({"https://a.pressbooks.pub/robots.txt": [blocked]})
    with pytest.raises(Blocked):
        s.get_json("https://a.pressbooks.pub/book/wp-json/pressbooks/v2/metadata")
    assert s.http.calls == ["https://a.pressbooks.pub/robots.txt"]
    with pytest.raises(Blocked):
        s.get_json("https://a.pressbooks.pub/other/")
    assert len(s.http.calls) == 1  # never asked again


def test_two_blocked_subdomains_block_the_parent_domain(no_sleep):
    blocked = _resp(403, "<html>Request blocked</html>", {"content-type": "text/html"})
    s = _session({"https://a.pressbooks.pub/robots.txt": [blocked], "https://b.pressbooks.pub/robots.txt": [blocked]})
    for host in ("a", "b"):
        with pytest.raises(Blocked):
            s.get_json(f"https://{host}.pressbooks.pub/x")
    with pytest.raises(Blocked):
        s.get_json("https://c.pressbooks.pub/x")
    assert len(s.http.calls) == 2


def test_server_errors_retry_with_backoff(no_sleep):
    s = _session({"https://ex.org/api": [_resp(503, "busy", {"content-type": "text/plain"}),
                                         _resp(200, "[1]", {"content-type": "application/json"})]})
    assert s.get_json("https://ex.org/api") == [1]
    assert s.http.calls.count("https://ex.org/api") == 2


def test_budget_stops_the_run(no_sleep):
    s = _session({"https://ex.org/a": [_resp(200, "1", {"content-type": "application/json"})]}, max_requests=2)
    s.get_json("https://ex.org/a")  # robots.txt (404) + the request
    with pytest.raises(common.BudgetExhausted):
        s.get_json("https://ex.org/a")


def test_state_carries_last_request_time_across_runs(no_sleep):
    import time as _t
    state = {"last": {"ex.org": _t.time()}, "robots": {"ex.org": {"fetched": _t.time(), "status": 200,
                                                                     "text": "User-agent: *\nCrawl-delay: 120"}}}
    s = _session({"https://ex.org/a": [_resp(200, "1", {"content-type": "application/json"})]}, state=state)
    s.get_json("https://ex.org/a")
    assert s.http.calls == ["https://ex.org/a"]  # robots.txt came from the saved state
    assert no_sleep and no_sleep[0] > 100       # and the saved last-request time made it wait
