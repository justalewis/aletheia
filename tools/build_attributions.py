"""Write corpus/files/ATTRIBUTIONS.md from every manifest.json under corpus/files/.

Each redistributed file gets a title, author, source, and license line (TASL), which CC
licenses require when copies are shared. Run after tools/mirror_pdfs.py (web) and
tools/import_corpus.py (Drive) have updated their manifests.

    python -I tools/build_attributions.py
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "corpus" / "files"


def pick(entry: dict, *keys: str) -> str:
    for k in keys:
        v = entry.get(k)
        if v:
            return "; ".join(v) if isinstance(v, list) else str(v)
    return ""


def load_entries() -> list[dict]:
    rows = []
    for manifest in sorted(FILES.rglob("manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        entries = data.get("files", data) if isinstance(data, dict) else data
        if isinstance(entries, dict):  # keyed by path
            entries = [{"path": k, **v} for k, v in entries.items()]
        base = manifest.parent
        for e in entries:
            rel = pick(e, "path", "dest", "file")
            full = (base / rel)
            if not rel or not full.exists():
                continue
            title = pick(e, "title", "book_title")
            if title.lower().startswith(("complete book", "full book", "entire book")) and e.get("book"):
                title = f"{e['book']} (complete book)"
            rows.append({
                "path": full.relative_to(FILES).as_posix(),
                "title": title,
                "authors": pick(e, "authors", "chapter_authors"),
                "license": pick(e, "license"),
                "license_url": pick(e, "license_url"),
                "source": pick(e, "source_url", "landing_url", "source", "url"),
                "bytes": e.get("bytes") or e.get("size") or full.stat().st_size,
            })
    rows.sort(key=lambda r: r["path"].lower())
    return rows


def main() -> None:
    rows = load_entries()
    total_mb = sum(int(r["bytes"]) for r in rows) / 1_048_576
    lines = [
        "# Attributions",
        "",
        f"This folder redistributes {len(rows)} openly licensed files ({total_mb:,.0f} MB) used by Aletheia's "
        "course readers. Each file is shared unchanged under its own license, listed below with its title, "
        "author, and source. Nothing here is relicensed by Aletheia.",
        "",
        f"Generated {date.today().isoformat()} by `tools/build_attributions.py` from the manifests in this folder.",
        "",
    ]
    folder = None
    for r in rows:
        top = r["path"].rsplit("/", 1)[0] if "/" in r["path"] else "."
        if top != folder:
            folder = top
            lines += ["", f"## {folder}", ""]
        credit = f"“{r['title'] or Path(r['path']).stem}”"
        if r["authors"]:
            credit += f" by {r['authors']}"
        credit += "."
        if r["source"]:
            credit += f" Source: <{r['source']}>."
        lic = r["license"] or "License: see source"
        if r["license_url"]:
            lic = f"[{lic}]({r['license_url']})"
        lines.append(f"- `{Path(r['path']).name}`: {credit} {lic}.")
    (FILES / "ATTRIBUTIONS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(rows)} files, {total_mb:,.0f} MB -> {FILES / 'ATTRIBUTIONS.md'}")


if __name__ == "__main__":
    main()
