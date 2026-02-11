"""
PDF Generation Service — FastAPI + Playwright
Сервіс для масової генерації PDF з довільних URL або HTML
з підтримкою ін'єкції кастомних CSS, JS та зображень.
"""

import asyncio
import hashlib
import ipaddress
import logging
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from app.browser_pool import BrowserPool
from app.pdf_generator import PDFGenerator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pdf-service")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POOL_SIZE = int(os.getenv("BROWSER_POOL_SIZE", "3"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_TASKS", "10"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/tmp/pdf-output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TASK_TTL_SECONDS = 3600  # cleanup completed tasks after 1 hour

# Body size limits (bytes)
MAX_HTML_SIZE = int(os.getenv("MAX_HTML_SIZE", str(5_000_000)))       # 5 MB
MAX_CSS_SIZE = int(os.getenv("MAX_CSS_SIZE", str(500_000)))           # 500 KB
MAX_JS_SIZE = int(os.getenv("MAX_JS_SIZE", str(100_000)))             # 100 KB

# Browser User-Agent
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
USER_AGENT = os.getenv("USER_AGENT", DEFAULT_USER_AGENT)

# ---------------------------------------------------------------------------
# URL validation (SSRF protection)
# ---------------------------------------------------------------------------
BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


def _validate_safe_url(url: str) -> str:
    """Block SSRF: only http(s), no private IPs, no cloud metadata."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs allowed, got {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must contain a hostname")
    if hostname in BLOCKED_HOSTS:
        raise ValueError("Access to metadata endpoints is blocked")
    # Resolve hostname to IP and check for private ranges
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"URLs pointing to private/internal networks are blocked")
    except socket.gaierror:
        pass  # DNS resolution failed — Playwright will handle the error
    return url


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PDFMargins(BaseModel):
    top: str = "10mm"
    right: str = "10mm"
    bottom: str = "10mm"
    left: str = "10mm"


class PDFOptions(BaseModel):
    format: str = Field(default="A4", description="Page format: A4, A3, Letter, Legal, Tabloid")
    landscape: bool = False
    print_background: bool = True
    margin: PDFMargins = Field(default_factory=PDFMargins)
    scale: float = Field(default=1.0, ge=0.1, le=2.0)
    prefer_css_page_size: bool = False
    header_template: Optional[str] = None
    footer_template: Optional[str] = None
    display_header_footer: bool = False


class ImageInjection(BaseModel):
    selector: str = Field(..., description="CSS selector of the element to replace")
    src: str = Field(..., description="Image URL or data:image/... base64 string")
    width: Optional[str] = None
    height: Optional[str] = None


class BlockRule(BaseModel):
    pattern: str = Field(..., description="URL pattern to block (glob), e.g. **/google-analytics.com/**")


class WaitCondition(BaseModel):
    selector: Optional[str] = Field(None, description="Wait for CSS selector to appear")
    timeout: int = Field(default=5000, description="Timeout in ms for the wait condition")


class CookieParam(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    value: str = Field(..., max_length=4096)
    domain: Optional[str] = None
    url: Optional[str] = None
    path: str = "/"
    expires: Optional[float] = None
    httpOnly: bool = False
    secure: bool = False
    sameSite: Optional[Literal["Strict", "Lax", "None"]] = None


WaitUntilState = Literal["domcontentloaded", "load", "networkidle", "commit"]


class _BodySizeMixin:
    """Shared body-size validators reused across request models."""

    @field_validator("inject_css")
    @classmethod
    def check_css_size(cls, v: str | None) -> str | None:
        if v is not None and len(v) > MAX_CSS_SIZE:
            raise ValueError(f"CSS exceeds {MAX_CSS_SIZE} bytes limit")
        return v

    @field_validator("inject_js")
    @classmethod
    def check_js_size(cls, v: str | None) -> str | None:
        if v is not None and len(v) > MAX_JS_SIZE:
            raise ValueError(f"JS exceeds {MAX_JS_SIZE} bytes limit")
        return v


class PDFFromURLRequest(_BodySizeMixin, BaseModel):
    url: str = Field(..., description="URL of the page to render")
    inject_css: Optional[str] = Field(None, description="Custom CSS to inject")
    inject_js: Optional[str] = Field(None, description="Custom JS to execute after page load")
    images: Optional[list[ImageInjection]] = Field(None, description="Images to inject/replace")
    block_requests: Optional[list[BlockRule]] = Field(None, description="URL patterns to block (ads, trackers)")
    pdf_options: PDFOptions = Field(default_factory=PDFOptions)
    wait_until: WaitUntilState = Field(default="networkidle", description="Load state: domcontentloaded, load, networkidle")
    wait_for: Optional[WaitCondition] = Field(None, description="Additional wait condition after load")
    timeout: int = Field(default=30, ge=5, le=120, description="Total timeout in seconds")
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=720, ge=240, le=2160)
    emulate_media: Optional[Literal["screen", "print"]] = Field("screen", description="Emulate media type: screen or print (default: screen to avoid empty PDFs from print-hiding CSS)")
    extra_http_headers: Optional[dict[str, str]] = Field(None, description="Extra HTTP headers")
    cookies: Optional[list[CookieParam]] = Field(None, description="Cookies to set before navigation")
    user_agent: Optional[str] = Field(None, description="Custom User-Agent (overrides env USER_AGENT)")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_safe_url(v)


class PDFFromHTMLRequest(_BodySizeMixin, BaseModel):
    html: str = Field(..., description="Raw HTML content to render")
    inject_css: Optional[str] = None
    inject_js: Optional[str] = None
    images: Optional[list[ImageInjection]] = None
    pdf_options: PDFOptions = Field(default_factory=PDFOptions)
    wait_for: Optional[WaitCondition] = None
    timeout: int = Field(default=30, ge=5, le=120)
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=720, ge=240, le=2160)
    base_url: Optional[str] = Field(None, description="Base URL for relative resources in HTML")
    user_agent: Optional[str] = Field(None, description="Custom User-Agent (overrides env USER_AGENT)")

    @field_validator("html")
    @classmethod
    def validate_html(cls, v: str) -> str:
        if len(v) > MAX_HTML_SIZE:
            raise ValueError(f"HTML exceeds {MAX_HTML_SIZE} bytes limit")
        return v

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_safe_url(v)
        return v


class BatchURLItem(_BodySizeMixin, BaseModel):
    url: str
    inject_css: Optional[str] = None
    inject_js: Optional[str] = None
    images: Optional[list[ImageInjection]] = None
    pdf_options: PDFOptions = Field(default_factory=PDFOptions)
    wait_until: WaitUntilState = "networkidle"
    timeout: int = 30
    filename: Optional[str] = None
    user_agent: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        return _validate_safe_url(v)


class BatchRequest(BaseModel):
    items: list[BatchURLItem] = Field(..., min_length=1, max_length=100)
    concurrent: int = Field(default=5, ge=1, le=20)


class BatchResultItem(BaseModel):
    url: str
    status: str  # "ok" | "error"
    filename: Optional[str] = None
    file_id: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0


class TaskStatus(BaseModel):
    task_id: str
    status: str  # "pending" | "processing" | "done" | "error"
    progress: int = 0
    total: int = 0
    results: Optional[list[BatchResultItem]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
browser_pool: BrowserPool
pdf_generator: PDFGenerator
task_semaphore: asyncio.Semaphore
tasks_store: dict[str, TaskStatus] = {}
_task_timestamps: dict[str, float] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global browser_pool, pdf_generator, task_semaphore
    logger.info(f"Starting browser pool (size={POOL_SIZE})...")
    browser_pool = BrowserPool(pool_size=POOL_SIZE)
    await browser_pool.start()
    pdf_generator = PDFGenerator(browser_pool)
    task_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    logger.info("PDF Service ready.")
    yield
    cleanup_task.cancel()
    logger.info("Shutting down browser pool...")
    await browser_pool.stop()


app = FastAPI(
    title="PDF Generation Service",
    description="Масова генерація PDF з URL/HTML з підтримкою кастомних CSS, JS та зображень",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — дозволяємо запити з веб-інтерфейсу
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files — веб-інтерфейс для тестування
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    pool_status = browser_pool.status()
    return {
        "status": "ok",
        "browsers": pool_status,
        "active_tasks": len([t for t in tasks_store.values() if t.status == "processing"]),
    }


@app.get("/")
async def root():
    """Serve web UI directly at root."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.post("/api/pdf/from-url")
async def pdf_from_url(req: PDFFromURLRequest):
    """Generate PDF from a URL with optional CSS/JS/image injection."""
    async with task_semaphore:
        try:
            t0 = time.monotonic()
            pdf_bytes = await pdf_generator.from_url(req)
            duration = int((time.monotonic() - t0) * 1000)
            logger.info(f"PDF generated from {req.url} in {duration}ms ({len(pdf_bytes)} bytes)")

            filename = _url_to_filename(req.url)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "X-Generation-Time-Ms": str(duration),
                },
            )
        except (asyncio.TimeoutError, TimeoutError):
            raise HTTPException(status_code=504, detail=f"Timeout after {req.timeout}s")
        except Exception as e:
            logger.exception(f"Error generating PDF from {req.url}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/pdf/from-html")
async def pdf_from_html(req: PDFFromHTMLRequest):
    """Generate PDF from raw HTML with optional CSS/JS/image injection."""
    async with task_semaphore:
        try:
            t0 = time.monotonic()
            pdf_bytes = await pdf_generator.from_html(req)
            duration = int((time.monotonic() - t0) * 1000)
            logger.info(f"PDF generated from HTML in {duration}ms ({len(pdf_bytes)} bytes)")

            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": 'attachment; filename="document.pdf"',
                    "X-Generation-Time-Ms": str(duration),
                },
            )
        except (asyncio.TimeoutError, TimeoutError):
            raise HTTPException(status_code=504, detail=f"Timeout after {req.timeout}s")
        except Exception as e:
            logger.exception("Error generating PDF from HTML")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/pdf/batch", response_model=TaskStatus)
async def pdf_batch(req: BatchRequest, background_tasks: BackgroundTasks):
    """Submit a batch of URLs for PDF generation. Returns a task ID for polling."""
    task_id = str(uuid.uuid4())
    task = TaskStatus(
        task_id=task_id,
        status="pending",
        total=len(req.items),
    )
    tasks_store[task_id] = task
    _task_timestamps[task_id] = time.time()
    background_tasks.add_task(_process_batch, task_id, req)
    return task


@app.get("/api/pdf/batch/{task_id}", response_model=TaskStatus)
async def batch_status(task_id: str):
    """Check the status of a batch task."""
    task = tasks_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/pdf/batch/{task_id}/file/{file_id}")
async def batch_download(task_id: str, file_id: str):
    """Download a single PDF from a completed batch."""
    # Validate file_id is a UUID (prevents path traversal)
    try:
        uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    task = tasks_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Verify file_id belongs to this task
    valid_ids = {r.file_id for r in (task.results or []) if r.file_id}
    if file_id not in valid_ids:
        raise HTTPException(status_code=404, detail="File not found")

    filepath = OUTPUT_DIR / f"{file_id}.pdf"
    if not filepath.resolve().is_relative_to(OUTPUT_DIR.resolve()):
        raise HTTPException(status_code=403, detail="Access denied")
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        filepath,
        media_type="application/pdf",
        filename=f"{file_id}.pdf",
    )


# ---------------------------------------------------------------------------
# Background batch processing
# ---------------------------------------------------------------------------

async def _process_batch(task_id: str, req: BatchRequest):
    task = tasks_store[task_id]
    task.status = "processing"
    task.results = []
    batch_sem = asyncio.Semaphore(req.concurrent)

    try:
        async def process_one(item: BatchURLItem) -> BatchResultItem:
            async with batch_sem:
                async with task_semaphore:
                    t0 = time.monotonic()
                    try:
                        url_req = PDFFromURLRequest(
                            url=item.url,
                            inject_css=item.inject_css,
                            inject_js=item.inject_js,
                            images=item.images,
                            pdf_options=item.pdf_options,
                            wait_until=item.wait_until,
                            timeout=item.timeout,
                            user_agent=item.user_agent,
                        )
                        pdf_bytes = await pdf_generator.from_url(url_req)
                        file_id = str(uuid.uuid4())
                        filepath = OUTPUT_DIR / f"{file_id}.pdf"
                        await asyncio.to_thread(filepath.write_bytes, pdf_bytes)
                        duration = int((time.monotonic() - t0) * 1000)

                        return BatchResultItem(
                            url=item.url,
                            status="ok",
                            filename=item.filename or _url_to_filename(item.url),
                            file_id=file_id,
                            duration_ms=duration,
                        )
                    except Exception as e:
                        duration = int((time.monotonic() - t0) * 1000)
                        return BatchResultItem(
                            url=item.url,
                            status="error",
                            error=str(e),
                            duration_ms=duration,
                        )
                    finally:
                        task.progress += 1

        results = await asyncio.gather(*[process_one(item) for item in req.items])

        task.results = list(results)
        task.status = "done"
        logger.info(
            f"Batch {task_id} done: "
            f"{sum(1 for r in results if r.status == 'ok')}/{len(results)} OK"
        )
    except Exception as e:
        logger.exception(f"Batch {task_id} failed")
        task.status = "error"
        task.error = str(e)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

async def _cleanup_old_tasks():
    """Remove completed tasks and their files after TTL."""
    now = time.time()
    to_remove = []
    for task_id, ts in _task_timestamps.items():
        task = tasks_store.get(task_id)
        if task and task.status in ("done", "error") and now - ts > TASK_TTL_SECONDS:
            to_remove.append(task_id)
    for task_id in to_remove:
        task = tasks_store.pop(task_id, None)
        _task_timestamps.pop(task_id, None)
        if task and task.results:
            for result in task.results:
                if result.file_id:
                    filepath = OUTPUT_DIR / f"{result.file_id}.pdf"
                    await asyncio.to_thread(filepath.unlink, True)
    if to_remove:
        logger.info(f"Cleaned up {len(to_remove)} old batch tasks")


async def _periodic_cleanup():
    """Periodically clean up old tasks and files."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        await _cleanup_old_tasks()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_to_filename(url: str) -> str:
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    parsed = urlparse(url)
    slug = parsed.netloc.replace(".", "_")
    path_slug = parsed.path.strip("/").replace("/", "_")[:40]
    if path_slug:
        slug += f"_{path_slug}"
    return f"{slug}_{h}.pdf"
