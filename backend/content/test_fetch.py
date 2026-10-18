import json

import pytest

from conftest import FakeSession
from content.fetch import (
    Client, FetchError, Robots, fetch_libretexts, fetch_manifold, fetch_openstax, fetch_pdf, fetch_pressbooks,
    fetch_wordpress, pressbooks_api_url,
)
from content.sanitize import sanitize

ROBOTS_OK = (200, {}, "User-agent: *\nDisallow: /wp-admin/\n")
# What pressbooks.pub hosts serve: the REST API is closed to crawlers, pages are open.
ROBOTS_PRESSBOOKS = (200, {}, "User-agent: *\nDisallow: /wp-json/oembed/\nDisallow: /*/wp-json/\nDisallow: /*/?s=\n")


def client(routes):
    return Client(session=FakeSession(routes), delay=0, sleep=lambda s: None)


def test_robots_wildcards_anchors_and_groups():
    r = Robots(ROBOTS_PRESSBOOKS[2])
    assert not r.allowed("https://kpu.pressbooks.pub/businesswriting/wp-json/pressbooks/v2/chapters?slug=x")
    assert r.allowed("https://kpu.pressbooks.pub/businesswriting/chapter/writing-memos/")
    assert not r.allowed("https://kpu.pressbooks.pub/businesswriting/?s=memo")
    r = Robots("User-agent: *\nDisallow: /*.pdf$\nAllow: /books/*.pdf$\nDisallow: /apps/archive\n")
    assert not r.allowed("https://x.org/files/a.pdf") and r.allowed("https://x.org/files/a.pdf?download=1")
    assert r.allowed("https://x.org/books/b.pdf")  # the longer rule wins
    assert not r.allowed("https://x.org/apps/archive/1.2/contents/x.json")
    assert r.allowed("https://x.org/apps/image-cdn/v1/f=webp/apps/archive/1.2/resources/abc")
    # A group naming this crawler replaces the * group; groups naming other bots don't apply.
    r = Robots("User-agent: ClaudeBot\nUser-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private/\n"
               "\nUser-agent: Aletheia/2\nDisallow: /drafts/\nCrawl-delay: 3\n")
    assert r.allowed("https://x.org/private/a") and not r.allowed("https://x.org/drafts/a") and r.delay == 3
    assert Robots("User-agent: GPTBot\nDisallow: /\n").allowed("https://x.org/read/a")


def test_pressbooks_reads_the_page_when_robots_closes_the_api():
    page = "https://kpu.pressbooks.pub/businesswriting/chapter/writing-memos/"
    html = ('<html><body><main><section data-type="chapter" class="chapter"><header><h1 class="entry-title">'
            'Writing Memos</h1></header><p>A memo is <a href="../netiquette/">short</a>.</p></section></main>'
            '<nav>contents</nav></body></html>')
    c = client({"https://kpu.pressbooks.pub/robots.txt": ROBOTS_PRESSBOOKS,
                page: (200, {"Content-Type": "text/html; charset=UTF-8"}, html)})
    f = fetch_pressbooks(c, page)
    assert f.title == "Writing Memos" and "entry-title" not in f.html and "contents" not in f.html
    assert 'href="https://kpu.pressbooks.pub/businesswriting/chapter/netiquette/"' in f.html
    assert f.source_url == page and not any("wp-json" in u for u in c.session.calls)


def test_pressbooks_part_brings_its_chapters_in_contents_order():
    base = "https://kpu.pressbooks.pub/businesswriting"
    part = f"{base}/part/chapter-6-writing-emails/"
    ch_a, ch_b, other = f"{base}/chapter/reviewing-cmapp/", f"{base}/chapter/writing-memos/", f"{base}/chapter/x/"
    toc = (f'<ol class="toc"><li class="toc__part"><div class="toc__title__container"><a href="{base}/part/ch-5/">'
           f'Chapter 5</a></div><ol class="toc__chapters"><li class="toc__chapter"><p class="toc__title">'
           f'<a href="{other}">X</a></p></li></ol></li>'
           f'<li class="toc__part"><div class="toc__title__container"><a href="{part}">Chapter 6</a></div>'
           f'<ol class="toc__chapters"><li class="toc__chapter"><p class="toc__title"><a href="{ch_a}">Reviewing'
           f'</a></p><ul><li><a href="{ch_a}#section-1">1</a></li></ul></li><li class="toc__chapter">'
           f'<p class="toc__title"><a href="{ch_b}">Memos</a></p></li></ol></li></ol>')

    def chapter(title, body):
        return (200, {"Content-Type": "text/html; charset=UTF-8"},
                f'<section data-type="chapter"><header><h1>{title}</h1></header>{body}</section>')

    c = client({"https://kpu.pressbooks.pub/robots.txt": ROBOTS_PRESSBOOKS,
                part: (200, {"Content-Type": "text/html; charset=UTF-8"},
                       f'<nav>{toc}</nav><section data-type="part"><header><h1>Chapter 6: Writing Emails</h1>'
                       '</header><p>In this section you will</p></section>'),
                ch_a: chapter("Reviewing CMAPP", "<p>CMAPP text</p>"),
                ch_b: chapter("Writing Memos", "<h2><span>W</span>riting Memos</h2><p>Memo text</p>")})
    f = fetch_pressbooks(c, part)
    assert f.title == "Chapter 6: Writing Emails"
    h = f.html
    assert h.index("In this section") < h.index("<h2>Reviewing CMAPP</h2>") < h.index("CMAPP text") \
        < h.index("riting Memos</h2>") < h.index("Memo text")
    assert "<h2>Writing Memos</h2>" not in h  # the chapter's own title isn't doubled
    assert other not in c.session.calls and c.session.calls.count(ch_a) == 1


def test_pressbooks_part_without_contents_is_not_cached_as_its_intro():
    part = "https://press.example.org/book/part/part-1/"
    c = client({"https://press.example.org/robots.txt": ROBOTS_OK,
                part: (200, {}, '<section data-type="part"><p>Part intro</p></section>')})
    with pytest.raises(FetchError) as e:
        fetch_pressbooks(c, part)
    assert e.value.status == "unsupported"


def test_manifold_section_brings_its_subsections():
    host = "https://manifold.example.edu"
    page = f"{host}/read/howdy/section/s17"
    toc = [{"id": "s16", "label": "16", "children": None},
           {"id": "s17", "label": "17 Instructions", "children": [
               {"id": "s17-1", "label": "17.1", "children": [{"id": "s17-1", "anchor": "a1", "children": None}]},
               {"id": "s17-2", "label": "17.2", "children": None}]}]
    api = f"{host}/api/v1/texts/howdy"

    def section(name, body):
        return (200, {"Content-Type": "application/vnd.api+json"},
                json.dumps({"data": {"type": "textSections", "attributes": {"name": name, "body": body}}}))

    c = client({
        f"{host}/robots.txt": (404, {}, "not found"),
        api: (200, {}, json.dumps({"data": {"type": "texts", "attributes": {"toc": toc, "spine": ["s16", "s17"]}}})),
        f"{api}/relationships/text_sections/s17": section("17 Instructions", "<!DOCTYPE html><section><h1>17</h1></section>"),
        f"{api}/relationships/text_sections/s17-1": section("17.1", '<section><h1>17.1</h1><p>One</p>'
                                                                    '<img src="/api/proxy/ingestion_sources/i1"></section>'),
        f"{api}/relationships/text_sections/s17-2": section("17.2", "<section><h1>17.2</h1><p>Two</p></section>"),
    })
    f = fetch_manifold(c, page)
    assert f.fetcher == "manifold" and f.title == "17 Instructions" and f.base_url == page
    assert f.html.index("<h1>17</h1>") < f.html.index("One") < f.html.index("Two") and "DOCTYPE" not in f.html
    assert f'src="{host}/api/proxy/ingestion_sources/i1"' in f.html
    assert f"{api}/relationships/text_sections/s16" not in c.session.calls


def test_openstax_introduction_brings_the_whole_chapter():
    base = "https://openstax.org/books/writing-guide/pages"
    tree = {"contents": [{"slug": "preface", "title": "Preface"}, {"slug": "unit-3", "title": "Unit", "contents": [
        {"slug": "3-unit-introduction", "title": "Unit Introduction"},
        {"slug": "20-portfolio", "title": "<span>20</span> Portfolio Reflection", "contents": [
            {"slug": "20-introduction"}, {"slug": "20-1-thinking"}, {"slug": "20-works-cited"}]}]}]}
    state = json.dumps({"content": {"book": {"title": "Writing Guide", "tree": tree}}})

    def page(text, script=""):
        html = (f'<html><head><meta charset="utf-8"></head><body><h1>Title</h1><div data-type="page">{text}</div>'
                f'<script>{script}</script></body></html>')
        return 200, {"Content-Type": "text/html"}, html.encode("utf-8")  # no charset in the header

    c = client({
        "https://openstax.org/robots.txt": (200, {}, "User-agent: *\nDisallow: /apps/archive\n"),
        f"{base}/20-introduction": page('<p>Reflecting on “growth”</p><figure><span data-type="media"><button '
                                        'class="image-button-wrapper" aria-label="Click to enlarge"><img alt="Hood" '
                                        'src="/apps/image-cdn/v1/f=webp/x"></button></span></figure>',
                                        f"window.__PRELOADED_STATE__ = {state};\nwindow.x = 1;"),
        f"{base}/20-1-thinking": page('<h2 data-type="document-title">20.1 Thinking</h2><p>Section one</p>'),
        f"{base}/20-works-cited": (200, {}, '<h1>Works Cited</h1><div data-type="composite-page" '
                                            'data-book-content="true"><p>Sources</p></div>'),
    })
    f = fetch_openstax(c, f"{base}/20-introduction")
    assert f.title == "20 Portfolio Reflection" and "“growth”" in f.html
    assert f.html.index("growth") < f.html.index("Section one") < f.html.index("<h2>Works Cited</h2>") \
        < f.html.index("Sources")
    s = sanitize(f.html, base_url=f.base_url)  # the figure's zoom button goes, its image stays
    assert s.images == ["https://openstax.org/apps/image-cdn/v1/f=webp/x"] and "<button" not in s.html
    assert not any("/apps/archive" in u.split("openstax.org")[1] for u in c.session.calls if "robots" not in u)


def test_openstax_section_page_is_fetched_alone():
    base = "https://openstax.org/books/writing-guide/pages"
    tree = {"contents": [{"slug": "20-portfolio", "contents": [{"slug": "20-introduction"}, {"slug": "20-1-thinking"}]}]}
    state = json.dumps({"content": {"book": {"tree": tree}}})
    c = client({
        "https://openstax.org/robots.txt": ROBOTS_OK,
        f"{base}/20-1-thinking": (200, {}, f'<h1>20.1 Thinking</h1><div data-type="page"><p>Only this</p></div>'
                                           f'<script>window.__PRELOADED_STATE__ = {state}</script>'),
    })
    f = fetch_openstax(c, f"{base}/20-1-thinking")
    assert "Only this" in f.html and f.title == "20.1 Thinking" and len(c.session.calls) == 2


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
