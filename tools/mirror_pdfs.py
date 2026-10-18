"""Mirror the corpus's openly licensed PDFs into a folder (normally Google Drive for Desktop).

Picks every reading that
  - has a pdf_url,
  - may be redistributed (reuse tier adapt, adapt-nc, or verbatim) with a verified license,
  - isn't already in the maintainer's Drive corpus (from the private research/catalog/readings.json),
  - and whose PDF link checked out live (bot-gated, blocked, and dead links are skipped).

Files land in <dest>/<collection>/<title>.pdf, and <dest>/_attributions.csv records title,
author, source, and license for every file (TASL), which CC licenses require when copies are
shared. Re-running skips files already downloaded.

Usage:
  python -I tools/mirror_pdfs.py --dry-run
  python -I tools/mirror_pdfs.py --dest "G:/My Drive/Comp Textbook Readings/OA PDFs"
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import string
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

import requests

ROOT = Path(__file__).resolve().parent.parent
READINGS = ROOT / "corpus" / "rhetcomp" / "readings.jsonl"
LINK_CHECK = ROOT / "research" / "link_check.json"
MANIFEST = ROOT / "research" / "pdf_mirror.json"
PRIVATE_CATALOG = ROOT / "research" / "catalog" / "readings.json"

OK_TIERS = {"adapt", "adapt-nc", "verbatim"}
OK_LINK = {"live", "redirected"}
MAX_BYTES = 200 * 1024 * 1024
HOST_DELAY = 2.0
USER_AGENT = "Aletheia-mirror/0.1 (open-access course reader project; polite single-threaded fetch)"


def find_drive_dest() -> Path | None:
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:/My Drive/Comp Textbook Readings")
        if p.exists():
            return p / "OA PDFs"
    return None


def safe_name(s: str, limit: int = 120) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s or "untitled").strip().rstrip(".")
    return re.sub(r"\s+", " ", s)[:limit] or "untitled"


def link_status() -> dict[str, str]:
    if not LINK_CHECK.exists():
        return {}
    data = json.loads(LINK_CHECK.read_text(encoding="utf-8"))
    return {r["url"]: r.get("status", "") for r in data.get("results", [])}


def already_in_drive() -> set[str]:
    if not PRIVATE_CATALOG.exists():
        return set()
    return {r["id"] for r in json.loads(PRIVATE_CATALOG.read_text(encoding="utf-8")) if r.get("drive_file_id")}


def candidates() -> list[dict]:
    status = link_status()
    in_drive = already_in_drive()
    by_url: dict[str, list[dict]] = defaultdict(list)
    for line in READINGS.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        url = (r.get("pdf_url") or "").strip()
        if not url or r.get("withdrawn") or r["id"] in in_drive:
            continue
        if r.get("reuse_tier") not in OK_TIERS or not r.get("license_verified"):
            continue
        if status.get(url) not in OK_LINK:
            continue
        by_url[urldefrag(url)[0]].append(r)
    picks = []
    for url, recs in by_url.items():
        # Several chapter records can share one whole-book PDF; file it under the book.
        whole = next((r for r in recs if r.get("level") in ("book", "collection")), None)
        r = whole or recs[0]
        shared = len(recs) > 1 and not whole
        title = r.get("parent_title") if shared and r.get("parent_title") else r.get("title")
        collection = r.get("parent_title") or r.get("publisher_platform") or "Other"
        if r.get("level") in ("book", "collection"):
            collection = r.get("publisher_platform") or "Books"
        picks.append({
            "url": url,
            "ids": [x["id"] for x in recs],
            "title": title,
            "authors": r.get("authors", ""),
            "license": r.get("license", ""),
            "license_url": r.get("license_url", ""),
            "landing_url": r.get("landing_url") or r.get("html_url") or url,
            "collection": collection,
        })
    picks.sort(key=lambda p: (p["collection"], p["title"]))
    return picks


def download(sess: requests.Session, url: str, path: Path) -> tuple[str, int, str]:
    with sess.get(url, stream=True, timeout=60, allow_redirects=True) as resp:
        if resp.status_code != 200:
            return f"http-{resp.status_code}", 0, ""
        tmp = path.with_suffix(".part")
        h, size, first = hashlib.sha256(), 0, b""
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(1 << 16):
                if not first:
                    first = chunk[:5]
                size += len(chunk)
                if size > MAX_BYTES:
                    fh.close()
                    tmp.unlink(missing_ok=True)
                    return "too-large", size, ""
                h.update(chunk)
                fh.write(chunk)
        if first != b"%PDF-":
            tmp.unlink(missing_ok=True)
            return "not-a-pdf", size, ""
        tmp.replace(path)
        return "ok", size, h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", help="target folder (default: <Drive>/Comp Textbook Readings/OA PDFs)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    picks = candidates()
    print(f"{len(picks)} PDFs to mirror")
    if args.dry_run:
        by = defaultdict(int)
        for p in picks:
            by[p["collection"]] += 1
        for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:25]:
            print(f"  {v:4d}  {k}")
        return

    dest = Path(args.dest) if args.dest else find_drive_dest()
    if not dest:
        sys.exit("No destination: install Google Drive for Desktop or pass --dest.")
    dest.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT
    last_hit: dict[str, float] = {}

    for n, p in enumerate(picks[: args.limit or None], 1):
        folder = dest / safe_name(p["collection"], 80)
        path = folder / (safe_name(p["title"]) + ".pdf")
        if path.exists() or manifest.get(p["url"], {}).get("status") == "ok":
            continue
        folder.mkdir(parents=True, exist_ok=True)
        host = urlsplit(p["url"]).netloc
        wait = HOST_DELAY - (time.time() - last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            status, size, digest = download(sess, p["url"], path)
        except requests.RequestException as e:
            status, size, digest = f"error: {type(e).__name__}", 0, ""
        last_hit[host] = time.time()
        manifest[p["url"]] = {"status": status, "path": str(path.relative_to(dest)), "bytes": size,
                              "sha256": digest, "ids": p["ids"]}
        print(f"[{n}/{len(picks)}] {status:10s} {path.relative_to(dest)}")
        if n % 10 == 0:
            MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    with (dest / "_attributions.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "title", "authors", "source", "license", "license_url"])
        for p in picks:
            m = manifest.get(p["url"], {})
            if m.get("status") == "ok":
                w.writerow([m["path"], p["title"], p["authors"], p["landing_url"], p["license"], p["license_url"]])
    ok = sum(1 for m in manifest.values() if m.get("status") == "ok")
    print(f"done: {ok} files in {dest}")


if __name__ == "__main__":
    main()
