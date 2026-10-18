"""Export the merged research catalog into the public rhet/comp corpus pack.

Reads   research/catalog/{readings,assignments,sources,commercial_reference}.json
Writes  corpus/rhetcomp/{readings,assignments,sources,commercial}.jsonl

The pack is what Aletheia loads. Ids written here are permanent: later harvests must
reuse them, so re-running this script only adds new ids and updates existing records.
Private fields (which files sit in the maintainer's Google Drive) never leave research/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "research" / "catalog"
PACK = ROOT / "corpus" / "rhetcomp"

PRIVATE = {"drive_file_id", "drive_path", "also_found_by"}
RENAME = {"source_agent": "provenance"}


def public(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if k in PRIVATE:
            continue
        out[RENAME.get(k, k)] = v
    return out


def load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {json.loads(line)["id"]: json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")


def export(name: str, out_name: str, transform=public) -> None:
    src = json.loads((CATALOG / f"{name}.json").read_text(encoding="utf-8"))
    path = PACK / f"{out_name}.jsonl"
    existing = load_existing(path)
    records = [transform(r) for r in src]
    new_ids = {r["id"] for r in records}
    dropped = sorted(set(existing) - new_ids)
    if dropped:
        # Ids are permanent; a record missing from the catalog is kept and marked, not deleted.
        for i in dropped:
            existing[i]["withdrawn"] = True
            records.append(existing[i])
    records.sort(key=lambda r: r["id"])
    write_jsonl(path, records)
    print(f"{out_name}: {len(records)} records ({len(new_ids - set(existing))} new, {len(dropped)} withdrawn)")


def commercial(record: dict) -> dict:
    # Title and publisher only: the pack must not reveal which files sit in anyone's Drive.
    notes = record.get("notes", "")
    m = re.search(r"(Bedford/St\. Martin's|Cambridge University Press|Modern Language Association|"
                  r"Wadsworth \(Cengage\)|Pearson|W\. W\. Norton)", notes)
    return {
        "id": re.sub(r"[^a-z0-9]+", "-", record["title"].lower().split(":")[0]).strip("-")[:60],
        "title": record["title"],
        "publisher": record.get("publisher") or (m.group(1).strip() if m else ""),
        "kind": "commercial-reference",
        "toc": [],
    }


def main() -> None:
    PACK.mkdir(parents=True, exist_ok=True)
    export("readings", "readings")
    export("assignments", "assignments")
    export("sources", "sources")
    export("commercial_reference", "commercial", commercial)


if __name__ == "__main__":
    main()
