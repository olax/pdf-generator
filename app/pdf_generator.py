"""
PDF Generator — основна логіка рендерингу сторінок у PDF
з підтримкою ін'єкції CSS, JS та заміни зображень.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from playwright.async_api import Page

if TYPE_CHECKING:
    from app.main import (
        PDFFromURLRequest,
        PDFFromHTMLRequest,
        ImageInjection,
        PDFOptions,
    )
    from app.browser_pool import BrowserPool

logger = logging.getLogger("pdf-service.generator")


async def _abort_route(route):
    """Async handler for blocking requests."""
    await route.abort()


class PDFGenerator:
    def __init__(self, pool: "BrowserPool"):
        self.pool = pool

    async def from_url(self, req: "PDFFromURLRequest") -> bytes:
        """Generate PDF from a URL."""
        from app.main import USER_AGENT

        async with asyncio.timeout(req.timeout):
            async with self.pool.acquire() as browser:
                ua = req.user_agent or USER_AGENT
                ctx_kwargs: dict = {
                    "viewport": {"width": req.viewport_width, "height": req.viewport_height},
                    "extra_http_headers": req.extra_http_headers or {},
                }
                if ua:
                    ctx_kwargs["user_agent"] = ua
                context = await browser.new_context(**ctx_kwargs)

                try:
                    page = await context.new_page()

                    # Set cookies if provided
                    if req.cookies:
                        await context.add_cookies([c.model_dump(exclude_none=True) for c in req.cookies])

                    # Block unwanted requests (ads, trackers)
                    if req.block_requests:
                        for rule in req.block_requests:
                            await page.route(rule.pattern, _abort_route)

                    # Emulate media type (screen/print)
                    if req.emulate_media:
                        await page.emulate_media(media=req.emulate_media)

                    # Navigate
                    await page.goto(
                        req.url,
                        wait_until=req.wait_until,
                        timeout=req.timeout * 1000,
                    )

                    # Additional wait condition
                    if req.wait_for and req.wait_for.selector:
                        await page.wait_for_selector(
                            req.wait_for.selector,
                            timeout=req.wait_for.timeout,
                        )

                    # Inject custom content
                    await self._inject_all(
                        page, req.inject_css, req.inject_js, req.images
                    )

                    # Generate PDF
                    pdf_bytes = await page.pdf(**self._pdf_kwargs(req.pdf_options))
                    return pdf_bytes

                finally:
                    await context.close()

    async def from_html(self, req: "PDFFromHTMLRequest") -> bytes:
        """Generate PDF from raw HTML content."""
        from app.main import USER_AGENT

        async with asyncio.timeout(req.timeout):
            async with self.pool.acquire() as browser:
                ua = req.user_agent or USER_AGENT
                ctx_kwargs: dict = {
                    "viewport": {"width": req.viewport_width, "height": req.viewport_height},
                }
                if ua:
                    ctx_kwargs["user_agent"] = ua
                context = await browser.new_context(**ctx_kwargs)

                try:
                    page = await context.new_page()

                    # Set HTML content
                    if req.base_url:
                        await page.goto(req.base_url, wait_until="domcontentloaded", timeout=10000)
                        await page.set_content(req.html, wait_until="load", timeout=req.timeout * 1000)
                    else:
                        await page.set_content(req.html, wait_until="load", timeout=req.timeout * 1000)

                    # Additional wait condition
                    if req.wait_for and req.wait_for.selector:
                        await page.wait_for_selector(
                            req.wait_for.selector,
                            timeout=req.wait_for.timeout,
                        )

                    # Inject custom content
                    await self._inject_all(
                        page, req.inject_css, req.inject_js, req.images
                    )

                    pdf_bytes = await page.pdf(**self._pdf_kwargs(req.pdf_options))
                    return pdf_bytes

                finally:
                    await context.close()

    # -------------------------------------------------------------------
    # Injection helpers
    # -------------------------------------------------------------------

    async def _inject_all(
        self, page: Page, css: str | None, js: str | None, images: list | None
    ) -> None:
        """Inject CSS, JS, and images."""
        if css:
            await page.add_style_tag(content=css)
            logger.debug(f"Injected {len(css)} chars of CSS")
        if js:
            await page.evaluate(js)
            logger.debug(f"Executed {len(js)} chars of JS")
        if images:
            await self._inject_images(page, images)

    async def _inject_images(self, page: Page, images: list):
        """Replace or inject images by CSS selector (batched in a single JS call)."""
        # Build a single JS call for all images to avoid N IPC round-trips
        ops = []
        for img in images:
            op = "{" + f"s:{_js_string(img.selector)},u:{_js_string(img.src)}"
            if img.width:
                op += f",w:{_js_string(img.width)}"
            if img.height:
                op += f",h:{_js_string(img.height)}"
            op += "}"
            ops.append(op)

        js_code = """
        (() => {
            const ops = [""" + ",".join(ops) + """];
            const results = [];
            for (const op of ops) {
                const el = document.querySelector(op.s);
                if (!el) { results.push(false); continue; }
                if (el.tagName === 'IMG') {
                    el.src = op.u;
                } else {
                    el.style.backgroundImage = 'url(' + op.u + ')';
                    el.style.backgroundSize = 'cover';
                    el.style.backgroundRepeat = 'no-repeat';
                }
                if (op.w) el.style.width = op.w;
                if (op.h) el.style.height = op.h;
                results.push(true);
            }
            return results;
        })()
        """
        try:
            results = await page.evaluate(js_code)
            for i, found in enumerate(results):
                if not found:
                    logger.warning(f"Image selector not found: {images[i].selector}")
                else:
                    logger.debug(f"Injected image for selector: {images[i].selector}")
            # Brief delay for image layout reflow
            await page.wait_for_timeout(50)
        except Exception as e:
            logger.warning(f"Error injecting images: {e}")

    # -------------------------------------------------------------------
    # PDF options mapper
    # -------------------------------------------------------------------

    def _pdf_kwargs(self, opts: "PDFOptions") -> dict:
        """Convert PDFOptions model to Playwright pdf() kwargs."""
        kwargs = {
            "format": opts.format,
            "landscape": opts.landscape,
            "print_background": opts.print_background,
            "scale": opts.scale,
            "prefer_css_page_size": opts.prefer_css_page_size,
            "margin": {
                "top": opts.margin.top,
                "right": opts.margin.right,
                "bottom": opts.margin.bottom,
                "left": opts.margin.left,
            },
        }

        if opts.display_header_footer:
            kwargs["display_header_footer"] = True
            if opts.header_template:
                kwargs["header_template"] = opts.header_template
            if opts.footer_template:
                kwargs["footer_template"] = opts.footer_template

        return kwargs


def _js_string(value: str) -> str:
    """Safely escape a string for JavaScript injection."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("${", "\\${")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'
