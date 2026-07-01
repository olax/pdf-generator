"""Unit tests for the filesystem PDF cache (stdlib only, no browser needed)."""
import asyncio
import importlib
import os
import time

import pytest


def _fresh_cache(tmp_path, **env):
    os.environ["CACHE_DIR"] = str(tmp_path)
    os.environ.setdefault("CACHE_ENABLED", "true")
    for k, v in env.items():
        os.environ[k] = str(v)
    import app.cache as cache_mod
    importlib.reload(cache_mod)
    return cache_mod


def test_key_is_canonical_and_distinct(tmp_path):
    cache_mod = _fresh_cache(tmp_path)
    c = cache_mod.PDFCache()
    k1 = c.key({"url": "https://x/a", "opts": {"b": 1, "a": 2}})
    k2 = c.key({"opts": {"a": 2, "b": 1}, "url": "https://x/a"})
    assert k1 == k2                      # order-independent
    assert c.key({"url": "https://x/b"}) != k1


def test_miss_set_hit(tmp_path):
    cache_mod = _fresh_cache(tmp_path)
    c = cache_mod.PDFCache()

    async def run():
        k = c.key({"u": 1})
        assert await c.get(k) is None
        await c.set(k, b"%PDF-1.4 hi")
        assert await c.get(k) == b"%PDF-1.4 hi"
        assert c.hits == 1 and c.misses == 1

    asyncio.run(run())


def test_ttl_expiry(tmp_path):
    cache_mod = _fresh_cache(tmp_path, CACHE_TTL_SECONDS=1)
    c = cache_mod.PDFCache()

    async def run():
        await c.set("k", b"data")
        assert await c.get("k") == b"data"
        time.sleep(1.2)
        assert await c.get("k") is None   # expired

    asyncio.run(run())


def test_size_bounded_eviction(tmp_path):
    cache_mod = _fresh_cache(tmp_path, CACHE_MAX_BYTES=300)
    c = cache_mod.PDFCache()

    async def run():
        for i in range(6):
            await c.set(f"e{i}", bytes(80))
            time.sleep(0.02)
        total = sum(p.stat().st_size for p in c.dir.glob("*.pdf"))
        assert total <= c.max_bytes

    asyncio.run(run())


def test_disabled_is_noop(tmp_path):
    cache_mod = _fresh_cache(tmp_path, CACHE_ENABLED="false")
    c = cache_mod.PDFCache()

    async def run():
        await c.set("x", b"y")
        assert await c.get("x") is None

    asyncio.run(run())
