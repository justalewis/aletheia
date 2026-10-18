"""The PDF renderers' URL fetcher: cache-only images, data: URIs, nothing from the network.

Needs WeasyPrint's system libraries (Pango), so it skips where they're missing; run it in the
Docker image or on the Fly machine.
"""
import pytest

from builder.reader_pdf import WeasyRenderer

try:  # importing weasyprint loads Pango through cffi
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher
except (ImportError, OSError):  # pragma: no cover - depends on system libraries
    pytest.skip("WeasyPrint system libraries not installed", allow_module_level=True)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4d80000000049454e44ae426082")


class FakeEntry:
    def image_bytes(self, name):
        return PNG


def renderer():
    return WeasyRenderer({"https://example.org/fig.png": (FakeEntry(), "fig.png")})


def test_fetcher_is_a_weasyprint_url_fetcher():
    assert isinstance(renderer().fetcher(), URLFetcher)


def test_cached_image_is_served_with_its_type():
    resp = renderer().fetcher()("https://example.org/fig.png")
    assert resp.content_type == "image/png"
    assert resp.read() == PNG


def test_data_uri_is_decoded():
    resp = renderer().fetcher()("data:text/plain;base64,aGk=")
    assert resp.read() == b"hi"


def test_anything_else_is_refused():
    with pytest.raises(ValueError):
        renderer().fetcher()("https://example.org/not-cached.png")


def test_render_with_cached_and_uncached_images():
    html = ('<p>Figure</p><img src="https://example.org/fig.png" alt="A figure">'
            '<img src="https://example.org/missing.png" alt="Missing">')
    rendered = renderer().render(html)
    assert rendered.pdf.startswith(b"%PDF-") and rendered.pages == 1
