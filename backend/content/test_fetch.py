import json

import pytest

from conftest import FakeSession
from content.fetch import (
    Client, FetchError, fetch_libretexts, fetch_pdf, fetch_pressbooks, fetch_wordpress, pressbooks_api_url,
)

ROBOTS_OK = (200, {}, "User-agent: *\nDisallow: /wp-admin/\n")


def client(routes):
    return Client(session=FakeSession(routes), delay=0, sleep=lambda s: None)


def test_pressbooks_api_url():
    assert pressbooks_api_url("https://press.example.org/mybook/chapter/the-slug/") == (
        "https://press.example.org/mybook/wp-json/pressbooks/v2/chapters?slug=the-slug&_fields=id,slug,link,title,content")
    assert "/front-matter?slug=intro" in pressbooks_api_url("https://x.org/b/front-matter/intro/")
    assert pressbooks_api_url("https://x.org/b/") is None
    assert pressbooks_api_url("https://openstax.org/books/x/pages/1-intro") is None


def test_fetch_pressbooks_returns_rendered_content():
    page = "https://press.example.org/mybook/chapter/the-slug/"
    api = pressbooks_api_url(page)
    body = [{"slug": "the-slug", "link": page, "title": {"rendered": "The &amp; Title"},
             "content": {"rendered": "<p>Chapter text</p>", "protected": False}}]
    c = client({"https://press.example.org/robots.txt": ROBOTS_OK, api: (200, {}, json.dumps(body))})
    f = fetch_pressbooks(c, page)
    assert f.html == "<p>Chapter text</p>" and f.fetcher == "pressbooks" and f.title == "The & Title"
    assert f.base_url == page


def test_wordpress_discovers_the_json_link():
    page = "https://journal.example.org/issue-1/article/"
    api = "https://journal.example.org/wp-json/wp/v2/posts/12"
    c = client({
        "https://journal.example.org/robots.txt": ROBOTS_OK,
        page: (200, {"Content-Type": "text/html", "Link": f'<{api}>; rel="alternate"; type="application/json"'},
               "<html><body>chrome</body></html>"),
        api: (200, {"Content-Type": "application/json"},
              json.dumps({"link": page, "title": {"rendered": "T"}, "content": {"rendered": "<p>Article</p>"}})),
    })
    assert fetch_wordpress(c, page).html == "<p>Article</p>"


def test_site_without_an_api_is_unsupported():
    page = "https://static.example.org/essay.html"
    c = client({"https://static.example.org/robots.txt": ROBOTS_OK,
                page: (200, {"Content-Type": "text/html"}, "<html><body><p>text</p></body></html>")})
    with pytest.raises(FetchError) as e:
        fetch_wordpress(c, page)
    assert e.value.status == "unsupported"


def test_libretexts_chapter_pulls_sections_in_order():
    ch = "https://human.libretexts.org/Bookshelves/X/07%3A_Chapter"
    s1, s2 = ch + "/7.01%3A_One", ch + "/7.02%3A_Two"
    listing = (f'<div class="mt-guide-content"><ul><li><a href="{s1}">7.1</a></li>'
               f'<li><a href="{s2}">7.2</a></li></ul></div>')
    c = client({
        "https://human.libretexts.org/robots.txt": (200, {}, "User-Agent: *\nCrawl-delay: 5\nDisallow: /@*\n"),
        ch: (200, {}, f'<section class="mt-content-container"><p>Intro</p>{listing}</section>'),
        s1: (200, {}, '<h1 id="title">7.1: One</h1><section class="mt-content-container"><p>First</p>'
                      '<img src="fig.png" alt="f"></section>'),
        s2: (200, {}, '<h1 id="title">7.2: Two</h1><section class="mt-content-container"><p>Second</p></section>'),
    })
    html = fetch_libretexts(c, ch).html
    assert html.index("Intro") < html.index("7.1: One") < html.index("First") < html.index("Second")
    assert "mt-guide-content" not in html
    assert f'src="{ch}/fig.png"' in html


def test_robots_txt_is_obeyed():
    c = client({"https://site.example.org/robots.txt": (200, {}, "User-agent: *\nDisallow: /private/\n")})
    with pytest.raises(FetchError) as e:
        c.get("https://site.example.org/private/page")
    assert e.value.status == "robots"
    assert c.session.calls == ["https://site.example.org/robots.txt"]


def test_crawl_delay_is_honored():
    waits = []
    c = Client(session=FakeSession({
        "https://slow.example.org/robots.txt": (200, {}, "User-agent: *\nCrawl-delay: 5\n"),
        "https://slow.example.org/a": (200, {}, "a"), "https://slow.example.org/b": (200, {}, "b")}),
        delay=0, sleep=waits.append)
    c.get("https://slow.example.org/a")
    c.get("https://slow.example.org/b")
    assert waits and max(waits) > 4


@pytest.mark.parametrize("status,headers,body,expected", [
    (403, {"cf-mitigated": "challenge"}, "<title>Just a moment...</title>", "bot-gated"),
    (503, {}, "<div id='challenge-platform'></div>", "bot-gated"),
    (403, {"Server": "CloudFront"}, "<h2>The request could not be satisfied.</h2>Request blocked.", "blocked"),
])
def test_bot_checks_stop_the_host(status, headers, body, expected):
    url = "https://gated.example.org/doc.pdf"
    c = client({"https://gated.example.org/robots.txt": ROBOTS_OK, url: (status, headers, body)})
    with pytest.raises(FetchError) as e:
        fetch_pdf(c, url)
    assert e.value.status == expected
    calls = len(c.session.calls)
    with pytest.raises(FetchError):
        fetch_pdf(c, "https://gated.example.org/other.pdf")  # the host isn't asked again
    assert len(c.session.calls) == calls


def test_wac_docs_are_never_requested():
    c = client({})
    with pytest.raises(FetchError) as e:
        fetch_pdf(c, "https://wacclearinghouse.org/docs/books/bad/chapter11.pdf")
    assert e.value.status == "bot-gated"
    assert c.session.calls == []


def test_challenge_page_served_as_200_is_not_a_pdf():
    url = "https://cdn.example.org/file.pdf"
    c = client({"https://cdn.example.org/robots.txt": ROBOTS_OK,
                url: (200, {"Content-Type": "text/html"}, "<html>Verify you are human</html>")})
    with pytest.raises(FetchError) as e:
        fetch_pdf(c, url)
    assert e.value.status == "bot-gated"
