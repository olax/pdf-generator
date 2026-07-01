# 📄 PDF Generation Service

Сервіс для масової генерації PDF з довільних URL або HTML-контенту.  
Підтримує ін'єкцію кастомних CSS, JS та зображень перед рендерингом,
**кешування результатів**, **API-key автентифікацію** та **allowlist доменів**.

**Stack:** FastAPI + Playwright (Chromium) + Docker

**Можливості:**
- Генерація PDF з URL, сирого HTML або пакетно (batch з polling)
- Ін'єкція CSS/JS, заміна зображень, блокування трекерів, cookies/headers
- **Кеш результатів** — однакові запити віддаються з диска без повторного рендеру
- **Безпека** — API-ключ на `/api/pdf/*`, SSRF-захист, allowlist доменів
- Пул браузерів з рециклінгом + ліміти конкурентності

---

## Швидкий старт

### Docker (рекомендовано)

```bash
docker compose up -d --build
```

Сервіс буде доступний на `http://localhost:8000`

### Без Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## API Endpoints

### `GET /api/health`

Перевірка стану сервісу.

```bash
curl http://localhost:8000/api/health
```

```json
{
  "status": "ok",
  "browsers": {"pool_size": 3, "active": 3, "pages_served": [12, 8, 15]},
  "active_tasks": 0,
  "cache": {"enabled": true, "entries": 42, "size_bytes": 8500000, "hits": 310, "misses": 44, "hit_rate": 0.876, "ttl_seconds": 86400, "max_bytes": 2000000000}
}
```

> `/api/health` та веб-UI відкриті. Усі `/api/pdf/*` вимагають API-ключ, якщо задано `PDF_API_KEYS` — див. [Безпека](#безпека-автентифікація--allowlist).

---

### `POST /api/pdf/from-url`

Генерація PDF зі сторінки сайту. Повертає PDF-файл напряму.

#### Мінімальний запит

```bash
curl -X POST http://localhost:8000/api/pdf/from-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}' \
  -o example.pdf
```

#### Повний запит з усіма опціями

```bash
curl -X POST http://localhost:8000/api/pdf/from-url \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/article",
    "inject_css": "body { font-family: Arial, sans-serif !important; } .ads, .cookie-banner, .popup { display: none !important; } @media print { nav, footer { display: none; } }",
    "inject_js": "document.querySelectorAll(\".modal-overlay\").forEach(e => e.remove()); window.scrollTo(0, document.body.scrollHeight);",
    "images": [
      {
        "selector": "#company-logo",
        "src": "https://my-cdn.com/logo.png",
        "width": "200px"
      },
      {
        "selector": ".watermark",
        "src": "data:image/png;base64,iVBORw0KGgo...",
        "width": "100%",
        "height": "100%"
      }
    ],
    "block_requests": [
      {"pattern": "**/google-analytics.com/**"},
      {"pattern": "**/doubleclick.net/**"},
      {"pattern": "**/facebook.com/tr/**"},
      {"pattern": "**/*.gif"}
    ],
    "pdf_options": {
      "format": "A4",
      "landscape": false,
      "print_background": true,
      "margin": {"top": "15mm", "right": "10mm", "bottom": "15mm", "left": "10mm"},
      "scale": 1.0,
      "display_header_footer": true,
      "header_template": "<div style=\"font-size:8px; text-align:center; width:100%;\">My Company Report</div>",
      "footer_template": "<div style=\"font-size:8px; text-align:center; width:100%;\">Page <span class=\"pageNumber\"></span> / <span class=\"totalPages\"></span></div>"
    },
    "wait_until": "networkidle",
    "wait_for": {"selector": ".content-loaded", "timeout": 5000},
    "timeout": 30,
    "viewport_width": 1280,
    "viewport_height": 720,
    "emulate_media": "print",
    "extra_http_headers": {"Accept-Language": "uk-UA"},
    "cookies": [
      {"name": "session", "value": "abc123", "domain": "example.com", "path": "/"}
    ]
  }' \
  -o report.pdf
```

---

### `POST /api/pdf/from-html`

Генерація PDF з сирого HTML.

```bash
curl -X POST http://localhost:8000/api/pdf/from-html \
  -H "Content-Type: application/json" \
  -d '{
    "html": "<!DOCTYPE html><html><head><style>body{font-family:sans-serif}</style></head><body><h1>Invoice #1234</h1><p>Total: $500</p></body></html>",
    "inject_css": "h1 { color: #2563eb; }",
    "pdf_options": {
      "format": "A4",
      "margin": {"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"}
    }
  }' \
  -o invoice.pdf
```

---

### `POST /api/pdf/batch`

Пакетна генерація PDF. Повертає `task_id` для відстеження.

```bash
# Відправити пакет
curl -X POST http://localhost:8000/api/pdf/batch \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {"url": "https://example.com/page1", "inject_css": ".ads { display: none; }"},
      {"url": "https://example.com/page2"},
      {"url": "https://example.com/page3", "timeout": 60}
    ],
    "concurrent": 3
  }'

# Відповідь:
# {"task_id": "550e8400-...", "status": "pending", "total": 3, ...}
```

#### Перевірити статус

```bash
curl http://localhost:8000/api/pdf/batch/550e8400-...
```

```json
{
  "task_id": "550e8400-...",
  "status": "done",
  "progress": 3,
  "total": 3,
  "results": [
    {"url": "https://example.com/page1", "status": "ok", "file_id": "abc-123", "duration_ms": 2100},
    {"url": "https://example.com/page2", "status": "ok", "file_id": "def-456", "duration_ms": 1800},
    {"url": "https://example.com/page3", "status": "error", "error": "Timeout", "duration_ms": 60000}
  ]
}
```

#### Завантажити файл з пакету

```bash
curl http://localhost:8000/api/pdf/batch/550e8400-.../file/abc-123 -o page1.pdf
```

---

## Приклади використання (Python)

### Простий клієнт

```python
import httpx

PDF_SERVICE = "http://localhost:8000"

# Одиночна генерація
resp = httpx.post(f"{PDF_SERVICE}/api/pdf/from-url", json={
    "url": "https://news.ycombinator.com",
    "inject_css": ".pagetop { background: #2563eb !important; }",
    "block_requests": [
        {"pattern": "**/google-analytics.com/**"}
    ],
}, timeout=60)

with open("hackernews.pdf", "wb") as f:
    f.write(resp.content)
```

### Пакетна генерація з polling

```python
import httpx
import time

PDF_SERVICE = "http://localhost:8000"
client = httpx.Client(timeout=120)

# Запустити пакет
resp = client.post(f"{PDF_SERVICE}/api/pdf/batch", json={
    "items": [
        {"url": f"https://example.com/page/{i}"} for i in range(50)
    ],
    "concurrent": 5,
})
task_id = resp.json()["task_id"]

# Polling
while True:
    status = client.get(f"{PDF_SERVICE}/api/pdf/batch/{task_id}").json()
    print(f"Progress: {status['progress']}/{status['total']}")
    if status["status"] in ("done", "error"):
        break
    time.sleep(2)

# Завантажити результати
for result in status["results"]:
    if result["status"] == "ok":
        pdf = client.get(
            f"{PDF_SERVICE}/api/pdf/batch/{task_id}/file/{result['file_id']}"
        )
        with open(result["filename"], "wb") as f:
            f.write(pdf.content)
```

---

## Конфігурація

| Змінна | За замовчуванням | Опис |
|--------|-----------------|------|
| `BROWSER_POOL_SIZE` | `3` | Кількість Chromium інстансів у пулі |
| `MAX_CONCURRENT_TASKS` | `10` | Ліміт одночасних завдань |
| `MAX_PAGES_PER_BROWSER` | `200` | Сторінок до рециклінгу браузера (проти memory leak) |
| `LOG_LEVEL` | `INFO` | Рівень логування |
| `OUTPUT_DIR` | `/tmp/pdf-output` | Директорія для batch-файлів |
| `CACHE_ENABLED` | `true` | Увімкнути файловий кеш PDF |
| `CACHE_DIR` | `/var/cache/pdf` | Директорія кешу |
| `CACHE_TTL_SECONDS` | `86400` | TTL запису кешу (`0` = без протермінування) |
| `CACHE_MAX_BYTES` | `2000000000` | Ліміт розміру кешу (LRU-евікшн) |
| `PDF_API_KEYS` / `PDF_API_KEY` | *(порожньо)* | API-ключі для `/api/pdf/*` (через кому); порожньо = auth вимкнено |
| `ALLOWED_HOSTS` | *(порожньо)* | Allowlist доменів (через кому); префікс `.` = суфікс-матч; порожньо = будь-який публічний хост |
| `CORS_ORIGINS` | `*` | CORS origins (через кому) |

Приклад — див. [`.env.example`](.env.example).

### Рекомендації по ресурсах

| Сценарій | POOL_SIZE | RAM | CPU |
|----------|-----------|-----|-----|
| Легке навантаження (<100 PDF/год) | 2 | 1 GB | 1 |
| Середнє (100-500 PDF/год) | 3 | 2 GB | 2 |
| Важке (500+ PDF/год) | 5 | 4 GB | 4 |

---

## Swagger / OpenAPI

Автогенерована документація доступна за адресами:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

---

## Web UI (тестова панель)

Після запуску відкрий http://localhost:8000 — автоматичний редірект на веб-інтерфейс.

Можливості панелі:
- **From URL** — генерація PDF з URL з усіма опціями (CSS/JS ін'єкція, блокування запитів, зображення, cookies, headers)
- **From HTML** — генерація з сирого HTML
- **Batch** — пакетна генерація з polling прогресу та завантаженням результатів
- **Raw JSON** — прямий запит до API з довільним JSON payload
- Індикатор стану сервісу (healthcheck)
- Preview JSON — перегляд згенерованого payload перед відправкою

---

## Кешування

Згенеровані PDF кешуються на диску. Ключ — це sha256 усіх полів запиту, що
впливають на результат (URL/HTML, `pdf_options`, ін'єкції CSS/JS, зображення,
cookies, headers, viewport, wait/media, User-Agent) — **окрім** `timeout` та
`no_cache`, які на байти не впливають.

- Перевірка кешу відбувається **до** семафора конкурентності → хіти віддаються
  миттєво й не обмежені `MAX_CONCURRENT_TASKS`.
- Відповідь містить заголовок **`X-Cache: HIT | MISS | BYPASS`**.
- Примусовий свіжий рендер — поле `"no_cache": true` у запиті.
- Евікшн: за TTL (`CACHE_TTL_SECONDS`) і за розміром (`CACHE_MAX_BYTES`, LRU).

Це головний захист від навантаження, коли краулери масово тягнуть один і той
самий PDF-endpoint: `N` однакових запитів = `1` рендер + `N−1` читань з диска.

```bash
# перший раз -> MISS (рендериться), другий -> HIT (з кешу)
curl -si -X POST http://localhost:8000/api/pdf/from-url \
  -H "X-API-Key: <key>" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}' | grep -i x-cache
```

---

## Безпека (автентифікація + allowlist)

**API-ключ.** Усі `/api/pdf/*` вимагають ключ, коли задано `PDF_API_KEYS`
(або `PDF_API_KEY`). Якщо не задано — auth **вимкнено** з попередженням у логах
(тоді не виставляй сервіс у ненадійну мережу). `/api/health` і UI лишаються відкриті.

```bash
curl -X POST http://localhost:8000/api/pdf/from-url \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}' -o out.pdf
# або: -H "Authorization: Bearer <key>"
```

**SSRF-захист** (завжди активний): дозволені лише `http(s)`, блокуються
cloud-metadata та приватні/loopback/link-local/reserved IP.

**Allowlist доменів** (опційно, `ALLOWED_HOSTS`): обмежує, які хости можна
рендерити. Запис із префіксом `.` — суфікс-матч (`.bayonne.fr` → `bayonne.fr`
і будь-який сабдомен). Порожньо = будь-який публічний хост.

---

## Тести

Браузер замоканий, тож тести не потребують Chromium:

```bash
pip install -r requirements-dev.txt
pytest
```

Покриття: кеш (miss/hit/TTL/евікшн), auth (401/Bearer), allowlist, SSRF,
заголовки `X-Cache`, `no_cache` bypass, health.

---

## Ліцензія

MIT
