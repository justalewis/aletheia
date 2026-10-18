import json

import pytest
from django.core.management import call_command
from pypdf import PdfReader

from catalog.models import Pack, Reading
from conftest import FakeSession, make_pdf, text_pdf
from content.fetch import Client, pressbooks_api_url
from content.library import PdfLibrary
from content.warm import Warmer

ROBOTS_OK = (200, {}, "User-agent: *\nAllow: /\n")
PAGE = "https://press.example.org/book/chapter/open-chapter/"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture
def pack(db):
    return Pack.objects.create(id="t", name="Test", version="0", config={})


def reading(pack, rid, tier="adapt", verified=True, **kw):
    return Reading.objects.create(id=rid, pack=pack, level=kw.pop("level", "chapter"), title=f"Reading {rid}",
                                  license="CC BY 4.0", reuse_tier=tier, license_verified=verified, **kw)


def pressbooks_routes(text="<p>" + "Chapter text. " * 60 + "</p>"):
    body = [{"slug": "open-chapter", "link": PAGE, "title": {"rendered": "Open"},
             "content": {"rendered": text + '<img src="/fig.png" alt="fig"><script>x()</script>'}}]
    return {"https://press.example.org/robots.txt": ROBOTS_OK,
            pressbooks_api_url(PAGE): (200, {}, json.dumps(body)),
            "https://press.example.org/fig.png": (200, {"Content-Type": "image/png"}, PNG)}


def warmer(store, routes, folders=()):
    session = FakeSession(routes)
    return Warmer(store, Client(session=session, delay=0, sleep=lambda s: None), PdfLibrary(list(folders))), session


def test_html_reading_is_sanitized_and_stored_with_images(pack, content_store):
    r = reading(pack, "open", html_url=PAGE)
    w, _ = warmer(content_store, pressbooks_routes())
    out = w.warm(r)
    assert (out.status, out.kind, out.via) == ("cached", "html", "pressbooks")
    entry = content_store.get("open")
    assert "Chapter text." in entry.html and "<script" not in entry.html
    assert entry.meta["source_url"] == pressbooks_api_url(PAGE) and entry.fetched
    assert entry.images == {"https://press.example.org/fig.png": next(iter(entry.images.values()))}
    assert entry.image_bytes(entry.images["https://press.example.org/fig.png"]) == PNG


@pytest.mark.parametrize("tier,verified", [("link-only", True), ("adapt", False)])
def test_link_only_readings_are_never_fetched(pack, content_store, tier, verified):
    r = reading(pack, "closed", tier=tier, verified=verified, html_url=PAGE)
    w, session = warmer(content_store, pressbooks_routes())
    assert w.warm(r).status == "link-only"
    assert session.calls == [] and content_store.get("closed") is None


def test_short_pages_are_not_cached(pack, content_store):
    r = reading(pack, "part", html_url=PAGE)
    w, _ = warmer(content_store, pressbooks_routes(text="<p>Part 2</p>"))
    assert w.warm(r).status == "empty"


def test_local_pdf_by_manifest_and_by_name(pack, content_store, tmp_path):
    drive = tmp_path / "drive"
    (drive / "ws1").mkdir(parents=True)
    (drive / "ws1" / "chapter.pdf").write_bytes(make_pdf(3))
    (drive / "whole.pdf").write_bytes(make_pdf(2))
    (drive / "manifest.json").write_text(json.dumps({"files": [
        {"path": "ws1/chapter.pdf", "catalog_ids": ["ws-chapter"], "source_url": "https://ws.example.org/ch"},
        {"path": "whole.pdf", "catalog_ids": ["book-ch1", "book-ch2"]},
    ]}))
    extra = tmp_path / "mine"
    (extra / "books" / "bad").mkdir(parents=True)
    (extra / "books" / "bad" / "chapter11.pdf").write_bytes(make_pdf(1))
    (extra / "named-reading.pdf").write_bytes(make_pdf(2))

    by_manifest = reading(pack, "ws-chapter", tier="verbatim", pdf_url="https://ws.example.org/ch.pdf")
    by_url = reading(pack, "wac-ch11", pdf_url="https://wacclearinghouse.org/docs/books/bad/chapter11.pdf")
    by_name = reading(pack, "named-reading", pdf_url="https://wacclearinghouse.org/docs/books/x/y.pdf")
    whole = reading(pack, "book-ch1", pdf_url="https://example.org/whole.pdf")
    w, session = warmer(content_store, {}, [extra, drive])
    w.prepare([by_manifest, by_url, by_name, whole], [])

    assert w.warm(by_manifest).detail == "3 pages from drive/ws1/chapter.pdf"
    assert content_store.get("ws-chapter").meta["source_url"] == "https://ws.example.org/ch"
    assert w.warm(by_url).status == "cached"
    assert w.warm(by_name).status == "cached"
    assert w.warm(whole).status == "whole-book-pdf"
    # The WAC links are gated, so nothing was requested.
    assert not any("wacclearinghouse" in c for c in session.calls)
    pdf = content_store.get("ws-chapter")
    with pdf.open_pdf() as fh:
        assert fh.read() == (drive / "ws1" / "chapter.pdf").read_bytes()  # byte for byte


def test_gated_pdf_is_reported_not_fetched(pack, content_store):
    r = reading(pack, "wac", pdf_url="https://wacclearinghouse.org/docs/books/bad/chapter9.pdf")
    w, session = warmer(content_store, {})
    out = w.warm(r)
    assert out.status == "bot-gated" and session.calls == []


def test_shared_book_pdf_is_never_used_whole_and_fetched_once(pack, content_store):
    url = "https://textbooks.example.edu/book.pdf"
    a = reading(pack, "ch-a", pdf_url=url + "#page=3")
    b = reading(pack, "ch-b", pdf_url=url + "#page=9")
    w, session = warmer(content_store, {"https://textbooks.example.edu/robots.txt": ROBOTS_OK,
                                        url: (200, {}, make_pdf(1))})
    w.prepare([a, b], [a.pdf_url, b.pdf_url])
    out = w.warm(a)
    assert out.status == "whole-book-pdf" and "no printed contents" in out.detail
    assert w.warm(b).status == "whole-book-pdf"
    assert session.calls.count(url) == 1 and content_store.get("ch-a") is None


def book_pdf() -> bytes:
    """A 12-page collection: contents on page 2, running heads with printed numbers (PDF page =
    printed page + 1), a part-title page between the chapters."""
    body = ["it was the best of times, it was the worst of times"] * 3
    pages = [
        ["A Collection"],
        ["TABLE OF CONTENTS", "Introduction 1", "Ed Itor", "First Chapter Title Here 3", "Ann Author",
         "PART TWO", "Second Chapter Title Here 6", "Bo Writer", "Afterword Pieces 9", "Cy Closer"],
        ["INTRODUCTION", "Ed Itor", *body],
        ["2 A Collection", *body],
        ["FIRST CHAPTER TITLE", "HERE", "Ann Author", *body],
        ["4 A Collection", *body, "Further Reading"],
        ["PART TWO"],
        ["SECOND CHAPTER TITLE HERE", "Bo Writer", *body],
        ["Part Two 7", *body],
        ["8 A Collection", *body],
        ["AFTERWORD PIECES", "Cy Closer", *body],
        ["Part Two 10", *body],
    ]
    return text_pdf(pages)


def test_chapter_pages_are_cut_from_the_whole_book(pack, content_store, tmp_path):
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "collection.pdf").write_bytes(book_pdf())
    (drive / "manifest.json").write_text(json.dumps({"files": [
        {"path": "collection.pdf", "catalog_ids": ["the-book"], "source_url": "https://books.example.edu/coll"}]}))
    book = reading(pack, "the-book", level="book")
    url = "https://books.example.edu/collection.pdf"
    first = reading(pack, "first", tier="verbatim", parent=book, pdf_url=url + "#page=5", pages=3)
    first.title = "First Chapter Title Here"
    second = reading(pack, "second", parent=book, pdf_url=url + "#page=7")
    second.title = "Second Chapter Title Here"
    w, session = warmer(content_store, {}, [drive])
    w.prepare([first, second], [first.pdf_url, second.pdf_url])

    out = w.warm(first)
    assert (out.status, out.via) == ("cached", "book-pages")
    assert out.detail == "2 pages (printed 3-4) from drive/collection.pdf"  # the part-title page is left off
    entry = content_store.get("first")
    assert entry.pages == 2 and entry.meta["book_pages"] == "5-6" and entry.meta["printed_pages"] == "3-4"
    assert entry.meta["source_url"] == "https://books.example.edu/coll"
    with entry.open_pdf() as fh:
        texts = [p.extract_text() for p in PdfReader(fh).pages]
    assert texts[0].startswith("FIRST CHAPTER TITLE") and texts[1].startswith("4 A Collection")

    # #page=7 points at the part-title page, not the chapter (page 8): refuse rather than guess.
    out = w.warm(second)
    assert out.status == "whole-book-pdf" and "the link says 7" in out.detail
    assert session.calls == []


def test_chapter_pages_must_match_the_catalog_count(pack, content_store, tmp_path):
    (tmp_path / "collection.pdf").write_bytes(book_pdf())
    (tmp_path / "manifest.json").write_text(json.dumps({"files": [
        {"path": "collection.pdf", "catalog_ids": ["the-book"]}]}))
    book = reading(pack, "the-book", level="book")
    ch = reading(pack, "ch", parent=book, pdf_url="https://books.example.edu/collection.pdf", pages=5)
    ch.title = "First Chapter Title Here"
    w, _ = warmer(content_store, {}, [tmp_path])
    w.prepare([ch], [ch.pdf_url, ch.pdf_url])
    out = w.warm(ch)
    assert out.status == "whole-book-pdf" and "the catalog says 5" in out.detail


def test_warm_command_writes_report(pack, content_store, tmp_path, settings):
    settings.CORPUS_DIR = tmp_path / "corpus"
    arcs = settings.CORPUS_DIR / "t" / "arcs"
    arcs.mkdir(parents=True)
    (arcs / "arc.yaml").write_text("id: arc\nunits:\n  - id: u\n    readings:\n      core: [closed, missing-id]\n")
    reading(pack, "closed", tier="link-only")
    call_command("warm_content", "--pack", "t", "--offline")
    report = content_store.read_report()
    assert report["counts"] == {"link-only": 1}
    assert report["unknown_ids"] == ["missing-id"]
