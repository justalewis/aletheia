import base64

from content.sanitize import sanitize, shift_headings

BASE = "https://books.example.org/book/chapter/one/"


def test_keeps_structure_and_text():
    html = """
    <h2>Heading</h2><p>Some <em>emphasis</em> and <strong>strength</strong>.</p>
    <ul><li>one</li><li>two</li></ul><ol start="3"><li>three</li></ol>
    <blockquote><p>Quoted</p></blockquote>
    <table><caption>T</caption><tr><th scope="col">H</th></tr><tr><td colspan="2">D</td></tr></table>
    <figure><img src="/img/a.png" alt="A chart"><figcaption>Figure 1</figcaption></figure>
    """
    s = sanitize(html, BASE)
    for kept in ["<h2>Heading</h2>", "<em>emphasis</em>", "<li>one</li>", '<ol start="3">', "<blockquote>",
                 '<th scope="col">H</th>', '<td colspan="2">D</td>', "<figcaption>Figure 1</figcaption>",
                 'alt="A chart"']:
        assert kept in s.html
    assert 'src="https://books.example.org/img/a.png"' in s.html
    assert s.images == ["https://books.example.org/img/a.png"]
    assert s.dropped == []


def test_strips_scripts_styles_nav_forms_and_attributes():
    html = """
    <nav><a href="/next">Next chapter</a></nav>
    <script>alert(1)</script><style>p{color:red}</style>
    <form><input name="q"><button>Go</button></form>
    <p class="x" style="color:red" onclick="steal()" data-foo="1">Body text</p>
    <div hidden>secret</div>
    """
    s = sanitize(html, BASE)
    assert "Body text" in s.html
    for gone in ["Next chapter", "alert", "color", "onclick", "steal", "data-foo", "class=", "<form",
                 "<input", "<button", "Go<", "secret"]:
        assert gone not in s.html


def test_neutralizes_dangerous_urls_and_handlers():
    html = """
    <a href="javascript:alert(1)">bad link</a>
    <a href="vbscript:x">also bad</a>
    <img src="x" onerror="alert(1)" alt="pic">
    <img src="data:text/html;base64,PHNjcmlwdD4=" alt="sneaky">
    <svg><script>alert(1)</script><circle r="4"/></svg>
    <iframe src="javascript:alert(1)"></iframe>
    """
    s = sanitize(html, "")
    assert "javascript:" not in s.html and "vbscript:" not in s.html and "onerror" not in s.html
    assert "bad link" in s.html  # the text stays, the link goes
    assert "data:text/html" not in s.html
    assert "[Image: sneaky]" in s.html
    # Inline SVG becomes an image, where its scripts can't run.
    assert "<svg" not in s.html and 'src="data:image/svg+xml;base64,' in s.html
    assert "<iframe" not in s.html


def test_embeds_become_links_and_lossy_media_is_reported():
    html = """
    <iframe src="https://www.youtube.com/embed/abc" title="Interview"></iframe>
    <video><source src="/media/talk.mp4"></video>
    <video></video>
    <math><mi>x</mi></math>
    <p>after</p>
    """
    s = sanitize(html, BASE)
    assert 'Embedded content: <a href="https://www.youtube.com/embed/abc" rel="noopener">Interview</a>' in s.html
    assert 'Video: <a href="https://books.example.org/media/talk.mp4"' in s.html
    assert s.dropped == ["math", "video"]


def test_ids_and_internal_links_are_prefixed():
    html = ('<p>Claim<a href="#fn1" id="ref1"><sup>1</sup></a> and '
            '<a href="https://books.example.org/book/chapter/one/#fn1">same page</a></p>'
            '<ol><li id="fn1">Note <a href="#ref1">back</a></li></ol><a name="old">x</a>')
    s = sanitize(html, BASE, id_prefix="r1-")
    assert 'href="#r1-fn1"' in s.html and 'id="r1-fn1"' in s.html and 'id="r1-ref1"' in s.html
    assert s.html.count('href="#r1-fn1"') == 2
    assert 'id="r1-old"' in s.html


def test_lazy_images_and_missing_alt():
    html = ('<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" data-src="/real.jpg">'
            '<img srcset="/small.jpg 300w, /big.jpg 900w">')
    s = sanitize(html, BASE)
    assert s.images == ["https://books.example.org/real.jpg", "https://books.example.org/small.jpg"]
    assert s.html.count('alt=""') == 2


def test_text_is_unchanged():
    """Sanitizing moves markup around but never edits the words (NoDerivatives works rely on this)."""
    text = "It was the best of times, it was the worst of times — “quoted” & <escaped>."
    s = sanitize(f"<div class='entry'><p>{text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</p></div>")
    assert text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") in s.html


def test_shift_headings_keeps_order_and_clamps():
    html = "<h2>a</h2><h3>b</h3><h4>c</h4><h5>d</h5>"
    assert shift_headings(html, 5) == "<h5>a</h5><h6>b</h6><h6>c</h6><h6>d</h6>"
    assert shift_headings("<h1 id='x'>t</h1><h2>u</h2>", 3) == "<h3 id='x'>t</h3><h4>u</h4>"
    assert shift_headings("<p>no headings</p>", 5) == "<p>no headings</p>"


def test_svg_alt_comes_from_title():
    s = sanitize('<svg role="img"><title>Bar chart of enrollment</title><rect/></svg>')
    assert 'alt="Bar chart of enrollment"' in s.html
    src = s.html.split('src="data:image/svg+xml;base64,')[1].split('"')[0]
    assert b"<rect" in base64.b64decode(src)
