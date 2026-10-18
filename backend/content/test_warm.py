import json

import pytest
from django.core.management import call_command

from catalog.models import Pack, Reading
from conftest import FakeSession, make_pdf
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


def test_shared_book_pdf_is_not_used_for_a_chapter(pack, content_store):
    url = "https://textbooks.example.edu/book.pdf"
    a = reading(pack, "ch-a", pdf_url=url)
    b = reading(pack, "ch-b", pdf_url=url)
    w, session = warmer(content_store, {"https://textbooks.example.edu/robots.txt": ROBOTS_OK,
                                        url: (200, {}, make_pdf(1))})
    w.prepare([a, b], [url, url])
    assert w.warm(a).status == "whole-book-pdf" and session.calls == []


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
