import pytest

from catalog.management.commands.load_pack import parse_pages


@pytest.mark.parametrize("value,level,expected", [
    ("12", "chapter", 12),
    ("88-92", "chapter", 5),
    ("168–173", "chapter", 6),
    ("~277 words", "chapter", 1),
    ("~3,000 words", "article", 10),
    ("131 (start)", "chapter", None),
    ("515", "chapter", None),
    ("515", "book", 515),
    ("", "chapter", None),
    (None, "book", None),
    ("n/a", "chapter", None),
])
def test_parse_pages(value, level, expected):
    assert parse_pages(value, level) == expected
