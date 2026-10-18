"""The catalog lists readings from open textbooks first and scholarly monographs last."""
import pytest
from django.test import Client

from catalog.models import Pack, Reading

# (id, level, audience, course_level, parent_title), in the order Browse should list them.
EXPECTED = [
    ("ws-chapter-a", "chapter", "student", "100", "Writing Textbook"),
    ("ws-chapter-b", "chapter", "student", "100", "Writing Textbook"),
    ("reader-essay", "article", "student", "100", "88 Essays"),
    ("primary-text", "primary-text", "student", "mixed", ""),
    ("general-article", "article", "both", "mixed", ""),
    ("style-site", "website", "student", "100", ""),
    ("whole-textbook", "book", "both", "100", ""),
    ("keywords-entry", "chapter", "both", "300-400", "Keywords"),
    ("upper-book", "book", "both", "300-400", ""),
    ("grad-chapter", "chapter", "both", "grad", "An Edited Collection"),
    ("pedagogy-article", "article", "instructor", "mixed", ""),
    ("a-monograph", "book", "instructor", "grad", ""),
    ("teaching-toolkit", "book", "instructor", "mixed", ""),
]


@pytest.fixture
def catalog(db):
    pack = Pack.objects.create(id="t", name="Test", version="0", config={})
    # Created in reverse so the order can't come from insertion.
    for rid, level, audience, course_level, parent in reversed(EXPECTED):
        Reading.objects.create(id=rid, pack=pack, level=level, audience=audience, course_level=course_level,
                               parent_title=parent, title=f"Title {rid}", license="CC BY 4.0", reuse_tier="adapt",
                               license_verified=True, skins_text="|FYC|")
    return pack


def ids(**params):
    return [r["id"] for r in Client().get("/api/readings", params).json()["items"]]


def test_textbook_readings_come_first_and_monographs_last(catalog):
    assert ids() == [e[0] for e in EXPECTED]


def test_the_order_holds_under_filters_and_paging(catalog):
    assert ids(focus="FYC", level="book") == ["whole-textbook", "upper-book", "a-monograph", "teaching-toolkit"]
    assert ids(limit=3, offset=4) == ["general-article", "style-site", "whole-textbook"]
