"""
Integration tests for auth, host-allowlist, SSRF guard and output cache.
The browser is mocked, so these run without Chromium.

Run: pip install -r requirements-dev.txt && pytest
"""
import asyncio
import importlib
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="pdfc-")
    os.environ["CACHE_ENABLED"] = "true"
    os.environ["PDF_API_KEYS"] = "secret-key-1, secret-key-2"
    os.environ["ALLOWED_HOSTS"] = ".bayonne.fr, example.org"

    import app.auth as auth_mod
    import app.cache as cache_mod
    import app.main as m
    importlib.reload(auth_mod)
    importlib.reload(cache_mod)
    importlib.reload(m)

    render_calls = {"n": 0}

    async def fake_from_url(req):
        render_calls["n"] += 1
        return b"%PDF-1.4 FAKE-" + req.url.encode()

    class FakeGen:
        from_url = staticmethod(fake_from_url)

    class FakePool:
        def status(self):
            return {"pool_size": 3, "active": 3, "pages_served": [0, 0, 0]}

    m.pdf_cache = cache_mod.PDFCache()
    m.task_semaphore = asyncio.Semaphore(10)
    m.pdf_generator = FakeGen()
    m.browser_pool = FakePool()

    c = TestClient(m.app)
    c.render_calls = render_calls  # expose for assertions
    c.m = m
    return c


AUTH = {"X-API-Key": "secret-key-1"}
BODY = {"url": "https://www.bayonne.fr/article/pdf"}


def test_host_allowlist(client):
    m = client.m
    assert m._host_allowed("www.bayonne.fr")
    assert m._host_allowed("bayonne.fr")
    assert m._host_allowed("arenes.bayonne.fr")
    assert not m._host_allowed("evil.com")
    assert not m._host_allowed("notbayonne.fr")   # dotted-boundary suffix


def test_ssrf_and_allowlist_validation(client):
    m = client.m
    for bad in ("ftp://bayonne.fr/x", "https://evil.com/x", "https://169.254.169.254/"):
        with pytest.raises(ValueError):
            m._validate_safe_url(bad)
    assert m._validate_safe_url("https://www.bayonne.fr/a/pdf")


def test_auth_required(client):
    assert client.post("/api/pdf/from-url", json=BODY).status_code == 401
    assert client.post("/api/pdf/from-url", json=BODY,
                       headers={"X-API-Key": "nope"}).status_code == 401


def test_cache_miss_then_hit(client):
    r = client.post("/api/pdf/from-url", json=BODY, headers=AUTH)
    assert r.status_code == 200 and r.headers["x-cache"] == "MISS"
    assert client.render_calls["n"] == 1
    r = client.post("/api/pdf/from-url", json=BODY, headers=AUTH)
    assert r.status_code == 200 and r.headers["x-cache"] == "HIT"
    assert client.render_calls["n"] == 1          # not re-rendered


def test_no_cache_bypass(client):
    client.post("/api/pdf/from-url", json=BODY, headers=AUTH)              # warm
    n = client.render_calls["n"]
    r = client.post("/api/pdf/from-url", json={**BODY, "no_cache": True}, headers=AUTH)
    assert r.headers["x-cache"] == "BYPASS"
    assert client.render_calls["n"] == n + 1


def test_bearer_auth(client):
    r = client.post("/api/pdf/from-url", json=BODY,
                    headers={"Authorization": "Bearer secret-key-2"})
    assert r.status_code == 200


def test_disallowed_host_rejected(client):
    r = client.post("/api/pdf/from-url", json={"url": "https://evil.com/x"}, headers=AUTH)
    assert r.status_code == 422


def test_health_open_with_cache_stats(client):
    h = client.get("/api/health")
    assert h.status_code == 200 and "cache" in h.json()
