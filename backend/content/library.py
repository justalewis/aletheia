"""Openly licensed PDFs already on disk, matched to catalog readings.

A folder can describe its files with a manifest.json, in either shape the repo's tools write:
  - tools/import_corpus.py: {"files": [{"path", "catalog_ids", "source_url", ...}]}
  - tools/mirror_pdfs.py:   {"<pdf url>": {"path", "ids", "status"}}
Any PDF can also be matched by name: <reading-id>.pdf anywhere in the folder, or a file whose
last folders and name match the end of the reading's pdf_url (books/bad/chapter11.pdf). A bare
file name counts only when no other reading being warmed has a pdf_url ending in that name.

A file listed for several catalog ids is a whole-book PDF; it is never used for one chapter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class LocalPdf:
    path: Path
    root: Path
    source_url: str = ""
    matched_by: str = ""

    @property
    def label(self) -> str:
        return f"{self.root.name}/{self.path.relative_to(self.root).as_posix()}"


def url_tail(url: str, segments: int) -> str:
    parts = [p for p in unquote(urlsplit(url or "").path).lower().split("/") if p]
    return "/".join(parts[-segments:]) if len(parts) >= segments else ""


class PdfLibrary:
    def __init__(self, folders: list[Path]):
        self.folders = [Path(f) for f in folders if Path(f).is_dir()]
        self.by_id: dict[str, LocalPdf] = {}
        self.shared: dict[str, str] = {}  # reading id -> whole-book file that lists it
        self.shared_paths: set[Path] = set()
        self.by_tail: dict[str, LocalPdf] = {}
        self.by_name: dict[str, list[LocalPdf]] = {}
        for root in self.folders:
            self._load_manifest(root)
            for p in sorted(root.rglob("*")):
                if p.suffix.lower() != ".pdf" or not p.is_file() or p in self.shared_paths:
                    continue
                rel = PurePosixPath(p.relative_to(root).as_posix().lower())
                self.by_id.setdefault(p.stem, LocalPdf(p, root, matched_by="file name"))
                pdf = LocalPdf(p, root, matched_by="url path")
                for n in (2, 3):
                    if len(rel.parts) >= n:
                        self.by_tail.setdefault("/".join(rel.parts[-n:]), pdf)
                self.by_name.setdefault(rel.name, []).append(pdf)

    def _load_manifest(self, root: Path) -> None:
        mf = root / "manifest.json"
        if not mf.exists():
            return
        data = json.loads(mf.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            rows = [(f.get("path", ""), f.get("catalog_ids") or [], f.get("source_url") or f.get("file_url") or "")
                    for f in data["files"]]
        elif isinstance(data, dict):
            rows = [(v.get("path", ""), v.get("ids") or [], url) for url, v in data.items()
                    if isinstance(v, dict) and v.get("status", "ok") == "ok"]
        else:
            return
        for path, ids, source in rows:
            p = root / path
            if not path or not p.is_file():
                continue
            if len(ids) == 1:
                self.by_id[ids[0]] = LocalPdf(p, root, source, "manifest")
            else:
                self.shared_paths.add(p)
                for i in ids:
                    self.shared.setdefault(i, path)

    def find(self, reading_id: str, pdf_url: str = "", ambiguous_names: frozenset = frozenset()) -> LocalPdf | None:
        if reading_id in self.by_id:
            return self.by_id[reading_id]
        for n in (3, 2):
            tail = url_tail(pdf_url, n)
            if tail and tail in self.by_tail:
                return self.by_tail[tail]
        name = url_tail(pdf_url, 1)
        if name and name not in ambiguous_names and len(self.by_name.get(name, [])) == 1:
            return self.by_name[name][0]
        return None
