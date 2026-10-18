"""Copy the openly licensed files from a Google Drive download into corpus/files/drive/.

Usage
    python -I tools\\import_corpus.py "C:\\path\\to\\unzipped download" [more folders] [--dry-run]

Download the "Comp Textbook Readings" folder from drive.google.com (right-click > Download).
Drive zips it, sometimes into several zips; unzip them all somewhere outside this project and
pass the folder(s). The script finds each file listed in research/drive_inventory.json by its
Drive path and checks that its size matches the inventory. It copies a file only if
  - the inventory classifies it "oa" (openly licensed),
  - its catalog records (or, for front matter and whole-volume PDFs, its book's record) have a
    verified license and none is link-only, and
  - it is no larger than 95 MB (GitHub rejects files over 100 MB).
Commercial, free-access and personal files are never copied, and files the inventory doesn't
know about are reported, not copied.

Writes (default --dest corpus/files/drive; corpus/files/web/ holds mirrored web PDFs, and
corpus/rhetcomp/ is the app's corpus pack, so this script touches neither)
    <dest>/<drive path>     the openly licensed files, keeping Drive's folder structure
    <dest>/manifest.json    one entry per file: path (relative to <dest>), bytes, sha256, title,
                            book, authors, license, license_url, license_from, catalog_ids,
                            source_url. No Drive ids, since the repo is public.
    <dest>/.gitignore       ignores everything in <dest> except manifest.json and the listed
                            files, so nothing else can be committed by accident

Re-running is safe: unchanged files are skipped, and files imported by an earlier run stay in
the manifest even if this download doesn't include them (useful when Drive splits the zip). The
script refuses a non-empty destination it didn't create, so its .gitignore can't hide other files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "research" / "drive_inventory.json"
READINGS = ROOT / "research" / "catalog" / "readings.json"
DEFAULT_DEST = ROOT / "corpus" / "files" / "drive"
OPEN_CLASS = "oa"
GOOGLE_NATIVE = "application/vnd.google-apps"
MAX_BYTES = 95 * 1024 * 1024  # GitHub rejects files over 100 MB

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_catalog import as_bool, norm_text, restriction_key  # noqa: E402


def norm(p: str) -> str:
    return p.replace("\\", "/").strip("/").lower()


def loose(p: str) -> str:
    """Path key that survives the character substitutions Drive and unzip tools make."""
    return re.sub(r"[^a-z0-9/]", "", norm(p))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(sources: list[Path]) -> list[dict]:
    found = []
    for src in sources:
        for p in src.rglob("*"):
            if p.is_file():
                rel = p.relative_to(src).as_posix()
                found.append({"path": p, "rel": norm(rel), "loose": loose(rel), "size": p.stat().st_size,
                              "shown": f"{src.name}/{rel}"})
    return found


def find(entry: dict, downloaded: list[dict]) -> tuple[dict | None, str, list[dict]]:
    """The downloaded file for an inventory entry: same Drive path (any wrapping folders), same size.

    Also returns every file at that path, so a size mismatch isn't reported as an unknown file.
    """
    want, size = norm(entry["path"]), int(entry.get("size_bytes") or -1)
    by_path = [d for d in downloaded if d["rel"] == want or d["rel"].endswith("/" + want)]
    if not by_path:
        lw = loose(entry["path"])
        by_path = [d for d in downloaded if d["loose"] == lw or d["loose"].endswith("/" + lw)]
    if entry.get("mime_type", "").startswith(GOOGLE_NATIVE):
        exports = google_exports(entry, downloaded)
        return None, "Google Docs file: Drive exports it with a new size, so it can't be verified", exports
    same_size = [d for d in by_path if d["size"] == size]
    if same_size:
        return same_size[0], "", by_path
    if by_path:
        return None, (f"size differs from the inventory ({by_path[0]['size']:,} vs {size:,} bytes); "
                      f"if the file changed in Drive, re-run the inventory"), by_path
    return None, "not in this download", []


def google_exports(entry: dict, downloaded: list[dict]) -> list[dict]:
    """Exports of a Google Docs/Sheets file (same name, any extension), so they aren't reported as unknown."""
    want = loose(entry["path"])
    out = []
    for d in downloaded:
        stem = loose(d["rel"].rsplit(".", 1)[0])
        if stem == want or stem.endswith("/" + want):
            out.append(d)
    return out


def gitignore_line(path: str) -> str:
    escaped = re.sub(r"([\[\]*?\\])", r"\\\1", path)
    if escaped.endswith(" "):
        escaped = escaped[:-1] + "\\ "
    return "!/" + escaped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+", type=Path, help="unzipped Drive download folder(s)")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="target folder (default: corpus/files/drive)")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
    args = ap.parse_args()

    dest = args.dest.resolve()
    for src in args.sources:
        if src.suffix.lower() == ".zip":
            sys.exit(f"{src} is a zip file; unzip it first and pass the folder.")
        if not src.is_dir():
            sys.exit(f"{src} is not a folder.")
        if src.resolve() == dest or dest in src.resolve().parents:
            sys.exit(f"{src} is inside {dest}; unzip the download somewhere else.")
        if ROOT in src.resolve().parents:
            print(f"note: {src} is inside the project; keep it out of commits (the root .gitignore blocks PDFs).")

    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    books = {b.get("folder_path", ""): b for b in inv.get("books", [])}
    files = [f for f in inv.get("files", []) if not f.get("is_folder")]
    records: dict[str, list[dict]] = defaultdict(list)
    book_records: dict[str, dict] = {}
    for r in json.loads(READINGS.read_text(encoding="utf-8")):
        if r.get("drive_file_id"):
            records[r["drive_file_id"]].append(r)
        if r.get("level") in ("book", "collection"):
            book_records.setdefault(norm_text(r.get("title", "")), r)

    manifest_path = dest / "manifest.json"
    if dest.exists() and any(dest.iterdir()) and not manifest_path.exists():
        sys.exit(f"{dest} already holds files this script didn't write; its .gitignore would hide them. "
                 f"Pick an empty or new folder.")
    old = {}
    if manifest_path.exists():
        old = {e["path"]: e for e in json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])}

    downloaded = scan(args.sources)
    claimed: set[Path] = set()
    entries: list[dict] = []
    copied = unchanged = kept = 0
    closed, held, missing = [], [], []

    for f in files:
        match, why, seen = find(f, downloaded)
        claimed.update(d["path"] for d in seen)
        if f.get("classification") != OPEN_CLASS:
            closed.append(f)
            continue
        folder = f["path"].rsplit("/", 1)[0] if "/" in f["path"] else ""
        book = books.get(folder, {})
        # The file's own catalog records govern; front matter and whole-volume PDFs fall back to
        # their book's record, then to the inventory's book-level license check.
        recs = records.get(f["drive_id"], [])
        book_rec = book_records.get(norm_text(book.get("title") or f.get("book_title", ""))) if not recs else None
        governing = recs or ([book_rec] if book_rec else [])
        if any(r["reuse_tier"] == "link-only" for r in governing):
            held.append((f, "its catalog record is link-only"))
            continue
        if governing and not all(r.get("license_verified") for r in governing):
            held.append((f, "its catalog license isn't verified"))
            continue
        if not governing and not (as_bool(book.get("license_verified")) and (f.get("license") or book.get("license"))):
            held.append((f, "no catalog record and no verified license in the inventory"))
            continue
        if int(f.get("size_bytes") or 0) > MAX_BYTES:
            held.append((f, "larger than 95 MB"))
            continue
        target = dest.joinpath(*f["path"].split("/"))
        if not match:
            prev = old.get(f["path"])
            if prev and target.exists() and target.stat().st_size == prev["bytes"]:
                entries.append(prev)
                kept += 1
            else:
                missing.append((f, why))
            continue

        digest = sha256(match["path"])
        if target.exists() and target.stat().st_size == match["size"] and sha256(target) == digest:
            unchanged += 1
        else:
            copied += 1
            if not args.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(match["path"], target)

        if governing:
            rec = max(governing, key=lambda r: restriction_key(r["license_family"]))
            lic, lic_url = rec["license"], rec.get("license_url", "")
            lic_from = "catalog" if recs else "catalog (book record)"
            source = rec.get("html_url") or rec.get("landing_url") or ""
        else:
            lic, lic_url, lic_from = f.get("license") or book.get("license", ""), "", "drive inventory"
            source = book.get("license_source_url", "")
        authors = f.get("chapter_authors") or book.get("authors") or ""
        entries.append({
            "path": f["path"],
            "bytes": match["size"],
            "sha256": digest,
            "title": f.get("chapter_title") or f.get("book_title") or f.get("name", ""),
            "book": f.get("book_title", ""),
            "authors": authors if isinstance(authors, list) else [a for a in [authors] if a],
            "license": lic,
            "license_url": lic_url,
            "license_from": lic_from,
            "catalog_ids": sorted(r["id"] for r in governing),
            "source_url": source,
        })

    unknown = [d for d in downloaded if d["path"] not in claimed]
    entries.sort(key=lambda e: e["path"].lower())
    listed = {e["path"] for e in entries}
    stale = []
    if dest.exists():
        for p in dest.rglob("*"):
            rel = p.relative_to(dest).as_posix()
            if p.is_file() and rel not in listed and rel not in ("manifest.json", ".gitignore"):
                stale.append(rel)

    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        manifest = {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "about": ("Openly licensed files from the maintainer's reading collection, redistributed unchanged "
                      "under their own licenses (each PDF carries its license notice). Commercial, free-access "
                      "and personal files are not included. Written by tools/import_corpus.py."),
            "count": len(entries),
            "bytes": sum(e["bytes"] for e in entries),
            "files": entries,
        }
        manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        lines = ["# Written by tools/import_corpus.py. Everything in corpus/ is ignored except the files",
                 "# listed in manifest.json, so non-open files can't be committed by accident.",
                 "*", "!*/", "!.gitignore", "!manifest.json"] + [gitignore_line(e["path"]) for e in entries]
        (dest / ".gitignore").write_text("\n".join(lines) + "\n", encoding="utf-8")

    verb = "would copy" if args.dry_run else "copied"
    print(f"{len(downloaded)} files in the download; {len(files)} in the Drive inventory.")
    print(f"Open files: {verb} {copied}, unchanged {unchanged}, kept from earlier runs {kept}; "
          f"manifest lists {len(entries)} ({sum(e['bytes'] for e in entries) / 2**20:.0f} MB).")
    print(f"Not copied: {len(closed)} commercial/free-access/personal files (by inventory classification).")
    for f, why in held:
        print(f"  held back: {f['path']} ({why})")
    for f, why in missing:
        print(f"  missing: {f['path']} ({why})")
    for d in unknown:
        print(f"  not in the inventory, not copied: {d['shown']}")
    if unknown:
        print("  (re-run the Drive inventory to classify new files before importing them)")
    for rel in stale:
        print(f"  in {dest.name}/ but not in the manifest (ignored by git; delete it if unwanted): {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
