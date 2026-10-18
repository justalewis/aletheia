"""Per-IP rate limits and the request-body cap on /api/."""
import json

import pytest
from django.test import Client, RequestFactory

from aletheia import ratelimit

MISSING = "/api/builds/00000000-0000-0000-0000-000000000000"
NOW = 1_800_000_000 - 1_800_000_000 % 3600 + 600  # ten minutes into a window


@pytest.fixture
def clock(monkeypatch):
    t = {"now": NOW}
    monkeypatch.setattr(ratelimit.time, "time", lambda: t["now"])
    return t


@pytest.fixture
def limits(settings, clock):
    settings.RATE_LIMIT_ENABLED = True
    settings.RATE_LIMIT_WINDOW = 3600
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "plan": 2, "export-pdf": 1, "export": 3, "read": 2,
                            "programs": 1, "write": 5}
    return settings.RATE_LIMITS


def post(path, ip="203.0.113.5", **extra):
    return Client().post(path, {"state": {}}, content_type="application/json", REMOTE_ADDR=ip, **extra)


def test_client_ip_prefers_fly_header_then_forwarded_for_then_remote_addr():
    rf = RequestFactory()
    both = rf.get("/api/pack", REMOTE_ADDR="10.0.0.1", HTTP_FLY_CLIENT_IP="198.51.100.7",
                  HTTP_X_FORWARDED_FOR="192.0.2.1, 10.0.0.2")
    assert ratelimit.client_ip(both) == "198.51.100.7"
    xff = rf.get("/api/pack", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR=" 192.0.2.1 , 10.0.0.2")
    assert ratelimit.client_ip(xff) == "192.0.2.1"
    assert ratelimit.client_ip(rf.get("/api/pack", REMOTE_ADDR="10.0.0.1")) == "10.0.0.1"
    v6 = rf.get("/api/pack", REMOTE_ADDR="10.0.0.1", HTTP_FLY_CLIENT_IP="2001:db8::1")
    assert ratelimit.client_ip(v6) == "2001:db8::1"
    # Junk in a header can't break the cache key.
    assert ratelimit.client_ip(rf.get("/", REMOTE_ADDR="10.0.0.1", HTTP_FLY_CLIENT_IP="1.2.3.4 \n<x>")) == "1.2.3.4x"


@pytest.mark.parametrize("method,path,rule", [
    ("POST", "/api/builds", "build-create"),
    ("PUT", "/api/builds/0b0e7f9c-1c1d-4a52-9f0a-7d0c2b0e1a11", "build-update"),
    ("POST", "/api/export/pdf", "export-pdf"),
    ("POST", "/api/export/html", "export"),
    ("POST", "/api/export/imscc", "export"),
    ("POST", "/api/plan", "plan"),
    ("POST", "/api/auto-build", "auto-build"),
    ("POST", "/api/feedback", "feedback"),
    ("POST", "/api/programs", "programs"),
    ("PUT", "/api/programs/wwu-eng-101", "programs"),
    ("DELETE", "/api/programs/wwu-eng-101/members/3", "programs"),
    ("POST", "/api/license-report", "write"),
    ("GET", "/api/readings", "read"),
    ("GET", "/api/builds/abc", "read"),
    ("OPTIONS", "/api/readings", None),
    ("POST", "/admin/login/", None),
])
def test_rules(method, path, rule):
    assert ratelimit.rule_for(method, path) == rule


def test_requests_are_counted_per_ip_and_answered_429_with_retry_after(db, limits):
    assert post("/api/plan").status_code != 429
    assert post("/api/plan").status_code != 429
    resp = post("/api/plan")
    assert resp.status_code == 429
    assert int(resp["Retry-After"]) == 3600 - 600  # the rest of the window
    assert "Try again in about 50 minutes" in json.loads(resp.content)["detail"]
    # Another address has its own count.
    assert post("/api/plan", ip="203.0.113.99").status_code != 429


def test_fly_client_ip_decides_whose_count_it_is(db, limits):
    for _ in range(2):
        post("/api/plan", ip="10.0.0.1", HTTP_FLY_CLIENT_IP="198.51.100.7")
    assert post("/api/plan", ip="10.0.0.1", HTTP_FLY_CLIENT_IP="198.51.100.7").status_code == 429
    # Same proxy address, different client: not limited.
    assert post("/api/plan", ip="10.0.0.1", HTTP_FLY_CLIENT_IP="198.51.100.8").status_code != 429


def test_pdf_exports_have_their_own_smaller_bucket(db, limits):
    assert post("/api/export/pdf").status_code != 429
    assert post("/api/export/pdf").status_code == 429
    # Other exports still work: they count against "export", not "export-pdf".
    assert post("/api/export/html").status_code == 200


def test_program_writes_are_limited_even_before_the_routes_exist(db, limits):
    assert post("/api/programs").status_code != 429
    assert post("/api/programs").status_code == 429


def test_gets_have_a_generous_cap_and_other_paths_none(db, limits):
    c = Client(REMOTE_ADDR="203.0.113.5")
    assert c.get(MISSING).status_code != 429
    assert c.get(MISSING).status_code != 429
    assert c.get(MISSING).status_code == 429
    assert all(c.get("/builder").status_code == 200 for _ in range(5))  # the SPA isn't counted
    assert c.get("/healthz").status_code == 200


def test_counts_reset_in_the_next_window(db, limits, clock):
    for _ in range(2):
        post("/api/plan")
    assert post("/api/plan").status_code == 429
    clock["now"] += 3600
    assert post("/api/plan").status_code != 429


def test_a_limit_of_zero_or_switching_off_disables_limits(db, limits, settings):
    settings.RATE_LIMITS = {**limits, "plan": 0}
    assert all(post("/api/plan").status_code != 429 for _ in range(4))
    settings.RATE_LIMITS = limits
    settings.RATE_LIMIT_ENABLED = False
    assert all(post("/api/plan").status_code != 429 for _ in range(4))


def test_cache_trouble_lets_requests_through(db, limits, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("cache table missing")
    monkeypatch.setattr(ratelimit.cache, "add", broken)
    assert all(post("/api/plan").status_code != 429 for _ in range(4))


def test_bodies_over_the_cap_get_413(db, settings):
    assert settings.MAX_API_BODY_BYTES == 512 * 1024 == settings.DATA_UPLOAD_MAX_MEMORY_SIZE
    big = {"state": {"instructor": "x" * (520 * 1024)}}
    resp = Client().post("/api/export/html", big, content_type="application/json", REMOTE_ADDR="203.0.113.5")
    assert resp.status_code == 413
    assert "512 KB" in json.loads(resp.content)["detail"]
    ok = {"state": {"instructor": "x" * (500 * 1024), "units": []}}
    resp = Client().post("/api/export/html", ok, content_type="application/json", REMOTE_ADDR="203.0.113.5")
    assert resp.status_code == 200


def test_security_headers(db):
    resp = Client().get(MISSING)
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert resp["Referrer-Policy"] == "same-origin"
    assert resp["X-Frame-Options"] == "DENY"
