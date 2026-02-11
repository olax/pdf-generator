# PDF Generation Service — Project Description

## What It Is

A standalone HTTP microservice for generating PDF files from web pages (URLs) or raw HTML. Built on **FastAPI** (Python) + **Playwright** (headless Chromium). Essentially a "PDF printer as a service" — it accepts a request via REST API, opens a real browser, renders the page, and returns a PDF.

## Key Features

- **PDF from URL** — pass any web page address, get a PDF back
- **PDF from HTML** — pass raw HTML code, get a PDF back
- **Batch processing** — up to 100 URLs per request, background processing with status polling
- **CSS/JS injection** — add custom styles and scripts before rendering (hide elements, change fonts, run JS)
- **Image replacement** — swap any image by CSS selector
- **Request blocking** — filter ads, trackers, unnecessary resources (glob patterns)
- **Cookies and headers** — pass authorization cookies, custom HTTP headers
- **Flexible PDF options** — format (A4/A3/Letter), orientation, margins, scale, headers/footers, background
- **Browser pool** — 3 Chromium instances with round-robin selection and automatic recycling after 200 pages
- **Docker-ready** — a single `docker compose up -d --build` and the service is running
- **Web UI** — built-in test page served at `/`

---

## Pros

### Architectural

- **Full browser rendering** — Chromium renders pages like a real browser, including JS frameworks (React, Vue, Angular), web fonts, flexbox/grid, CSS animations. The result is identical to what the user sees
- **Request isolation** — each PDF is rendered in a separate BrowserContext, no state leakage between requests
- **Browser pool with recycling** — prevents memory leaks through automatic recycling after N pages
- **SSRF protection** — URL validation, blocking of private IPs and cloud metadata endpoints
- **Size limits** — caps on HTML (5MB), CSS (500KB), JS (100KB)

### Operational

- **Simple deployment** — one Docker container, minimal dependencies (4 packages)
- **Horizontal scaling** — multiple instances behind nginx (template included in docker-compose)
- **Health check** — `/api/health` endpoint with pool info and load metrics
- **Auto-cleanup** — batch results are deleted after 1 hour
- **ENV-based configuration** — everything is configurable via environment variables

### Functional

- **CSS/JS injection** — powerful customization tool without modifying the source page
- **Batch API** — mass generation with concurrency control
- **Request blocking** — remove ads, pop-ups, chat widgets, analytics from PDFs
- **Cookies** — works with authenticated pages

---

## Cons

### Resource Consumption

- **Chromium is memory-hungry** — each instance uses ~150-300MB RAM, a pool of 3 browsers requires at least 2GB. This is not a lightweight wkhtmltopdf
- **CPU-intensive** — rendering complex pages is heavy on the processor. The 2 CPU limit in docker-compose exists for a reason
- **Cold start** — launching 3 browsers at container startup takes 5-10 seconds

### Architectural Limitations

- **Single worker** — uvicorn runs with 1 worker because the browser pool holds in-memory state. Scaling is only possible through additional containers
- **In-memory state** — batch tasks are stored in a dict. On container restart, all tasks and files are lost
- **No queue** — no Redis/RabbitMQ. Under heavy load, requests will wait on the semaphore (max 10 concurrent)
- **No authentication** — the API is fully open, requires an external security layer (nginx, API gateway)

### Missing Functionality

- **No tests** — zero unit/integration tests
- **No retry logic** — if a page fails to load, it immediately returns an error
- **No webhook/callback** — batch only via polling, no push notifications
- **No caching** — identical URLs are always regenerated
- **No persistent storage** — PDFs are stored temporarily in `/tmp`, no S3/MinIO
- **CORS `*`** — everything is allowed, fine for dev but not for production

---

## Use Cases

### 1. Invoice/Receipt Generation
A CMS or ERP sends an HTML invoice template via `/api/pdf/from-html` and receives a PDF. Ideal for e-commerce.

### 2. Web Page Archiving
Saving page snapshots as PDFs via `/api/pdf/from-url`. Useful for compliance, legal evidence, archival purposes.

### 3. Mass Catalog/Price List Generation
Batch API for generating PDFs from dozens or hundreds of catalog pages. Each product page becomes a PDF.

### 4. Print-Friendly Pages with Customization
Hide navigation, footers, ads via CSS injection. Replace images with high-res versions. Block trackers.

### 5. PDF Reports from Dashboards
Capture PDFs from Grafana, Metabase, or custom frontend dashboards. Cookies handle authentication.

### 6. E-commerce: PDF Product Cards
Generate polished PDFs for offline viewing, sending to clients, or printing.

---

## CMS Integration

### WordPress

**Approach: PHP class using HTTP requests**

```php
// In functions.php or a custom plugin
function generate_pdf_from_post($post_id) {
    $url = get_permalink($post_id);
    $response = wp_remote_post('http://pdf-service:8000/api/pdf/from-url', [
        'headers' => ['Content-Type' => 'application/json'],
        'body' => json_encode([
            'url' => $url,
            'inject_css' => '#wpadminbar, .site-header, .site-footer, .sidebar { display: none !important; }',
            'block_requests' => [
                ['pattern' => '**/google-analytics.com/**'],
                ['pattern' => '**/facebook.net/**'],
            ],
            'pdf_options' => ['format' => 'A4', 'margin' => ['top' => '15mm', 'bottom' => '15mm']],
        ]),
        'timeout' => 60,
    ]);
    return wp_remote_retrieve_body($response); // PDF bytes
}
```

**WordPress scenarios:**

- **"Download PDF" button** on posts/pages — a `[download_pdf]` shortcode generates a PDF of the current page
- **WooCommerce invoices** — generate PDF receipts on order placement via HTML template
- **PDF versions of articles** for subscribers (membership plugin + PDF)
- **Bulk export** — batch API for generating PDFs of all product catalog pages
- **Print-friendly versions** — CSS injection hides the menu, sidebar, comments, ads

**Advantages over WP plugins (mPDF, DOMPDF):**

- Renders JS (WooCommerce blocks, Elementor, Gutenberg blocks with JS logic)
- Supports modern CSS (grid, flexbox, custom properties)
- No dependency on PHP rendering libraries

---

### Drupal

**Approach: Custom module with Guzzle**

```php
// src/Service/PdfService.php
class PdfService {
    public function generateFromNode(NodeInterface $node): string {
        $url = $node->toUrl()->setAbsolute()->toString();
        $response = $this->httpClient->post('http://pdf-service:8000/api/pdf/from-url', [
            'json' => [
                'url' => $url,
                'inject_css' => '.toolbar, .contextual, #block-local-tasks { display: none !important; }',
                'cookies' => [/* session cookies for accessing unpublished content */],
                'pdf_options' => ['format' => 'A4'],
            ],
            'timeout' => 60,
        ]);
        return $response->getBody()->getContents();
    }
}
```

**Drupal scenarios:**

- **PDF versions of nodes** — a controller at `/node/{nid}/pdf` generates a PDF for any content type
- **Views PDF export** — batch generation of PDFs for View results (product listings, reports)
- **Commerce invoices** — PDF invoices on order placement via Drupal Commerce
- **Print module alternative** — replaces the Print module (which has poor support for modern CSS)
- **Webform submissions** — generate PDFs from completed Webform submissions
- **Authenticated rendering** — cookies allow rendering non-public content (intranet, restricted access)

**Advantages over Drupal Print/Entity Print:**

- Real browser rendering vs limited HTML-to-PDF conversion
- Works with Paragraphs, Layout Builder, complex View modes
- No need to duplicate markup specifically for PDF — it renders what the user sees

---

### TYPO3

**Approach: Middleware or Controller**

```php
// Classes/Controller/PdfController.php
class PdfController extends ActionController {
    public function generateAction(int $pageUid): ResponseInterface {
        $uri = $this->uriBuilder->reset()
            ->setTargetPageUid($pageUid)
            ->setCreateAbsoluteUri(true)
            ->build();

        $response = (new \GuzzleHttp\Client())->post('http://pdf-service:8000/api/pdf/from-url', [
            'json' => [
                'url' => $uri,
                'inject_css' => '#header, #footer, .breadcrumb, #cookie-banner { display: none !important; }',
                'block_requests' => [['pattern' => '**/matomo.**']],
                'pdf_options' => [
                    'format' => 'A4',
                    'header_template' => '<div style="font-size:8px; text-align:center;">Company Name</div>',
                    'footer_template' => '<div style="font-size:8px; text-align:center;"><span class="pageNumber"></span>/<span class="totalPages"></span></div>',
                    'display_header_footer' => true,
                ],
            ],
            'timeout' => 60,
        ]);

        return $this->responseFactory->createResponse()
            ->withHeader('Content-Type', 'application/pdf')
            ->withBody($this->streamFactory->createStream($response->getBody()->getContents()));
    }
}
```

**TYPO3 scenarios:**

- **PDF versions of pages** — TypoScript or Middleware adds a `/pdf` route to any page
- **Batch sitemap-to-PDF** — bulk export of all pages or a section via the batch API
- **Catalogs and price lists** — PDF generation from EXT:commerce or custom catalogs
- **Personalized documents** — HTML template via Fluid -> `/api/pdf/from-html` (invoices, certificates, receipts)
- **Multi-language PDFs** — generate PDFs for each language version of a page (TYPO3 sites configuration)
- **Scheduler task** — automated nightly generation of up-to-date PDF catalogs

**Advantages over TYPO3 solutions (tcpdf, mpdf extensions):**

- Works with Fluid templates that are rendered in the browser (not the limited HTML subset for tcpdf)
- Supports TYPO3 content elements, gridelements, container
- No need to duplicate markup specifically for PDF — it renders what the user sees

---

## General Recommendations for All CMS Platforms

- **Networking** — keep pdf-service in the same Docker network as the CMS (internal traffic, no public exposure)
- **Authorization** — for restricted pages, pass session cookies via the API
- **CSS injection** — always hide navigation, footers, cookie banners, chat widgets
- **Request blocking** — block analytics, ads, social widgets (they are unnecessary in PDFs and slow down rendering)
- **Caching** — implement caching on the CMS side (store PDFs, serve them again, invalidate on content changes)
- **Timeouts** — increase timeout to 60-120s for complex pages
- **Print CSS** — if the site has `@media print` styles, use `emulate_media: "screen"` (the default) to avoid empty PDFs

---

## When NOT to Use This Service

- **Simple text documents** — if you need a PDF from plain text or a table, lighter libraries (wkhtmltopdf, tcpdf, mPDF) will be more efficient
- **Very high load (1000+ PDFs/min)** — requires significant horizontal scaling and an external queue
- **Server environments without Docker** — Playwright + Chromium is difficult to install on bare metal shared hosting
