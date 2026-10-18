"""License report and TASL attribution lines for a build."""
from collections import Counter


def attribution(item, kind: str = "reading") -> str:
    """Title, Author, Source, License, in the order CC recommends."""
    parts = [f"“{item.title}”"]
    if item.authors:
        parts.append(f"by {item.authors}")
    parent = getattr(item, "parent_title", "") or getattr(item, "source_repository", "")
    if parent:
        parts.append(f"in {parent}")
    if item.year:
        parts.append(f"({item.year})")
    line = " ".join(parts) + "."
    if item.effective_tier == "link-only":
        status = "All rights reserved by the rights holder; linked, not copied." if item.license_verified else \
            "License not yet verified; linked, not copied."
        return f"{line} {status}"
    lic = item.license or "license unknown"
    if lic.lower().startswith("public domain"):
        return f"{line} {lic}."
    return f"{line} Licensed under {lic}."


def license_report(readings: list, assignments: list) -> dict:
    items = list(readings) + list(assignments)
    tiers = Counter(i.effective_tier for i in items)
    families = Counter(i.license_family or "Unknown" for i in items)
    nc = [i for i in items if "NC" in (i.license_family or "") and i.effective_tier != "link-only"]
    nd = [i for i in items if (i.effective_tier == "verbatim")]
    sa = [i for i in items if (i.license_family or "").endswith("-SA") or i.license_family == "CC BY-SA"]
    unverified = [i for i in items if not i.license_verified]
    statements = []
    if items:
        statements.append(
            "Every included item carries a title, author, source, and license credit. Your own selection and "
            "arrangement of the reader can be shared under CC BY 4.0; each item keeps its own license.")
    if nc:
        statements.append(
            f"{len(nc)} item(s) are NonCommercial. Aletheia readers are free and never sold, so this is fine. "
            "A campus copy center may print the reader and charge only its cost; selling it for profit is not allowed.")
    if nd:
        statements.append(
            f"{len(nd)} item(s) are NoDerivatives. They are included whole and unchanged; don't excerpt or edit them.")
    if sa:
        statements.append(
            f"{len(sa)} item(s) are ShareAlike. If you adapt one, your adapted version must carry the same license.")
    link_only = tiers.get("link-only", 0)
    if link_only:
        statements.append(
            f"{link_only} item(s) are linked, not copied: they're free to read but all rights reserved, "
            "or their license hasn't been verified yet. Students follow the link to read them.")
    return {
        "total": len(items),
        "tiers": dict(tiers),
        "families": dict(families),
        "noncommercial": len(nc),
        "noderivatives": len(nd),
        "sharealike": len(sa),
        "unverified": [i.id for i in unverified],
        "statements": statements,
    }
