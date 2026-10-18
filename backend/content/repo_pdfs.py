"""PDFs a corpus/files manifest lists but the disk doesn't have, fetched from a copy of the repo.

The Docker image ships corpus/files/*/manifest.json without the PDFs. On the server,
warm_content fetches each listed PDF from settings.CONTENT_PDF_BASE_URL (in production, the
public GitHub repo's raw files) by its path under corpus/files, checks it against the
manifest's size and SHA-256, and stores it in the content cache. No auth: the repo is public.

Readings are matched to files the same way library.py matches files on disk (manifest catalog
id, then <reading-id>.pdf, then the end of the reading's pdf_url). Whole-book PDFs listed for
several readings are never used for one chapter. warm.py tries local folders first, then this,
then the publisher's own URL.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from content.fetch import MAX_PDF_BYTES, Client, FetchError
from content.library import PdfLibrary, manifest_rows


@dataclass(frozen=True)
class RepoPdf:
    rel_path: str           # under corpus/files, e.g. "drive/informedwriter/chapter1.pdf"
    sha256: str
    size: int | None = None
    source_url: str = ""
    matched_by: str = ""

    @property
    def label(self) -> str:
        return self.rel_path


class RepoPdfs:
    def __init__(self, base_url: str, folders: list[Path], files_root: Path):
        self.base_url = base_url.rstrip("/") + "/" if base_url else ""
        self.by_id: dict[str, RepoPdf] = {}
        self.by_tail: dict[str, RepoPdf] = {}
        self.by_name: dict[str, list[RepoPdf]] = {}
        if not self.base_url:
            return
        files_root = Path(files_root).resolve()
        for root in folders:
            root = Path(root)
            try:
                prefix = root.resolve().relative_to(files_root).as_posix()
            except ValueError:
                continue  # a folder outside corpus/files isn't in the repo
            listed = [(row, RepoPdf(str(PurePosixPath(prefix) / row.path), row.sha256.lower(), row.size,
                                    row.source_url))
                      for row in manifest_rows(root)
                      if row.path and row.sha256 and len(row.ids) <= 1 and not (root / row.path).is_file()]
            for row, pdf in listed:
                if row.ids:
                    self.by_id[row.ids[0]] = replace(pdf, matched_by="manifest")
            for row, pdf in listed:  # names after manifest ids, as library.py does
                rel = PurePosixPath(row.path.lower())
                self.by_id.setdefault(PurePosixPath(row.path).stem, replace(pdf, matched_by="file name"))
                by_url = replace(pdf, matched_by="url path")
                for n in (2, 3):
                    if len(rel.parts) >= n:
                        self.by_tail.setdefault("/".join(rel.parts[-n:]), by_url)
                self.by_name.setdefault(rel.name, []).append(by_url)

    def __len__(self) -> int:
        """How many listed files are available to fetch."""
        return len({p.rel_path for ps in self.by_name.values() for p in ps})

    # The same matching rules as files on disk.
    find = PdfLibrary.find

    def url(self, pdf: RepoPdf) -> str:
        return self.base_url + quote(pdf.rel_path)

    def fetch(self, client: Client, pdf: RepoPdf) -> bytes:
        data, _ = client.get_bytes(self.url(pdf), MAX_PDF_BYTES, accept="application/pdf")
        if pdf.size is not None and len(data) != pdf.size:
            raise FetchError("checksum", f"{pdf.rel_path}: {len(data)} bytes, the manifest says {pdf.size}")
        if hashlib.sha256(data).hexdigest() != pdf.sha256:
            raise FetchError("checksum", f"{pdf.rel_path}: SHA-256 doesn't match the manifest")
        return data
