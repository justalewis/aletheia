import io

import pytest
import requests
from requests.structures import CaseInsensitiveDict
from pypdf import PdfWriter

from builder.reader_pdf import folio
from content.store import ContentStore


@pytest.fixture(autouse=True)
def content_store(settings, tmp_path):
    """A content cache in a temp folder for every test, so none reads the real backend/content_cache."""
    settings.STORAGES = {**settings.STORAGES, "content": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": tmp_path / "content", "allow_overwrite": True}}}
    return ContentStore()


def make_pdf(pages: int, size=(300, 400), label: str = "P") -> bytes:
    """A small PDF whose pages carry text (label + page number) so tests can spot them."""
    w = PdfWriter()
    for i in range(pages):
        page = w.add_blank_page(*size)
        folio(page, f"{label}{i + 1}")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class FakeSession:
    """Stands in for requests.Session: routes map URL -> (status, headers, body)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True, stream=False):
        self.calls.append(url)
        status, hdrs, body = self.routes.get(url, (404, {}, b"not found"))
        resp = requests.Response()
        resp.status_code = status
        resp.headers = CaseInsensitiveDict(hdrs)
        resp._content = body if isinstance(body, bytes) else body.encode("utf-8")
        resp._content_consumed = True
        resp.url = url
        resp.encoding = "utf-8"
        return resp
