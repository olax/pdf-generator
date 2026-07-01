"""
PDF output cache — filesystem-backed, keyed by a hash of all render-affecting
request fields.

Rationale: the crawler-driven outage that motivated this service was caused by
"N identical PDF requests = N full renders". Caching turns that into
"1 render + (N-1) static file reads", which is the single biggest cost fix for
public, crawlable PDF endpoints (city-site article PDFs are effectively static).

Design:
- key = sha256 of the canonical JSON of every field that affects the output
  (URL/HTML, pdf_options, injected CSS/JS, images, cookies, headers, viewport,
  wait/media settings, user-agent) — but NOT `timeout`/`no_cache` (they do not
  change the bytes).
- entries stored as <key>.pdf; TTL via file mtime; LRU-ish (get() refreshes
  mtime); size-bounded eviction (oldest first) on write.
- fully async (blocking FS ops go through asyncio.to_thread).
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("pdf-service.cache")


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


CACHE_ENABLED = _env_bool("CACHE_ENABLED", True)
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/tmp/pdf-cache"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "86400"))          # 24h; 0 = no expiry
CACHE_MAX_BYTES = int(os.getenv("CACHE_MAX_BYTES", str(2_000_000_000)))   # 2 GB


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:  # pragma: no cover
        logger.warning(f"cache: failed to unlink {path}: {e}")


def _touch_quiet(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


class PDFCache:
    """Filesystem PDF cache. Safe no-op when disabled."""

    def __init__(self) -> None:
        self.enabled = CACHE_ENABLED
        self.dir = CACHE_DIR
        self.ttl = CACHE_TTL_SECONDS
        self.max_bytes = CACHE_MAX_BYTES
        self._write_lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                f"PDF cache enabled: dir={self.dir} ttl={self.ttl}s "
                f"max={self.max_bytes}B"
            )
        else:
            logger.info("PDF cache disabled (CACHE_ENABLED=false)")

    # -- key ---------------------------------------------------------------

    @staticmethod
    def key(fingerprint: dict) -> str:
        """Stable sha256 over the canonical JSON of render-affecting fields."""
        blob = json.dumps(
            fingerprint, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.pdf"

    # -- read / write ------------------------------------------------------

    async def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            st = await asyncio.to_thread(path.stat)
        except FileNotFoundError:
            self.misses += 1
            return None
        if self.ttl > 0 and (time.time() - st.st_mtime) > self.ttl:
            await asyncio.to_thread(_unlink_quiet, path)
            self.misses += 1
            return None
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:  # raced with eviction
            self.misses += 1
            return None
        await asyncio.to_thread(_touch_quiet, path)  # LRU refresh
        self.hits += 1
        return data

    async def set(self, key: str, data: bytes) -> None:
        if not self.enabled or not data:
            return
        path = self._path(key)
        tmp = path.with_name(f"{key}.{os.getpid()}.tmp")
        async with self._write_lock:
            try:
                await asyncio.to_thread(tmp.write_bytes, data)
                await asyncio.to_thread(os.replace, tmp, path)
            except OSError as e:  # pragma: no cover
                logger.warning(f"cache: failed to store {key}: {e}")
                await asyncio.to_thread(_unlink_quiet, tmp)
                return
            await self._evict_if_needed()

    # -- maintenance -------------------------------------------------------

    async def _evict_if_needed(self) -> None:
        """Drop oldest entries until total size <= max_bytes. Caller holds lock."""
        entries: list[tuple[float, int, Path]] = []
        total = 0
        for p in self.dir.glob("*.pdf"):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= self.max_bytes:
            return
        entries.sort()  # oldest mtime first
        for _mtime, size, p in entries:
            await asyncio.to_thread(_unlink_quiet, p)
            total -= size
            if total <= self.max_bytes:
                break

    async def sweep_expired(self) -> int:
        """Remove expired entries; returns count. For periodic maintenance."""
        if not self.enabled or self.ttl <= 0:
            return 0
        now = time.time()
        removed = 0
        for p in list(self.dir.glob("*.pdf")):
            try:
                if now - p.stat().st_mtime > self.ttl:
                    await asyncio.to_thread(_unlink_quiet, p)
                    removed += 1
            except FileNotFoundError:
                continue
        if removed:
            logger.info(f"cache: swept {removed} expired entries")
        return removed

    def stats(self) -> dict:
        entries = 0
        size = 0
        if self.enabled:
            for p in self.dir.glob("*.pdf"):
                try:
                    size += p.stat().st_size
                    entries += 1
                except FileNotFoundError:
                    continue
        total_lookups = self.hits + self.misses
        hit_rate = round(self.hits / total_lookups, 3) if total_lookups else 0.0
        return {
            "enabled": self.enabled,
            "entries": entries,
            "size_bytes": size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": hit_rate,
            "ttl_seconds": self.ttl,
            "max_bytes": self.max_bytes,
        }
