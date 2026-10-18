"""Clean publisher HTML for the reader: keep the text and its structure, drop the page around it.

Kept: headings, paragraphs, lists, quotations, tables, figures and images with their alt text,
links, inline emphasis, code, and footnote anchors (ids are prefixed so two readings can't clash).
Removed with their content: scripts, styles, navigation, forms, and other page furniture.
Attributes are allowlisted (no class, style, or event handlers), and links keep only http(s),
mailto and in-page targets.

Changes that only carry the work into another format are made silently, as CC licenses allow
even for NoDerivatives works: embedded frames, video and audio become links to their source,
and inline SVG becomes an image. Media with no such equivalent (MathML, canvas, plugins, or
video without a source) is removed and named in `dropped`, so the exporter can tell an
incomplete copy from a whole one.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, CData, Comment, Declaration, Doctype, ProcessingInstruction, Tag

KEEP = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "q", "cite", "pre", "code", "kbd", "samp", "var", "em", "strong", "i", "b", "u",
    "s", "del", "ins", "mark", "small", "sub", "sup", "abbr", "dfn", "time", "a", "img", "figure",
    "figcaption", "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td", "colgroup", "col",
    "div", "span", "address", "details", "summary", "wbr",
}
# Containers whose landmark meaning doesn't belong inside a reader page; their content stays.
TO_DIV = {"section", "article", "main", "header", "footer", "aside", "center", "hgroup"}
REMOVE = {
    "script", "style", "noscript", "template", "nav", "form", "button", "input", "select", "option",
    "optgroup", "textarea", "fieldset", "legend", "datalist", "output", "progress", "meter", "head",
    "title", "meta", "link", "base", "dialog", "menu", "source", "track", "param", "area", "portal",
}
LOSSY = {"math", "canvas", "object", "embed", "applet", "map"}
MEDIA = {"iframe", "video", "audio"}

ATTRS = {
    "*": {"id", "lang", "dir", "title"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "th": {"colspan", "rowspan", "headers", "scope", "abbr"},
    "td": {"colspan", "rowspan", "headers"},
    "col": {"span"}, "colgroup": {"span"},
    "ol": {"start", "type", "reversed"}, "li": {"value"},
    "blockquote": {"cite"}, "q": {"cite"}, "time": {"datetime"}, "details": {"open"},
}
URL_ATTRS = {"href", "cite"}
LINK_SCHEMES = {"http", "https", "mailto"}
DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$", re.I)
NUMERIC = re.compile(r"^\d{1,5}(%|px)?$")
LAZY_SRC = ("data-src", "data-lazy-src", "data-original", "data-orig-file")


@dataclass
class Sanitized:
    html: str
    dropped: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)  # absolute http(s) image URLs, in order
    text_chars: int = 0


def _str(value) -> str:
    """An attribute value as text (BeautifulSoup gives multi-valued attributes as lists)."""
    if value is None:
        return ""
    return " ".join(value) if isinstance(value, list) else str(value).strip()


def _abs(url: str, base: str) -> str:
    url = (url or "").strip()
    return urljoin(base, url) if base else url


def _media_link(soup: BeautifulSoup, el: Tag, base: str) -> Tag | None:
    """A paragraph linking to an embed's source, or None when there's nothing to link to."""
    src = el.get("src") or el.get("data-src") or ""
    if not src:
        source = el.find("source", src=True)
        src = source.get("src", "") if source else ""
    if not _str(src):
        return None
    url = _abs(_str(src), base)
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    label = {"iframe": "Embedded content", "video": "Video", "audio": "Audio"}[el.name]
    p = soup.new_tag("p")
    p.append(f"{label}: ")
    a = soup.new_tag("a", href=url)
    a.string = (el.get("title") or el.get("aria-label") or url).strip()
    p.append(a)
    return p


def _svg_to_img(soup: BeautifulSoup, el: Tag) -> Tag:
    for bad in el.find_all(["script", "foreignobject", "foreignObject"]):
        bad.decompose()
    for tag in [el, *el.find_all(True)]:
        for attr in [a for a in tag.attrs if a.lower().startswith("on")]:
            del tag[attr]
    title = el.find("title")
    alt = el.get("aria-label") or (title.get_text(" ", strip=True) if title else "")
    if not el.get("xmlns"):
        el["xmlns"] = "http://www.w3.org/2000/svg"
    data = base64.b64encode(str(el).encode("utf-8")).decode("ascii")
    return soup.new_tag("img", src=f"data:image/svg+xml;base64,{data}", alt=alt)


def sanitize(html: str, base_url: str = "", id_prefix: str = "") -> Sanitized:
    soup = BeautifulSoup(html or "", "html.parser")
    dropped: set[str] = set()

    for node in soup.find_all(string=lambda s: isinstance(s, (CData, Comment, Declaration, Doctype, ProcessingInstruction))):
        node.extract()

    # Embeds first, while their attributes are still there.
    for el in soup.find_all(MEDIA):
        if el.decomposed or el.parent is None:
            continue
        link = _media_link(soup, el, base_url)
        if link is not None:
            el.replace_with(link)
        else:
            if el.name != "iframe":
                dropped.add(el.name)
            el.decompose()
    for el in soup.find_all("svg"):
        if el.decomposed or el.parent is None or el.find_parent("svg"):
            continue
        if el.get("aria-hidden") == "true" or el.get("role") in ("presentation", "none"):
            el.decompose()  # a decorative icon
        else:
            el.replace_with(_svg_to_img(soup, el))
    for el in soup.find_all("picture"):
        el.unwrap()

    for el in soup.find_all(True):
        if el.decomposed:
            continue
        name = el.name.lower()
        if name in REMOVE or el.has_attr("hidden"):
            el.decompose()
        elif name in LOSSY:
            dropped.add(name)
            el.decompose()

    images: list[str] = []
    for el in soup.find_all(True):
        name = el.name.lower()
        if name in TO_DIV:
            el.name = name = "div"
        if name not in KEEP:
            el.unwrap()
            continue
        if name == "img":
            src = _str(el.get("src"))
            if not src or src.startswith("data:image/gif"):  # lazy-loading placeholder
                src = next((_str(el[a]) for a in LAZY_SRC if el.get(a)), "") or src
            if not src and el.get("srcset"):
                src = _str(el["srcset"]).split(",")[0].strip().split(" ")[0]
            el["src"] = src
        if name == "a" and el.get("name") and not el.get("id"):
            el["id"] = _str(el["name"])  # old-style anchor
        if name == "details":
            el["open"] = ""  # the reader shows everything
        allowed = ATTRS["*"] | ATTRS.get(name, set())
        for attr in list(el.attrs):
            if attr not in allowed:
                del el[attr]
        for attr in ("width", "height", "colspan", "rowspan", "span", "start", "value"):
            if attr in el.attrs and not NUMERIC.match(_str(el[attr])):
                del el[attr]
        if "id" in el.attrs:
            el["id"] = id_prefix + _str(el["id"])
        if "headers" in el.attrs:
            el["headers"] = " ".join(id_prefix + h for h in _str(el["headers"]).split())
        for attr in URL_ATTRS & set(el.attrs):
            raw = _str(el[attr])
            if not raw:
                del el[attr]
                continue
            if raw.startswith("#"):
                el[attr] = "#" + id_prefix + raw[1:]
                continue
            url = _abs(raw, base_url)
            page, frag = urldefrag(url)
            if frag and base_url and page == urldefrag(base_url)[0]:
                el[attr] = "#" + id_prefix + frag  # a link to elsewhere on the same page
            elif urlsplit(url).scheme in LINK_SCHEMES:
                el[attr] = url
            else:
                del el[attr]
        if name == "img":
            src = _abs(_str(el["src"]), base_url) if el.get("src") else ""
            if urlsplit(src).scheme in ("http", "https"):
                el["src"] = src
                images.append(src)
            elif DATA_IMAGE.match(src):
                el["src"] = src
            else:
                # An image we can't show: keep its description in the text.
                alt = (el.get("alt") or "").strip()
                if alt:
                    el.replace_with(f"[Image: {alt}]")
                else:
                    dropped.add("img")
                    el.decompose()
                continue
            if "alt" not in el.attrs:
                el["alt"] = ""
        if name == "a" and el.get("href", "").startswith(("http:", "https:")):
            el["rel"] = "noopener"
        if name == "span" and not el.attrs:
            el.unwrap()

    text_chars = len(soup.get_text(" ", strip=True))
    out = str(soup).strip()
    return Sanitized(html=out, dropped=sorted(dropped), images=list(dict.fromkeys(images)), text_chars=text_chars)


_HEADING = re.compile(r"<(/?)h([1-6])\b", re.I)


def shift_headings(html: str, top: int) -> str:
    """Renumber headings so the reading's highest level becomes `top`, keeping their order.

    A reading's own h1/h2 would otherwise outrank the reader's unit headings. Levels past h6
    collapse into h6. This changes markup only, never text.
    """
    levels = [int(m.group(2)) for m in _HEADING.finditer(html)]
    if not levels:
        return html
    offset = top - min(levels)
    return _HEADING.sub(lambda m: f"<{m.group(1)}h{min(max(int(m.group(2)) + offset, 1), 6)}", html)
