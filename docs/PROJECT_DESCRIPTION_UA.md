# PDF Generation Service — опис проекту

## Що це таке

Самостійний HTTP-мікросервіс для генерації PDF-файлів з веб-сторінок (URL) або сирого HTML. Побудований на **FastAPI** (Python) + **Playwright** (headless Chromium). По суті це "PDF-принтер як сервіс" — приймає запит через REST API, відкриває реальний браузер, рендерить сторінку і повертає PDF.

## Ключові можливості

- **PDF з URL** — передаєш адресу будь-якої веб-сторінки, отримуєш PDF
- **PDF з HTML** — передаєш сирий HTML-код, отримуєш PDF
- **Batch-обробка** — до 100 URL за один запит, обробка у фоні з поллінгом статусу
- **CSS/JS-ін'єкція** — можна додати довільні стилі та скрипти перед рендерингом (приховати елементи, змінити шрифти, запустити JS)
- **Заміна зображень** — підмінити будь-яке зображення за CSS-селектором
- **Блокування запитів** — фільтрація реклами, трекерів, зайвих ресурсів (glob-патерни)
- **Cookies та заголовки** — передача авторизаційних cookies, кастомних HTTP-заголовків
- **Гнучкі PDF-опції** — формат (A4/A3/Letter), орієнтація, поля, масштаб, хедери/футери, фон
- **Пул браузерів** — 3 інстанси Chromium з round-robin вибором та автоматичною переробкою після 200 сторінок
- **Docker-ready** — один `docker compose up -d --build` і сервіс працює
- **Веб-інтерфейс** — вбудована тестова сторінка на `/`

---

## Плюси

### Архітектурні

- **Повний браузерний рендеринг** — Chromium рендерить сторінку як реальний браузер, включаючи JS-фреймворки (React, Vue, Angular), web fonts, flexbox/grid, CSS animations. Результат ідентичний тому, що бачить користувач
- **Ізоляція запитів** — кожен PDF рендериться в окремому BrowserContext, немає витоку стану між запитами
- **Пул браузерів з переробкою** — уникає memory leaks через автоматичний recycling після N сторінок
- **SSRF-захист** — валідація URL, блокування приватних IP та cloud metadata endpoints
- **Контроль розмірів** — ліміти на HTML (5MB), CSS (500KB), JS (100KB)

### Операційні

- **Простий деплой** — один Docker-контейнер, мінімум залежностей (4 пакети)
- **Горизонтальне масштабування** — декілька інстансів за nginx (шаблон є в docker-compose)
- **Health check** — ендпоінт `/api/health` з інформацією про пул та навантаження
- **Автоочищення** — batch-результати видаляються через 1 годину
- **Конфігурація через ENV** — все налаштовується змінними оточення

### Функціональні

- **CSS/JS-ін'єкція** — потужний інструмент для кастомізації без зміни вихідної сторінки
- **Batch API** — масова генерація з контролем паралелізму
- **Блокування запитів** — прибрати рекламу, поп-апи, чати, аналітику з PDF
- **Cookies** — працює з авторизованими сторінками

---

## Мінуси

### Ресурсоємність

- **Chromium жере пам'ять** — кожен інстанс ~150-300MB RAM, пул з 3 браузерів потребує мінімум 2GB. Це не легковагий wkhtmltopdf
- **CPU-інтенсивність** — рендеринг складних сторінок навантажує процесор. Ліміт 2 CPU в docker-compose не просто так
- **Холодний старт** — запуск 3 браузерів при старті контейнера займає 5-10 секунд

### Обмеження архітектури

- **Single worker** — uvicorn працює з 1 воркером, бо browser pool тримає стан в пам'яті. Масштабування тільки через додаткові контейнери
- **In-memory стан** — batch tasks зберігаються в dict. При перезапуску контейнера всі задачі та файли втрачаються
- **Немає черги** — немає Redis/RabbitMQ. При великому навантаженні запити будуть чекати на семафорі (max 10 паралельних)
- **Немає автентифікації** — API повністю відкрите, потрібен зовнішній шар безпеки (nginx, API gateway)

### Відсутній функціонал

- **Немає тестів** — жодного unit/integration тесту
- **Немає retry-логіки** — якщо сторінка не завантажилась — одразу помилка
- **Немає webhook/callback** — batch тільки через поллінг, немає push-нотифікацій
- **Немає кешування** — однакові URL завжди генеруються заново
- **Немає persistent storage** — PDF зберігаються тимчасово в `/tmp`, без S3/MinIO
- **CORS `*`** — дозволено все, підходить для dev, але не для production

---

## Сценарії використання

### 1. Генерація рахунків/інвойсів
CMS або ERP передає HTML-шаблон рахунку через `/api/pdf/from-html`, отримує PDF. Ідеально для e-commerce.

### 2. Архівування веб-сторінок
Збереження стану сторінок у PDF через `/api/pdf/from-url`. Можна використовувати для compliance, юридичних доказів, архівів.

### 3. Масова генерація каталогів/прайсів
Batch API для генерації PDF з десятків/сотень сторінок каталогу. Кожна сторінка товару -> PDF.

### 4. Друк сторінок з кастомізацією
Сховати навігацію, футер, рекламу через CSS-ін'єкцію. Замінити зображення на high-res версії. Заблокувати трекери.

### 5. PDF-звіти з дашбордів
Зняти PDF з дашборду Grafana, Metabase, або кастомного фронтенду. Cookies для авторизації.

### 6. E-commerce: PDF-версії товарних карток
Генерація красивих PDF для офлайн-перегляду, відправки клієнтам, друку.

---

## Інтеграція з CMS

### WordPress

**Варіант: PHP-клас для HTTP-запитів**

```php
// У functions.php або у плагіні
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

**Сценарії для WordPress:**

- **Кнопка "Завантажити PDF"** на постах/сторінках — шорткод `[download_pdf]` генерує PDF поточної сторінки
- **WooCommerce інвойси** — генерація PDF-рахунків при оформленні замовлення через HTML-шаблон
- **PDF-версії статей** для підписників (membership плагін + PDF)
- **Масовий експорт** — batch API для генерації PDF всіх сторінок каталогу товарів
- **Print-friendly версії** — CSS-ін'єкція ховає меню, сайдбар, коментарі, рекламу

**Переваги над wp-плагінами (mPDF, DOMPDF):**

- Рендерить JS (WooCommerce блоки, Elementor, Gutenberg blocks з JS-логікою)
- Підтримує сучасний CSS (grid, flexbox, custom properties)
- Не залежить від PHP-бібліотек для рендерингу

---

### Drupal

**Варіант: Custom module з Guzzle**

```php
// src/Service/PdfService.php
class PdfService {
    public function generateFromNode(NodeInterface $node): string {
        $url = $node->toUrl()->setAbsolute()->toString();
        $response = $this->httpClient->post('http://pdf-service:8000/api/pdf/from-url', [
            'json' => [
                'url' => $url,
                'inject_css' => '.toolbar, .contextual, #block-local-tasks { display: none !important; }',
                'cookies' => [/* session cookies для доступу до неопублікованого контенту */],
                'pdf_options' => ['format' => 'A4'],
            ],
            'timeout' => 60,
        ]);
        return $response->getBody()->getContents();
    }
}
```

**Сценарії для Drupal:**

- **PDF-версії нод** — контролер `/node/{nid}/pdf` генерує PDF будь-якого контент-типу
- **Views PDF export** — batch генерація PDF для результатів View (списки товарів, звіти)
- **Commerce invoices** — PDF-інвойси при оформленні замовлень через Drupal Commerce
- **Print module альтернатива** — замість модуля Print (який погано підтримує сучасний CSS)
- **Webform submissions** — генерація PDF з результатів заповнення Webform
- **Друк з авторизацією** — cookies дозволяють рендерити непублічний контент (інтранет, restricted)

**Переваги над Drupal Print/Entity Print:**

- Реальний браузерний рендеринг vs обмежений HTML-to-PDF
- Працює з Paragraphs, Layout Builder, складними View modes
- Не потрібно дублювати верстку спеціально для PDF — рендериться те, що бачить користувач

---

### TYPO3

**Варіант: Middleware або Controller**

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

**Сценарії для TYPO3:**

- **PDF-версії сторінок** — TypoScript або Middleware додає `/pdf` роут до будь-якої сторінки
- **Batch-генерація сайтмапи у PDF** — масовий експорт всіх сторінок або розділу через batch API
- **Каталоги та прайс-листи** — генерація PDF з EXT:commerce або кастомних каталогів
- **Персоналізовані документи** — HTML-шаблон через Fluid -> `/api/pdf/from-html` (рахунки, сертифікати, квитанції)
- **Multi-language PDF** — генерація PDF для кожної мовної версії сторінки (TYPO3 sites configuration)
- **Scheduler task** — автоматична нічна генерація актуальних PDF-каталогів

**Переваги над TYPO3-рішеннями (tcpdf, mpdf extensions):**

- Працює з Fluid-шаблонами, які рендеряться у браузері (а не обмежений HTML для tcpdf)
- Підтримує TYPO3 content elements, gridelements, container
- Не потрібно дублювати верстку спеціально для PDF — рендериться те, що бачить користувач

---

## Загальні рекомендації для всіх CMS

- **Мережа** — тримати pdf-service у тій же Docker-мережі, що й CMS (внутрішній трафік, без публічного доступу)
- **Авторизація** — для закритих сторінок передавати session cookies через API
- **CSS-ін'єкція** — завжди ховати навігацію, футер, cookie-банери, чат-віджети
- **Блокування** — блокувати analytics, ads, social widgets (не потрібні в PDF, сповільнюють рендеринг)
- **Кешування** — реалізувати кеш на стороні CMS (зберігати PDF, віддавати повторно, інвалідувати при зміні контенту)
- **Таймаути** — для складних сторінок збільшувати timeout до 60-120с
- **Print CSS** — якщо сайт має `@media print` стилі, використовувати `emulate_media: "screen"` (за замовчуванням), щоб уникнути порожніх PDF

---

## Коли НЕ варто використовувати цей сервіс

- **Прості текстові документи** — якщо потрібен PDF з чистого тексту/таблиці, легші бібліотеки (wkhtmltopdf, tcpdf, mPDF) будуть ефективнішими
- **Дуже високе навантаження (1000+ PDF/хв)** — потребує значного горизонтального масштабування та зовнішньої черги
- **Серверне оточення без Docker** — Playwright + Chromium важко встановити на bare metal shared hosting
