"""Find one chapter's pages in a collection published only as a whole-book PDF.

Bad Ideas About Writing, for one, exists as a single 382-page PDF. A chapter's own pages are
copied out unchanged, which every CC license allows, NoDerivatives included: the licenses let
anyone share the work "in whole or in part," and a run of the book's pages is a part of it, not
an adaptation.

The book's printed contents give every chapter's first page. A chapter is found by its title at
the top of a page, the running heads tie PDF pages to printed page numbers, and the chapter runs
to the page before the next entry in the contents. Blank pages and part-title pages at the end
are left off. When any of those checks fails the chapter stays a link: FetchError, never a guess.
"""
from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import cached_property

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from content.fetch import FetchError

CONTENTS_SCAN = 30          # the contents are somewhere in the first pages
TITLE_WINDOW = 40           # a title must start this close to the top (after a running head)
ENTRY = re.compile(r"\S.*?\s(\d{1,4})\s*$")
HEAD_NUMBER = (re.compile(r"^\s*(\d{1,4})\s+\D"), re.compile(r"\D\s+(\d{1,4})\s*$"))


def norm(text: str) -> str:
    """Letters and digits only, lowercased: "Popular Culture … a T ext" == "popularculture…atext"."""
    text = unicodedata.normalize("NFKD", text).lower()
    return re.sub(r"[^a-z0-9]", "", text)


@dataclass(frozen=True)
class Chapter:
    first: int          # PDF page indexes, from 0
    last: int
    printed_first: int
    printed_last: int

    @property
    def pages(self) -> int:
        return self.last - self.first + 1


class BookPdf:
    def __init__(self, data: bytes):
        try:
            self.reader = PdfReader(io.BytesIO(data))
            if self.reader.is_encrypted and not self.reader.decrypt(""):
                raise FetchError("encrypted", "the book PDF needs a password")
            self.count = len(self.reader.pages)
        except (PdfReadError, ValueError, KeyError) as e:
            raise FetchError("bad-pdf", f"{type(e).__name__}: {e}") from e
        self._texts: dict[int, str] = {}

    def text(self, i: int) -> str:
        if i not in self._texts:
            try:
                self._texts[i] = self.reader.pages[i].extract_text() or ""
            except Exception:  # pypdf raises a zoo of errors on odd fonts; an unreadable page is blank
                self._texts[i] = ""
        return self._texts[i]

    @cached_property
    def contents(self) -> tuple[set[int], set[int]]:
        """(printed start pages listed in the contents, PDF indexes of the contents pages)."""
        numbers, pages = set(), set()
        for i in range(min(CONTENTS_SCAN, self.count)):
            lines = self.text(i).splitlines()
            found = [int(m.group(1)) for line in lines if (m := ENTRY.match(line.strip()))]
            if not pages:
                if "contents" not in norm(" ".join(lines[:5])) or len(found) < 3:
                    continue
            elif not found or found[0] <= max(numbers):  # the contents ran out
                break
            numbers.update(found)
            pages.add(i)
        return numbers, pages

    def chapter(self, title: str, start_hint: int | None = None, expected_pages: int | None = None) -> Chapter:
        """`start_hint`: the PDF page index a #page= link points at; `expected_pages`: the catalog's count."""
        numbers, contents_pages = self.contents
        if not numbers:
            raise FetchError("whole-book-pdf", "no printed contents found to place the chapter")
        want = norm(title)
        if len(want) < 8:
            raise FetchError("whole-book-pdf", f"title {title!r} is too short to find safely")
        starts = [i for i in range(self.count) if i not in contents_pages and self._starts_with(i, want)]
        if len(starts) != 1:
            raise FetchError("whole-book-pdf", f"{len(starts)} pages start with the title {title!r}")
        first = starts[0]
        if start_hint is not None and start_hint != first:
            raise FetchError("whole-book-pdf", f"the title is on PDF page {first + 1}, the link says {start_hint + 1}")
        offset = self._offset(first)
        printed_first = first - offset
        if printed_first not in numbers:
            raise FetchError("whole-book-pdf", f"printed page {printed_first} isn't a start page in the contents")
        later = [n for n in numbers if n > printed_first]
        if not later:
            raise FetchError("whole-book-pdf", "the last entry in the contents; its end isn't marked")
        last = min(min(later) + offset - 1, self.count - 1)
        untrimmed = last - first + 1
        while last > first and self._filler(last):
            last -= 1
        if expected_pages and expected_pages not in (untrimmed, last - first + 1):
            raise FetchError("whole-book-pdf",
                             f"found {last - first + 1} pages (printed {printed_first}-{last - offset}), "
                             f"the catalog says {expected_pages}")
        return Chapter(first, last, printed_first, last - offset)

    def extract(self, ch: Chapter) -> bytes:
        writer = PdfWriter()
        for i in range(ch.first, ch.last + 1):
            writer.add_page(self.reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def _starts_with(self, i: int, want: str) -> bool:
        top = norm(" ".join(self.text(i).splitlines()[:8]))
        return 0 <= top.find(want) <= TITLE_WINDOW

    def _offset(self, first: int) -> int:
        """PDF index minus printed page number, read from the running heads near `first`.

        A chapter's opening page often has no running head, so the next few pages are read too,
        and two of them have to agree.
        """
        seen = Counter()
        for i in range(first, min(first + 6, self.count)):
            lines = [ln for ln in self.text(i).splitlines() if ln.strip()]
            for line in (lines[:1] + lines[-1:]) if lines else []:
                for pattern in HEAD_NUMBER:
                    if m := pattern.search(line):
                        seen[i - int(m.group(1))] += 1
                        break
        if seen and (best := seen.most_common(1)[0])[1] >= 2:
            return best[0]
        raise FetchError("whole-book-pdf", "no running heads with page numbers to place the chapter")

    def _filler(self, i: int) -> bool:
        """A blank page or a part-title page (a line or two of capitals), with no images.
        A trailing page that holds a figure is the chapter's, so it stays."""
        text = self.text(i).strip()
        if not (len(norm(text)) < 20 or (not any(c.islower() for c in text) and len(text.splitlines()) <= 3)):
            return False
        try:
            return not self.reader.pages[i].images
        except Exception:
            return False
