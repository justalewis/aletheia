"""The content cache: full text of readings, kept outside git.

One folder per reading in the "content" storage (backend/content_cache/ by default; set
ALETHEIA_CONTENT_CACHE to move it, or point STORAGES["content"] at object storage):

    <key>/meta.json      what was cached, from where, and when (written last)
    <key>/content.html   sanitized full text, for readings published as web pages
    <key>/source.pdf     the publisher's PDF, byte for byte, for readings published as PDFs (for a
                         chapter of a whole-book PDF, its own pages, copied unchanged)
    <key>/img/<name>     images the HTML uses, so the PDF reader doesn't fetch at export time
    _report.json         the last warm run: every reading's status, cached or not

A folder without meta.json is an interrupted fetch and is ignored. The cache records the license
and tier at fetch time for reference only; exports always apply the catalog's current tier.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import cached_property

from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage, storages

REPORT = "_report.json"


def key_for(reading_id: str) -> str:
    """A short, filesystem-safe folder name (Windows paths top out at 260 characters)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", reading_id).strip(".-") or "reading"
    if len(safe) <= 64 and safe == reading_id:
        return safe
    return f"{safe[:55].rstrip('-.')}-{hashlib.sha1(reading_id.encode()).hexdigest()[:8]}"


@dataclass
class Entry:
    id: str
    meta: dict
    store: "ContentStore" = field(repr=False)

    @property
    def kind(self) -> str:
        return self.meta.get("kind", "")

    @property
    def fetched(self) -> str:
        return self.meta.get("fetched", "")

    @property
    def source_url(self) -> str:
        return self.meta.get("source_url", "")

    @property
    def pages(self) -> int | None:
        return self.meta.get("pages")

    @property
    def dropped(self) -> list[str]:
        """Media the sanitizer had to leave out (video without a source, MathML, canvas...)."""
        return self.meta.get("dropped", [])

    @property
    def missing_images(self) -> list[str]:
        return self.meta.get("missing_images", [])

    @cached_property
    def html(self) -> str:
        return self.store.read_text(self.id, "content.html") if self.kind == "html" else ""

    @property
    def images(self) -> dict[str, str]:
        """Image URL -> cached file name."""
        return self.meta.get("images", {})

    def open_pdf(self):
        return self.store.open(self.id, "source.pdf")

    def image_bytes(self, name: str) -> bytes:
        with self.store.open(self.id, f"img/{name}") as fh:
            return fh.read()


class ContentStore:
    def __init__(self, storage: Storage | None = None):
        self.storage = storage or storages["content"]

    def _name(self, reading_id: str, filename: str) -> str:
        return f"{key_for(reading_id)}/{filename}"

    def get(self, reading_id: str) -> Entry | None:
        name = self._name(reading_id, "meta.json")
        if not self.storage.exists(name):
            return None
        with self.storage.open(name, "rb") as fh:
            meta = json.loads(fh.read().decode("utf-8"))
        if meta.get("id") != reading_id or meta.get("kind") not in ("html", "pdf"):
            return None
        return Entry(reading_id, meta, self)

    def read_text(self, reading_id: str, filename: str) -> str:
        with self.storage.open(self._name(reading_id, filename), "rb") as fh:
            return fh.read().decode("utf-8")

    def open(self, reading_id: str, filename: str):
        return self.storage.open(self._name(reading_id, filename), "rb")

    def path(self, reading_id: str, filename: str) -> str | None:
        """Local path when the storage is on disk (pypdf and WeasyPrint read faster from paths)."""
        try:
            return self.storage.path(self._name(reading_id, filename))
        except NotImplementedError:
            return None

    def clear(self, reading_id: str) -> None:
        self._delete_tree(key_for(reading_id))

    def _delete_tree(self, folder: str) -> None:
        if not self.storage.exists(folder):
            return
        dirs, files = self.storage.listdir(folder)
        for f in files:
            self.storage.delete(f"{folder}/{f}")
        for d in dirs:
            self._delete_tree(f"{folder}/{d}")

    def save_html(self, reading_id: str, html: str, meta: dict, images: dict[str, bytes] | None = None) -> Entry:
        self.clear(reading_id)
        for name, data in (images or {}).items():
            self.storage.save(self._name(reading_id, f"img/{name}"), ContentFile(data))
        self.storage.save(self._name(reading_id, "content.html"), ContentFile(html.encode("utf-8")))
        return self._commit(reading_id, {**meta, "kind": "html"})

    def save_pdf(self, reading_id: str, source, meta: dict) -> Entry:
        """`source` is bytes or an open binary file."""
        self.clear(reading_id)
        content = ContentFile(source) if isinstance(source, bytes) else File(source)
        self.storage.save(self._name(reading_id, "source.pdf"), content)
        return self._commit(reading_id, {**meta, "kind": "pdf"})

    def _commit(self, reading_id: str, meta: dict) -> Entry:
        meta = {"id": reading_id, **meta}
        self.storage.save(self._name(reading_id, "meta.json"),
                          ContentFile(json.dumps(meta, indent=1, ensure_ascii=False).encode("utf-8")))
        return Entry(reading_id, meta, self)

    def write_report(self, report: dict) -> None:
        self.storage.save(REPORT, ContentFile(json.dumps(report, indent=1, ensure_ascii=False).encode("utf-8")))

    def read_report(self) -> dict:
        if not self.storage.exists(REPORT):
            return {}
        with self.storage.open(REPORT, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
