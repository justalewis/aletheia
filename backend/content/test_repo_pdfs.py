"""Corpus PDFs listed in a manifest but missing on disk come from the repo, checked against the manifest."""
import hashlib
import json
from io import StringIO

import pytest
from django.core.management import call_command

from catalog.models import Pack, Reading
from conftest import FakeSession, make_pdf
from content.fetch import Client
from content.library import PdfLibrary
from content.management.commands import warm_content
from content.repo_pdfs import RepoPdfs
from content.warm import Warmer

BASE = "https://raw.example.org/me/aletheia/main/corpus/files/"
CHAPTER = make_pdf(3, label="CH")
BOOK = make_pdf(2, label="BK")


def entry(path, data, ids, **kw):
    return {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "catalog_ids": ids, **kw}


@pytest.fixture
def corpus(tmp_path):
    """corpus/files/drive with a manifest and no PDFs, as in the Docker image."""
    files = tmp_path / "corpus" / "files"
    drive = files / "drive"
    drive.mkdir(parents=True)
    (drive / "manifest.json").write_text(json.dumps({"files": [
        entry("ws1/open chapter.pdf", CHAPTER, ["ws-chapter"], source_url="https://ws.example.org/ch"),
        entry("whole.pdf", BOOK, ["book-ch1", "book-ch2"]),
        entry("books/bad/chapter11.pdf", CHAPTER, []),
    ]}))
    return files


@pytest.fixture
def pack(db):
    return Pack.objects.create(id="t", name="Test", version="0", config={})


def reading(pack, rid, **kw):
    return Reading.objects.create(id=rid, pack=pack, level="chapter", title=f"Reading {rid}", license="CC BY 4.0",
                                  reuse_tier=kw.pop("tier", "adapt"), license_verified=True, **kw)


def chapter_url():
    return BASE + "drive/ws1/open%20chapter.pdf"


def make_warmer(store, routes, corpus, base=BASE, offline=False):
    session = FakeSession(routes)
    folders = [corpus / "drive"]
    w = Warmer(store, Client(session=session, delay=0, sleep=lambda s: None), PdfLibrary(folders),
               offline=offline, repo=RepoPdfs(base, folders, corpus))
    return w, session


def test_listed_pdf_is_fetched_by_manifest_path_and_checked(pack, content_store, corpus):
    r = reading(pack, "ws-chapter", tier="verbatim", pdf_url="https://ws.example.org/ch.pdf")
    w, session = make_warmer(content_store, {chapter_url(): (200, {}, CHAPTER)}, corpus)
    w.prepare([r], [])
    out = w.warm(r)
    assert (out.status, out.kind, out.via) == ("cached", "pdf", "repo-pdf")
    assert out.detail == "3 pages from drive/ws1/open chapter.pdf"
    entry_ = content_store.get("ws-chapter")
    assert entry_.meta["repo_file"] == "drive/ws1/open chapter.pdf" and entry_.meta["repo_url"] == chapter_url()
    assert entry_.meta["source_url"] == "https://ws.example.org/ch"
    with entry_.open_pdf() as fh:
        assert fh.read() == CHAPTER
    # Only the repo was asked (its robots.txt, then the file); never the publisher.
    assert session.calls == ["https://raw.example.org/robots.txt", chapter_url()]


def test_matching_by_url_path_works_as_on_disk(pack, content_store, corpus):
    r = reading(pack, "wac-ch11", pdf_url="https://wacclearinghouse.org/docs/books/bad/chapter11.pdf")
    w, session = make_warmer(content_store, {BASE + "drive/books/bad/chapter11.pdf": (200, {}, CHAPTER)}, corpus)
    w.prepare([r], [])
    assert w.warm(r).via == "repo-pdf"
    assert not any("wacclearinghouse" in c for c in session.calls)


def test_a_file_on_disk_wins_and_the_repo_is_not_asked(pack, content_store, corpus):
    (corpus / "drive" / "ws1").mkdir()
    (corpus / "drive" / "ws1" / "open chapter.pdf").write_bytes(CHAPTER)
    r = reading(pack, "ws-chapter", pdf_url="https://ws.example.org/ch.pdf")
    w, session = make_warmer(content_store, {}, corpus)
    assert w.warm(r).via == "local-pdf" and session.calls == []


@pytest.mark.parametrize("body", [make_pdf(3, label="XX"), CHAPTER[:-10]])
def test_a_file_that_doesnt_match_the_manifest_is_refused(pack, content_store, corpus, body):
    r = reading(pack, "ws-chapter", pdf_url="https://ws.example.org/ch.pdf")
    publisher = "https://ws.example.org/ch.pdf"
    w, session = make_warmer(content_store, {chapter_url(): (200, {}, body)}, corpus)
    out = w.warm(r)
    assert out.status == "checksum" and content_store.get("ws-chapter") is None
    # Next in line is the publisher's URL.
    assert session.calls[-1] == publisher

    w, _ = make_warmer(content_store, {chapter_url(): (200, {}, body), publisher: (200, {}, CHAPTER)}, corpus)
    assert w.warm(r).via == "download"


def test_whole_book_files_and_offline_runs_never_fetch(pack, content_store, corpus):
    whole = reading(pack, "book-ch1", pdf_url="https://example.org/whole.pdf")
    w, session = make_warmer(content_store, {BASE + "drive/whole.pdf": (200, {}, BOOK)}, corpus)
    w.warm(whole)
    assert BASE + "drive/whole.pdf" not in session.calls

    r = reading(pack, "ws-chapter", pdf_url="https://ws.example.org/ch.pdf")
    w, session = make_warmer(content_store, {chapter_url(): (200, {}, CHAPTER)}, corpus, offline=True)
    assert w.warm(r).status == "offline" and session.calls == []


def test_no_base_url_or_a_folder_outside_the_corpus_turns_it_off(corpus, tmp_path):
    assert len(RepoPdfs("", [corpus / "drive"], corpus)) == 0
    elsewhere = tmp_path / "mine"
    elsewhere.mkdir()
    (elsewhere / "manifest.json").write_text((corpus / "drive" / "manifest.json").read_text())
    assert len(RepoPdfs(BASE, [elsewhere], corpus)) == 0
    assert len(RepoPdfs(BASE, [corpus / "drive"], corpus)) == 2  # the whole book isn't offered


def test_warm_command_uses_the_base_url_setting(pack, content_store, corpus, settings, monkeypatch):
    settings.CORPUS_DIR = corpus.parent
    settings.CONTENT_PDF_DIRS = [corpus / "drive"]
    settings.CONTENT_PDF_BASE_URL = BASE
    reading(pack, "ws-chapter", pdf_url="https://ws.example.org/ch.pdf")
    session = FakeSession({chapter_url(): (200, {}, CHAPTER)})
    monkeypatch.setattr(warm_content, "Client", lambda delay: Client(session=session, delay=0, sleep=lambda s: None))
    out = StringIO()
    call_command("warm_content", "--ids", "ws-chapter", stdout=out)
    assert content_store.get("ws-chapter").meta["fetcher"] == "repo-pdf"
    assert f"2 corpus PDFs aren't on disk; fetching any needed from {BASE}" in out.getvalue()

    # --pdf-base-url "" turns the source off for one run.
    content_store.clear("ws-chapter")
    session.calls.clear()
    call_command("warm_content", "--ids", "ws-chapter", "--pdf-base-url", "", stdout=StringIO())
    assert content_store.get("ws-chapter") is None and chapter_url() not in session.calls
