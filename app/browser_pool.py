"""
Browser Pool — керує пулом Playwright Chromium інстансів.
Кожен браузер перевикористовується для багатьох запитів,
що прибирає overhead на холодний старт.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import async_playwright, Browser, Playwright

logger = logging.getLogger("pdf-service.pool")

CHROMIUM_ARGS = [
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-translate",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--safebrowsing-disable-auto-update",
    "--hide-scrollbars",
]

# Max pages per browser before recycling (prevents memory leaks)
MAX_PAGES_PER_BROWSER = int(os.getenv("MAX_PAGES_PER_BROWSER", "200"))


@dataclass
class BrowserInstance:
    browser: Browser
    pages_served: int = 0
    is_recycling: bool = False
    active_count: int = 0
    _pending_close: bool = False


class BrowserPool:
    def __init__(self, pool_size: int = 3):
        self.pool_size = pool_size
        self._playwright: Playwright | None = None
        self._instances: list[BrowserInstance] = []
        self._lock = asyncio.Lock()

    async def start(self):
        """Launch the browser pool."""
        self._playwright = await async_playwright().start()
        for i in range(self.pool_size):
            instance = await self._create_instance()
            self._instances.append(instance)
            logger.info(f"Browser instance {i+1}/{self.pool_size} launched")

    async def stop(self):
        """Gracefully shut down all browsers."""
        for inst in self._instances:
            try:
                await inst.browser.close()
            except Exception:
                pass
        if self._playwright:
            await self._playwright.stop()
        self._instances.clear()
        logger.info("Browser pool stopped")

    @asynccontextmanager
    async def acquire(self):
        """
        Get a browser instance from the pool (least-loaded selection).
        Prefers instances that are not recycling and have the fewest active contexts.
        Automatically recycles browsers that have served too many pages.

        Usage: async with pool.acquire() as browser: ...
        """
        async with self._lock:
            if not self._instances:
                raise RuntimeError("Browser pool is not running")

            # Prefer non-recycling instances, pick least loaded
            available = [i for i in self._instances if not i.is_recycling]
            if not available:
                available = self._instances  # fallback: all recycling
            instance = min(available, key=lambda i: i.active_count)
            instance.pages_served += 1
            instance.active_count += 1

            # Schedule recycling if needed
            if (
                instance.pages_served >= MAX_PAGES_PER_BROWSER
                and not instance.is_recycling
            ):
                instance.is_recycling = True
                asyncio.create_task(self._recycle(instance))

        try:
            yield instance.browser
        finally:
            should_close = False
            async with self._lock:
                instance.active_count -= 1
                if instance._pending_close and instance.active_count == 0:
                    should_close = True
            if should_close:
                try:
                    await instance.browser.close()
                    logger.info("Closed recycled browser after last context released")
                except Exception:
                    pass

    async def _create_instance(self) -> BrowserInstance:
        browser = await self._playwright.chromium.launch(
            headless=True,
            args=CHROMIUM_ARGS,
        )
        return BrowserInstance(browser=browser)

    async def _recycle(self, old_instance: BrowserInstance):
        """Replace an old browser instance with a fresh one."""
        logger.info(
            f"Recycling browser after {old_instance.pages_served} pages"
        )
        try:
            new_instance = await self._create_instance()
            should_close = False
            async with self._lock:
                idx = self._instances.index(old_instance)
                self._instances[idx] = new_instance
                if old_instance.active_count == 0:
                    should_close = True
                else:
                    old_instance._pending_close = True
            if should_close:
                await old_instance.browser.close()
            logger.info("Browser recycled successfully")
        except Exception as e:
            logger.error(f"Error recycling browser: {e}")
            old_instance.is_recycling = False

    def status(self) -> dict:
        return {
            "pool_size": self.pool_size,
            "active": len(self._instances),
            "pages_served": [inst.pages_served for inst in self._instances],
            "max_pages_per_browser": MAX_PAGES_PER_BROWSER,
        }
