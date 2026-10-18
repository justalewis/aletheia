"""Per-IP request limits and a body-size cap for the public API.

Counts live in Django's cache (the database cache in settings), so every gunicorn worker and
every machine shares them. Windows are fixed: a client gets N requests per window, counted from
the top of the window, and a 429 with Retry-After once it runs out.

Each request counts against the first rule it matches. Numbers come from settings.RATE_LIMITS,
keyed by rule name; a limit of 0 turns that rule off.
"""
from __future__ import annotations

import logging
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

log = logging.getLogger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
READ_METHODS = frozenset({"GET", "HEAD"})

# (rule name, methods, path pattern). First match wins.
RULES = [
    ("build-create", {"POST"}, re.compile(r"^/api/builds/?$")),
    ("build-update", {"PUT"}, re.compile(r"^/api/builds/[^/]+/?$")),
    ("export-pdf", {"POST"}, re.compile(r"^/api/export/pdf/?$")),
    ("export", {"POST"}, re.compile(r"^/api/export/")),
    ("plan", {"POST"}, re.compile(r"^/api/plan/?$")),
    ("auto-build", {"POST"}, re.compile(r"^/api/auto-build/?$")),
    ("programs", WRITE_METHODS, re.compile(r"^/api/programs(/|$)")),
    ("write", WRITE_METHODS, re.compile(r"^/api/")),
    ("read", READ_METHODS, re.compile(r"^/api/")),
]

_IP_CHARS = re.compile(r"[^0-9A-Za-z:.]")


def client_ip(request) -> str:
    """Fly-Client-IP (set by Fly's proxy), then the first X-Forwarded-For hop, then REMOTE_ADDR."""
    meta = request.META
    raw = (meta.get("HTTP_FLY_CLIENT_IP", "").strip()
           or meta.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
           or meta.get("REMOTE_ADDR", "").strip())
    return _IP_CHARS.sub("", raw)[:64] or "unknown"


def rule_for(method: str, path: str) -> str | None:
    for name, methods, pattern in RULES:
        if method in methods and pattern.match(path):
            return name
    return None


def _too_many(rule: str, retry_after: int) -> JsonResponse:
    minutes = max(1, round(retry_after / 60))
    what = {"export-pdf": "PDF exports", "export": "exports", "build-create": "new saved courses"}.get(rule, "requests")
    resp = JsonResponse(
        {"detail": f"Your network has made too many {what} for now. "
                   f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}."},
        status=429)
    resp["Retry-After"] = str(retry_after)
    return resp


def _too_large(limit: int) -> JsonResponse:
    return JsonResponse({"detail": f"That request is too large. The limit is {limit // 1024} KB."}, status=413)


class ApiGuardMiddleware:
    """Cap request bodies and count requests per client IP on /api/."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/"):
            blocked = self._check_size(request) or self._check_rate(request)
            if blocked is not None:
                return blocked
        return self.get_response(request)

    # ------------------------------------------------------------ body size

    def _check_size(self, request):
        """Django reads no more than Content-Length bytes (none without it), so the header is the size."""
        limit = settings.MAX_API_BODY_BYTES
        length = request.META.get("CONTENT_LENGTH")
        if not limit or not length:
            return None
        try:
            return _too_large(limit) if int(length) > limit else None
        except ValueError:
            return JsonResponse({"detail": "Bad Content-Length header."}, status=400)

    # ------------------------------------------------------------ rate

    def _check_rate(self, request):
        if not settings.RATE_LIMIT_ENABLED:
            return None
        rule = rule_for(request.method, request.path)
        limit = settings.RATE_LIMITS.get(rule, 0) if rule else 0
        if limit <= 0:
            return None
        window = settings.RATE_LIMIT_WINDOW
        now = int(time.time())
        start = now - now % window
        key = f"rl:{rule}:{client_ip(request)}:{start}"
        try:
            cache.add(key, 0, timeout=window + 60)
            count = cache.incr(key)
        except Exception:  # noqa: BLE001 - a cache outage shouldn't take the site down
            log.warning("rate limit cache unavailable; letting %s %s through", request.method, request.path,
                        exc_info=True)
            return None
        if count > limit:
            return _too_many(rule, max(1, start + window - now))
        return None
