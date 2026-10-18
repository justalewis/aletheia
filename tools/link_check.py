"""Check every link in the catalog and record its status.

Reads   research/catalog/readings.json     (landing_url, html_url, pdf_url, epub_url)
        research/catalog/assignments.json  (url, file_url)
Writes  research/link_check.json

Polite by design: one request at a time per host, a fixed delay between requests to the
same host, and a real browser User-Agent that also names this tool. Pages get a GET (first
256 KB only, so soft 404s can be caught); file links get a HEAD, falling back to a ranged GET.

wacclearinghouse.org /docs/ files sit behind a Cloudflare bot check, so they are marked
"bot-gated" without a request. Any other Cloudflare challenge is detected and marked the same.

Statuses
    live        2xx at the URL, or after a trivial redirect (http->https, www, trailing slash)
    redirected  2xx after a redirect to a different location (final_url says where)
    dead        404/410, unknown host, or a page whose title says "not found" (soft 404)
    bot-gated   Cloudflare (or similar) challenge page; not attempted further
    blocked     401/403/429 without a challenge page: the server refused an automated request
    error       timeout, connection failure, or 5xx after one retry

Results are cached in the output file: a re-run checks only URLs it has not seen.
    --recheck        check everything again
    --retry STATUS   re-check URLs whose last status was STATUS (repeatable)
    --limit N        stop after N new checks (for testing)
"""
from __future__ import annotations

import argparse
import gzip
import html
import http.cookiejar
import json
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "research" / "catalog"
OUT = ROOT / "research" / "link_check.json"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36 OATextbookBuilder-linkcheck/1.0")
DEFAULT_DELAY = 2.0
HOST_DELAY = {"wacclearinghouse.org": 3.0}
TIMEOUT = 25
MAX_REDIRECTS = 10
READ_LIMIT = 256 * 1024
WORKERS = 12

READING_URL_FIELDS = ["landing_url", "html_url", "pdf_url", "epub_url"]
ASSIGNMENT_URL_FIELDS = ["url", "file_url"]
FILE_EXT = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|odt|epub|zip|rtf|mp3|mp4)$", re.I)
SOFT_404 = re.compile(r"\b(404|page not found|not found|no longer available|does not exist|page missing)\b", re.I)
CHALLENGE = re.compile(r"just a moment\.\.\.|challenge-platform|cf-chl-|attention required! \| cloudflare|"
                       r"checking your browser|ddos-guard|captcha-delivery|px-captcha", re.I)
CHALLENGE_PATH = re.compile(r"^/(challenge|captcha|cdn-cgi/challenge-platform)\b", re.I)  # e.g. sandbox.acrl.org


def is_bot_gated_by_rule(url: str) -> bool:
    p = urlsplit(url)
    return p.netloc.lower().removeprefix("www.") == "wacclearinghouse.org" and p.path.startswith("/docs/")


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def to_ascii_url(url: str) -> str:
    """Percent-encode spaces and non-ASCII so urllib accepts the URL; leave existing escapes alone."""
    p = urlsplit(url.strip())
    netloc = p.netloc.encode("idna").decode("ascii") if any(ord(c) > 127 for c in p.netloc) else p.netloc
    path = quote(p.path, safe="/%:@!$&'()*+,;=~-._")
    query = quote(p.query, safe="/%:@!$&'()*+,;=?~-._")
    return urlunsplit((p.scheme, netloc, path, query, ""))


def trivial_redirect(a: str, b: str) -> bool:
    """Same resource after scheme, www, case, or trailing-slash normalization."""
    def norm(u):
        p = urlsplit(u)
        return (p.netloc.lower().removeprefix("www."), p.path.rstrip("/").lower() or "/", p.query)
    return norm(a) == norm(b)


def went_home(orig: str, final: str) -> bool:
    """A deep link that lands on a bare site root is usually a removed page."""
    o, f = urlsplit(orig), urlsplit(final)
    return (o.path.strip("/") != "" and not f.query
            and f.path.strip("/") in ("", "index.html", "index.php", "home"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


SSL_OK = ssl.create_default_context()
SSL_LAX = ssl.create_default_context()
SSL_LAX.check_hostname = False
SSL_LAX.verify_mode = ssl.CERT_NONE
COOKIES = http.cookiejar.CookieJar()  # some repositories (KC Works) redirect until a session cookie comes back


def opener(lax: bool) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=SSL_LAX if lax else SSL_OK),
                                       urllib.request.HTTPCookieProcessor(COOKIES))


def decode_body(raw: bytes, encoding: str, charset: str = "utf-8") -> str:
    try:
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            raw = zlib.decompress(raw)
    except Exception:  # truncated stream: decompress what we can
        try:
            raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
        except Exception:
            return ""
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def one_request(url: str, method: str, lax: bool, ranged: bool) -> dict:
    """A single HTTP request, no redirect following. Returns code, headers, a body sample."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    if ranged:
        headers["Range"] = "bytes=0-4095"
    req = urllib.request.Request(to_ascii_url(url), method=method, headers=headers)
    try:
        resp = opener(lax).open(req, timeout=TIMEOUT)
        code, hdrs = resp.status, resp.headers
        body = resp.read(READ_LIMIT) if method == "GET" else b""
        resp.close()
    except urllib.error.HTTPError as e:
        code, hdrs = e.code, e.headers
        try:
            body = e.read(READ_LIMIT) if method == "GET" or code >= 400 else b""
        except Exception:
            body = b""
    text = ""
    ctype = (hdrs.get("Content-Type") or "").lower()
    if body and ("html" in ctype or "text" in ctype or not ctype):
        m = re.search(r"charset=([\w-]+)", ctype)
        text = decode_body(body, (hdrs.get("Content-Encoding") or "").lower(), m.group(1) if m else "utf-8")
    return {"code": code, "headers": hdrs, "body": body, "text": text, "ctype": ctype}


def classify_failure(exc: Exception) -> tuple[str, str]:
    reason = getattr(exc, "reason", exc)
    msg = f"{type(reason).__name__}: {reason}"
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in msg or "Name or service not known" in msg:
        return "dead", "unknown host (" + msg + ")"
    return "error", msg


def fetch(url: str, prefer_head: bool) -> dict:
    """Follow redirects by hand so the chain is recorded."""
    chain: list[list] = []
    current = url
    lax = False
    warning = ""
    method = "HEAD" if prefer_head else "GET"
    for _ in range(MAX_REDIRECTS + 1):
        try:
            r = one_request(current, method, lax, ranged=(prefer_head and method == "GET"))
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError) and not lax:
                lax, warning = True, f"certificate problem: {e.reason}"
                continue
            status, note = classify_failure(e)
            return {"status": status, "note": note, "redirects": chain, "warning": warning}
        except (TimeoutError, socket.timeout) as e:
            return {"status": "error", "note": f"timeout: {e}", "redirects": chain, "warning": warning}
        except (ConnectionError, OSError, ValueError) as e:
            status, note = classify_failure(e)
            return {"status": status, "note": note, "redirects": chain, "warning": warning}

        code, hdrs = r["code"], r["headers"]
        if code in (301, 302, 303, 307, 308) and hdrs.get("Location"):
            nxt = urljoin(current, hdrs["Location"])
            chain.append([code, nxt])
            if nxt == current:
                break
            current = nxt
            continue
        if method == "HEAD" and code >= 400:
            method = "GET"  # many servers mishandle HEAD; ask again with a ranged GET
            continue
        return {"code": code, "final_url": current, "redirects": chain, "ctype": r["ctype"],
                "text": r["text"], "body": r["body"], "headers": hdrs, "method": method, "warning": warning}
    return {"status": "error", "note": "too many redirects", "redirects": chain, "warning": warning}


def title_of(page: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:200] if m else ""


def check(url: str, is_file: bool) -> dict:
    if is_bot_gated_by_rule(url):
        return {"status": "bot-gated", "note": "wacclearinghouse.org /docs/ file behind a Cloudflare bot check; not requested",
                "method": "skipped"}
    r = fetch(url, prefer_head=is_file)
    if "status" in r:
        out = {k: r[k] for k in ("status", "note") if r.get(k)}
        if r.get("redirects"):
            out["redirects"] = r["redirects"]
        if r.get("warning"):
            out["warning"] = r["warning"]
        return out

    code, final, text = r["code"], r["final_url"], r["text"]
    hdrs = r["headers"]
    out = {"http_status": code, "method": r["method"]}
    if r["redirects"]:
        out["redirects"] = r["redirects"]
        out["final_url"] = final
    if r["ctype"]:
        out["content_type"] = r["ctype"].split(";")[0].strip()
    if r.get("warning"):
        out["warning"] = r["warning"]
    title = title_of(text) if text else ""
    if title:
        out["title"] = title

    challenged = (hdrs.get("cf-mitigated", "").lower() == "challenge"
                  or (code in (403, 429, 503) and text and CHALLENGE.search(text[:20000]))
                  or bool(CHALLENGE_PATH.search(urlsplit(final).path)))
    if challenged:
        out["status"] = "bot-gated"
        out["note"] = "challenge page (Cloudflare or similar); not attempted further"
    elif 200 <= code < 300:
        if title and SOFT_404.search(title) and not FILE_EXT.search(urlsplit(url).path):
            out["status"], out["note"] = "dead", "soft 404: page title says not found"
        elif r["redirects"] and went_home(url, final):
            out["status"], out["note"] = "dead", "redirects to the site's home page"
        elif r["redirects"] and not trivial_redirect(url, final):
            out["status"] = "redirected"
        else:
            out["status"] = "live"
        if is_file:
            body = r["body"] or b""
            looks_pdf = "pdf" in out.get("content_type", "") or body[:5] == b"%PDF-"
            if urlsplit(url).path.lower().endswith(".pdf") and not looks_pdf and r["method"] == "GET":
                out["note"] = (out.get("note", "") + "; " if out.get("note") else "") + "PDF link serves non-PDF content"
            elif "html" in out.get("content_type", "") and urlsplit(url).path.lower().endswith(".pdf"):
                out["note"] = (out.get("note", "") + "; " if out.get("note") else "") + "PDF link serves HTML"
    elif code in (404, 410):
        out["status"] = "dead"
    elif code in (401, 403, 429):
        out["status"] = "blocked"
    else:
        out["status"] = "error"
    return out


def collect() -> dict[str, dict]:
    """url -> {"is_file": bool, "used_by": [...]}"""
    urls: dict[str, dict] = {}
    for kind, fname, fields in [("reading", "readings.json", READING_URL_FIELDS),
                                ("assignment", "assignments.json", ASSIGNMENT_URL_FIELDS)]:
        for rec in json.loads((CATALOG / fname).read_text(encoding="utf-8")):
            for f in fields:
                u = (rec.get(f) or "").strip()
                if not u.startswith(("http://", "https://")):
                    continue
                entry = urls.setdefault(u, {"is_file": False, "used_by": []})
                entry["used_by"].append({"type": kind, "id": rec["id"], "field": f})
                if f in ("pdf_url", "file_url", "epub_url") or FILE_EXT.search(urlsplit(u).path):
                    entry["is_file"] = True
    return urls


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("--retry", action="append", default=[])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    urls = collect()
    old = {}
    if OUT.exists() and not args.recheck:
        old = {r["url"]: r for r in json.loads(OUT.read_text(encoding="utf-8")).get("results", [])}
    todo = [u for u in urls if u not in old or old[u].get("status") in args.retry]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(urls)} unique URLs; {len(old)} in cache; checking {len(todo)}", flush=True)

    results = {u: r for u, r in old.items() if u in urls}
    lock = threading.Lock()
    done = Counter()

    def save() -> None:
        rows = []
        for u in sorted(results):
            row = {"url": u, **{k: v for k, v in results[u].items() if k not in ("url", "used_by")}}
            row["used_by"] = urls[u]["used_by"]
            rows.append(row)
        summary = Counter(r["status"] for r in rows)
        payload = {
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user_agent": USER_AGENT,
            "delay_seconds": {"default": DEFAULT_DELAY, **HOST_DELAY},
            "total_urls": len(urls),
            "checked": len(rows),
            "summary": dict(summary.most_common()),
            "results": rows,
        }
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT)

    by_host: dict[str, list[str]] = defaultdict(list)
    for u in todo:
        by_host[host_of(u)].append(u)

    def run_host(host: str) -> None:
        delay = HOST_DELAY.get(host, DEFAULT_DELAY)
        last = 0.0
        for u in by_host[host]:
            if not is_bot_gated_by_rule(u):
                wait = last + delay - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            res = check(u, urls[u]["is_file"])
            if res["status"] == "error" or res.get("http_status") in (429, 500, 502, 503, 504):
                time.sleep(max(delay * 4, 10))
                res = check(u, urls[u]["is_file"])
                res["retried"] = True
            if res.get("method") != "skipped":
                last = time.monotonic()
            res["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with lock:
                results[u] = res
                done[res["status"]] += 1
                n = sum(done.values())
                if n % 100 == 0:
                    print(f"  {n}/{len(todo)} {dict(done)}", flush=True)
                    save()

    hosts = sorted(by_host, key=lambda h: -len(by_host[h]))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run_host, hosts))
    save()
    print("done:", dict(Counter(r["status"] for r in results.values()).most_common()), flush=True)


if __name__ == "__main__":
    sys.exit(main())
