# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PDF Generation Service — generates PDFs from URLs or raw HTML using FastAPI + Playwright (Chromium). Supports CSS/JS injection, image replacement, request blocking, and batch processing. Written in Ukrainian (comments, README).

## Commands

### Run with Docker (recommended)
```bash
docker compose up -d --build
```

### Run locally (without Docker)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium && playwright install-deps chromium
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Service runs on `http://localhost:8000`. No test suite exists.

## Architecture

Three Python modules, no sub-packages:

- **`app/main.py`** — FastAPI app, all Pydantic request/response models, endpoint handlers, batch background processing, and config (env vars). Models are defined inline (not in a separate schemas file). Batch task state is stored in an in-memory `tasks_store` dict.
- **`app/browser_pool.py`** — `BrowserPool` class managing a pool of Playwright Chromium instances with round-robin selection, async locking, and automatic recycling after `MAX_PAGES_PER_BROWSER` pages to prevent memory leaks.
- **`app/pdf_generator.py`** — `PDFGenerator` class with `from_url()` and `from_html()` methods. Handles page navigation, CSS/JS/image injection, and PDF rendering. Uses `TYPE_CHECKING` imports from `main.py` to avoid circular deps.

### Request flow
1. FastAPI endpoint acquires a `task_semaphore` slot
2. `PDFGenerator` calls `BrowserPool.acquire()` to get a Chromium instance (round-robin)
3. Creates a new `BrowserContext` + `Page` per request (isolated, closed in `finally`)
4. Applies injections (CSS → JS → images), then calls `page.pdf()`
5. For batch: background task processes items concurrently with its own semaphore, saves PDFs to `OUTPUT_DIR`, results polled via `/api/pdf/batch/{task_id}`

### Key env vars
| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_POOL_SIZE` | `3` | Chromium instances in pool |
| `MAX_CONCURRENT_TASKS` | `10` | Global concurrency limit |
| `MAX_PAGES_PER_BROWSER` | `200` | Pages before browser recycling |
| `OUTPUT_DIR` | `/tmp/pdf-output` | Batch output directory |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `MAX_HTML_SIZE` | `5000000` | Max HTML body size (bytes) |
| `MAX_CSS_SIZE` | `500000` | Max CSS injection size (bytes) |
| `MAX_JS_SIZE` | `100000` | Max JS injection size (bytes) |
| `USER_AGENT` | Chrome 131 UA | Default browser User-Agent (overridable per request) |

### Static files
`app/static/index.html` — single-file web UI for testing all endpoints. Served at `/static/` and root `/` redirects to it.

## API Endpoints
- `GET /api/health` — pool status and active task count
- `POST /api/pdf/from-url` — single PDF from URL (returns PDF bytes directly)
- `POST /api/pdf/from-html` — single PDF from raw HTML
- `POST /api/pdf/batch` — submit batch job, returns `task_id`
- `GET /api/pdf/batch/{task_id}` — poll batch status
- `GET /api/pdf/batch/{task_id}/file/{file_id}` — download individual PDF from batch

## Important Patterns

- **No separate schemas file** — all Pydantic models live in `main.py`. Keep them there.
- **Context-per-request** — each PDF generation creates and closes its own `BrowserContext`. Never reuse contexts across requests.
- **Circular import avoidance** — `pdf_generator.py` imports models from `main.py` only under `TYPE_CHECKING`.
- **Single worker** — Dockerfile runs uvicorn with `--workers 1` because the browser pool uses in-process async state. Do not increase workers without switching to shared state.
